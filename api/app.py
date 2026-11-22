"""API regijskega centra (specifikacija 10): FastAPI + WebSocket.

Zagon:  uvicorn api.app:app --port 8000
Noben odgovor za frontend ne vsebuje imena ponudnika poti; to je le na
/interno/zdravje (zahteva X-Skrbnik-Zeton = AID_SKRBNIK_ZETON).
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dnevnik.shramba import Shramba
from karta.ploscice import NiNaVoljo, Omejitev, Ploscice
from usmerjanje.servis import nastavi_dnevnik
from voznik.avtentikacija import NapakaPrijave
from voznik.ekipa import NapakaEkipe
from voznik.naloge import NapakaStanja
from voznik.vmesnik import ustvari_usmerjevalnik

from voznik.omejitve import Omejevalnik

from .dispecer import PISKOTEK, TRAJANJE_S, Dispecerji, NapakaPrijaveDispecerja, Varovalo
from .ovire import NapakaOvire
from .storitev import Center, NapakaStoritve

KOREN = Path(__file__).resolve().parent.parent


def nalozi_env(pot: Path = KOREN / ".env") -> None:
    """Ključi iz lokalne datoteke .env (ni v gitu). Okoljske spremenljivke imajo prednost."""
    if not pot.exists() or os.environ.get("AID_BREZ_ENV") == "1":
        return
    for vrstica in pot.read_text(encoding="utf-8").splitlines():
        vrstica = vrstica.strip()
        if not vrstica or vrstica.startswith("#") or "=" not in vrstica:
            continue
        kljuc, vrednost = vrstica.split("=", 1)
        os.environ.setdefault(kljuc.strip(), vrednost.strip().strip('"').strip("'"))


nalozi_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


class _SkrijKljuce(logging.Filter):
    """API ključi (key=… v URL) nikoli ne pridejo v dnevnik."""
    import re as _re
    _vzorec = _re.compile(r"(key=)[^&\s\"']+")

    def filter(self, zapis: logging.LogRecord) -> bool:
        zapis.msg = self._vzorec.sub(r"\1***", str(zapis.msg))
        if zapis.args:
            zapis.args = tuple(self._vzorec.sub(r"\1***", str(a)) if isinstance(a, str) or "key=" in str(a) else a
                               for a in (zapis.args if isinstance(zapis.args, tuple) else (zapis.args,)))
        return True


for _ime in ("httpx", "httpcore", "httpx2", "httpcore2", "usmerjanje", "karta", "geokodiranje", "poslusalec"):
    logging.getLogger(_ime).addFilter(_SkrijKljuce())
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


# ---------- telesa zahtev ----------
class ZacniKlic(BaseModel):
    stevilka: str | None = None
    cakajoci_id: str | None = None


class SegmentVhod(BaseModel):
    govorec: str = Field(pattern="^(klicatelj|dispecer)$")
    besedilo: str
    od_s: float | None = None
    do_s: float | None = None
    koncni: bool = True
    rocni: bool = False


class Vrednost(BaseModel):
    vrednost: Any


class RocniVnos(BaseModel):
    polje: str
    vrednost: Any


class Razlog(BaseModel):
    razlog: str | None = None


class DogodekVhod(BaseModel):
    tip: str
    parametri: dict = {}


class Odsek(BaseModel):
    od: tuple[float, float]
    do: tuple[float, float]
    tip: str = Field(default="vozilo", pattern="^(vozilo|motorist|helikopter)$")


class OdsekiVhod(BaseModel):
    odseki: list[Odsek] = Field(max_length=80)


class DodeliVhod(BaseModel):
    enota_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")


class DemoOvira(BaseModel):
    faza: str = Field(pattern="^(zapora|obtical)$")
    enota_id: str | None = None


class DemoKlic(BaseModel):
    cakajoci_id: str | None = None
    hitrost: float = 1.0


class PrijavaDispecerja(BaseModel):
    ime: str = Field(min_length=1, max_length=60)
    geslo: str = Field(min_length=1, max_length=200)


def uporabnik(request: Request) -> str:
    """Ime dispečerja iz prijavljene seje (pravilo 2.6) – nikoli iz glave odjemalca."""
    ime = getattr(request.state, "dispecer", None)
    if not ime:
        raise HTTPException(401, "prijava dispečerja je potrebna")
    return ime


def je_skrbnik(zeton: str | None) -> bool:
    pravi = os.environ.get("AID_SKRBNIK_ZETON")
    return bool(pravi and zeton and hmac.compare_digest(pravi, zeton))


def preveri_skrbnika(x_skrbnik_zeton: str | None = Header(default=None)) -> None:
    if not je_skrbnik(x_skrbnik_zeton):
        raise HTTPException(403, "samo za skrbnika")


# območje storitve (Slovenija z robom): poti in ploščice izven njega ne gredo k zunanjim ponudnikom
OBMOCJE = (45.2, 13.2, 47.0, 16.8)  # jug, zahod, sever, vzhod


def preveri_obmocje(lat: float, lon: float) -> None:
    j, z, s_, v = OBMOCJE
    if not (j <= lat <= s_ and z <= lon <= v):
        raise HTTPException(422, "točka je izven območja storitve")


def ploscica_v_obmocju(z: int, x: int, y: int) -> bool:
    import math
    if not (5 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return False
    n = 2 ** z
    def lat(yy):  # severni rob ploščice yy
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    zahod, vzhod = x / n * 360 - 180, (x + 1) / n * 360 - 180
    sever, jug = lat(y), lat(y + 1)
    j, zz, s_, v = OBMOCJE
    return vzhod >= zz and zahod <= v and sever >= j and jug <= s_


# prosojna ploščica 256×256 (PNG) za ploščice zunaj območja
def _prazna_ploscica() -> bytes:
    import struct
    import zlib
    vrstica = b"\x00" + b"\x00\x00\x00\x00" * 256  # filter 0 + 256 prosojnih RGBA pikslov

    def kos(tip, podatki):
        return struct.pack(">I", len(podatki)) + tip + podatki + struct.pack(">I", zlib.crc32(tip + podatki) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + kos(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0))
            + kos(b"IDAT", zlib.compress(vrstica * 256, 9)) + kos(b"IEND", b""))


PRAZNA_PLOSCICA = _prazna_ploscica()

# omejitve zahtevkov (na dispečerja/napravo/IP), da ne gre kvota zunanjih ponudnikov
OMEJITVE = {"ploscice": 2400, "poti": 30, "pot": 120, "eta": 120, "prijava": 10, "javnost": 60}


def ustvari_app(center: Center | None = None, ozadje: bool = True, dispecerji: Dispecerji | None = None) -> FastAPI:
    stanje: dict = {"center": center,
                    "dispecerji": dispecerji or Dispecerji.iz_okolja(KOREN / "demo" / "scenarij.json")}
    omejevalniki = {k: Omejevalnik(v) for k, v in OMEJITVE.items()}

    def omeji(vrsta: str, request: Request) -> None:
        kdo = getattr(request.state, "dispecer", None) or (request.headers.get("authorization") or "")[-24:] \
            or (request.client.host if request.client else "?")
        if not omejevalniki[vrsta].dovoli(kdo):
            raise HTTPException(429, "preveč zahtevkov – počakajte trenutek")

    @contextlib.asynccontextmanager
    async def zivljenje(_app):
        await zagon()
        yield
        for t in stanje.get("naloge", []):
            t.cancel()

    docs = os.environ.get("AID_DOCS") == "1"  # dokumentacija API le, če jo skrbnik izrecno vklopi
    app = FastAPI(title="AID – regijski dispečerski center", docs_url="/interno/docs" if docs else None, redoc_url=None,
                  openapi_url="/interno/openapi.json" if docs else None, lifespan=zivljenje)
    app.add_middleware(Varovalo, dispecerji=lambda: stanje["dispecerji"],
                       preveri_voznika=lambda t: stanje["center"].avtentikacija.preveri_dostop(t), je_skrbnik=je_skrbnik)

    # ---------- prijava dispečerja ----------
    @app.post("/dispecer/prijava")
    async def prijava_dispecerja(v: PrijavaDispecerja, request: Request):
        omeji("prijava", request)
        d: Dispecerji = stanje["dispecerji"]
        try:
            zeton, ime = d.prijava(v.ime, v.geslo)
        except NapakaPrijaveDispecerja as e:
            C().db.revizija(f"neznan ({request.client.host if request.client else '?'})", "Dispecer", v.ime[:60],
                            "prijava_zavrnjena", None, None)
            raise HTTPException(e.koda, str(e)) from e
        C().db.revizija(ime, "Dispecer", ime, "prijava", None, None)
        r = JSONResponse({"ime": ime})
        r.set_cookie(PISKOTEK, zeton, max_age=TRAJANJE_S, httponly=True, samesite="strict",
                     secure=request.url.scheme == "https", path="/")
        return r

    # ---------- javnost: opozorilo »reševalno vozilo v bližini« ----------
    @app.get("/javnost/blizina")
    async def blizina(lat: float, lon: float, request: Request):
        """Samo vozila NA NUJNI VOŽNJI v radiju 3 km; brez oznake vozila, naloge ali podatkov o pacientu.
        Položaj zaokrožen na ~10 m. Starih položajev (> 60 s) ne kažemo."""
        omeji("javnost", request)
        preveri_obmocje(lat, lon)
        from usmerjanje.ponudniki import zracna_razdalja_m
        c, zdaj, out = C(), time.time(), []
        for e in c.enote():
            k = c.naloge_voznikov.trenutni(e["id"])
            if not k or c.naloge_voznikov.faza(e["id"], k) != "na_poti" or not (k.get("nujna") or (k.get("prioriteta") or 5) <= 2):
                continue
            pol = e.get("polozaj") or {}
            if pol.get("vir") != "gps" or not pol.get("ts") or zdaj - pol["ts"] > 60:
                continue
            d = zracna_razdalja_m((lat, lon), (e["lat"], e["lon"]))
            if d > 3000:
                continue
            smer = pol.get("smer")
            proti = None
            if smer is not None:
                import math
                r = math.pi / 180
                y = math.sin((lon - e["lon"]) * r) * math.cos(lat * r)
                x = math.cos(e["lat"] * r) * math.sin(lat * r) - math.sin(e["lat"] * r) * math.cos(lat * r) * math.cos((lon - e["lon"]) * r)
                k_uporabniku = (math.degrees(math.atan2(y, x)) + 360) % 360
                proti = abs((k_uporabniku - smer + 180) % 360 - 180) < 70
            out.append({"lat": round(e["lat"], 4), "lon": round(e["lon"], 4), "smer": smer, "razdalja_m": round(d, -1),
                        "priblizuje_se": proti if proti is not None else d < 500})
        out.sort(key=lambda x: x["razdalja_m"])
        return {"vozila": out[:5], "cas": zdaj}

    # ---------- ovire na poti: obvoz in prerazporeditev (odloča dispečer) ----------
    @app.get("/ovire/vpliv")
    async def ovire_vpliv():
        return {"ovire": C().ovire.aktivne(), "vpliv": C().ovire.vpliv()}

    @app.get("/enote/{enota_id}/obvoz")
    async def obvoz_predlog(enota_id: str, request: Request):
        omeji("pot", request)
        return await C().ovire.predlog_obvoza(enota_id)

    @app.post("/enote/{enota_id}/obvoz")
    async def obvoz_potrdi(enota_id: str, u: str = Depends(uporabnik)):
        return await C().ovire.potrdi_obvoz(enota_id, u)

    @app.get("/naloge/{naloga_id}/kandidati")
    async def kandidati(naloga_id: str, request: Request):
        omeji("eta", request)
        return await C().ovire.kandidati(naloga_id)

    @app.post("/naloge/{naloga_id}/dodeli")
    async def dodeli(naloga_id: str, v: DodeliVhod, u: str = Depends(uporabnik)):
        return {"plan_predlog": await C().ovire.dodeli(naloga_id, v.enota_id, u)}

    @app.post("/ovire/{dogodek_id}/odstrani")
    async def odstrani_oviro(dogodek_id: str, u: str = Depends(uporabnik)):
        return C().ovire.odstrani(dogodek_id, u)

    @app.post("/demo/ovira")
    async def demo_ovira(v: DemoOvira, u: str = Depends(uporabnik)):
        return await C().ovire.demo(v.faza, v.enota_id, u)

    @app.get("/voznik/enote-za-prijavo")
    async def enote_za_prijavo():
        """Za prijavni obrazec voznika: samo id in ime (brez položajev in nalog)."""
        return [{"id": e["id"], "ime": e["ime"]} for e in C().db.vsi("enota")]

    @app.get("/dispecer/jaz")
    async def jaz(request: Request):
        return {"ime": uporabnik(request)}

    @app.post("/dispecer/odjava")
    async def odjava_dispecerja(request: Request):
        ime = stanje["dispecerji"].odjava(request.cookies.get(PISKOTEK))
        if ime:
            C().db.revizija(ime, "Dispecer", ime, "odjava", None, None)
        r = JSONResponse({"odjavljen": True})
        r.delete_cookie(PISKOTEK, path="/")
        return r

    async def zagon():
        if stanje["center"] is None:
            nastavi_dnevnik(os.environ.get("AID_USMERJANJE_LOG", str(KOREN / "usmerjanje.log")))
            stanje["center"] = Center(Shramba(os.environ.get("AID_DB", KOREN / "podatki" / "aid.sqlite")))
        c: Center = stanje["center"]
        if not os.environ.get("AID_VOZNIK_SKRIVNOST"):
            logging.getLogger("voznik").warning("AID_VOZNIK_SKRIVNOST ni nastavljen – žetoni voznikov ne preživijo ponovnega "
                                                "zagona (vozniki se morajo prijaviti znova). Za pravo uporabo ga nastavite.")
        if ozadje and ploscice.na_voljo("promet"):
            # ali ključ sploh ima Traffic API? (403 -> sloj prometa izklopljen, vmesnik skrije stikalo)
            for sloj in ("promet", "nesrece"):
                with contextlib.suppress(NiNaVoljo, Exception):
                    await asyncio.wait_for(ploscice.ploscica(sloj, 12, 2218, 1453), 5)
        if not c.plan_aktiven():
            p = await c.predlagaj_plan("začetni plan izmene")
            if p:
                c.potrdi_plan(p["id"], "sistem (začetek izmene)")
        if ozadje:
            from zajem import zvok
            zvok.objava = c.objava
            if any(k.get("posnetki") for k in c.demo_klici) or not c.demo_klici:
                zvok.prednalozi()  # Whisper se naloži ob zagonu strežnika, ne šele ob prvem klicu (le, če je zvočni klic)
            stanje["naloge"] = [asyncio.ensure_future(c.usmerjanje.zanka_zdravja(30)),
                                asyncio.ensure_future(_izvoz(c)),
                                asyncio.ensure_future(_signal(c)),
                                asyncio.ensure_future(_ciscenje(c))]

    async def _signal(c: Center):
        """Nadzor signala: prehod v_zivo / zakasnjeno / ni_signala -> `enota_signal` za dispečerja."""
        while True:
            with contextlib.suppress(Exception):
                objavi_signale(c)
            with contextlib.suppress(Exception):
                c.naloge_voznikov.preveri_eskalacije()
            with contextlib.suppress(Exception):
                c.heartbeati.preveri_izpade()
            await asyncio.sleep(2)

    async def _ciscenje(c: Center):
        while True:
            with contextlib.suppress(Exception):
                n = await asyncio.to_thread(c.polozaji.pocisti)
                m = c.ekipa.pocisti(c.polozaji.meja_hrambe())
                s = c.avtentikacija.pocisti()
                if n or m or s:
                    logging.getLogger("voznik").info("hramba: izbrisanih %d položajev, %d sporočil, %d sej", n, m, s)
            await asyncio.sleep(3600)

    async def _izvoz(c: Center):
        pot = Path(os.environ.get("AID_IZVOZ", KOREN / "podatki" / "izvoz.json"))
        while True:
            await asyncio.sleep(60)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(c.db.izvozi_json, pot)

    def C() -> Center:
        return stanje["center"]

    @app.exception_handler(NapakaOvire)
    async def napaka_ovire(_: Request, e: NapakaOvire):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda)

    @app.exception_handler(NapakaStoritve)
    async def napaka(_: Request, e: NapakaStoritve):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda)

    @app.exception_handler(NapakaPrijave)
    async def napaka_prijave(_: Request, e: NapakaPrijave):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda,
                            headers={"WWW-Authenticate": "Bearer"} if e.koda == 401 else None)

    @app.exception_handler(NapakaEkipe)
    async def napaka_ekipe(_: Request, e: NapakaEkipe):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda)

    @app.exception_handler(NapakaStanja)
    async def napaka_stanja(_: Request, e: NapakaStanja):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda)

    app.include_router(ustvari_usmerjevalnik(C, preveri_skrbnika, uporabnik))

    # ---------- klic ----------
    @app.get("/cakajoci")
    async def cakajoci():
        return C().cakajoci

    @app.post("/klic/zacni")
    async def zacni(v: ZacniKlic, u: str = Depends(uporabnik)):
        return C().zacni_klic(u, v.stevilka, v.cakajoci_id)

    @app.post("/klic/{klic_id}/segment")
    async def segment(klic_id: str, v: SegmentVhod, u: str = Depends(uporabnik)):
        return C().dodaj_segment(klic_id, v.govorec, v.besedilo, v.od_s, v.do_s, v.koncni, v.rocni, u)

    @app.post("/klic/{klic_id}/koncaj")
    async def koncaj(klic_id: str, u: str = Depends(uporabnik)):
        return C().koncaj_klic(klic_id, u)

    @app.get("/klic/{klic_id}")
    async def klic(klic_id: str):
        c = C()
        return {**c.klic(klic_id), "segmenti": c.segmenti(klic_id), "predlogi": c.predlogi(klic_id)}

    @app.get("/klic/{klic_id}/predlogi")
    async def predlogi(klic_id: str):
        C().klic(klic_id)
        return C().predlogi(klic_id)

    @app.post("/klic/{klic_id}/rocno")
    async def rocno(klic_id: str, v: RocniVnos, u: str = Depends(uporabnik)):
        return await C().rocni_vnos(klic_id, v.polje, v.vrednost, u)

    @app.post("/klic/{klic_id}/sprejmi-vse")
    async def sprejmi_vse(klic_id: str, u: str = Depends(uporabnik)):
        return await C().sprejmi_vse(klic_id, u)

    @app.post("/klic/{klic_id}/odobri")
    async def odobri(klic_id: str, u: str = Depends(uporabnik)):
        return await C().odobri_klic(klic_id, u)

    # ---------- predlogi ----------
    @app.post("/predlog/{pid}/sprejmi")
    async def sprejmi(pid: str, u: str = Depends(uporabnik)):
        return await C().sprejmi(pid, u)

    @app.post("/predlog/{pid}/spremeni")
    async def spremeni(pid: str, v: Vrednost, u: str = Depends(uporabnik)):
        return await C().spremeni(pid, v.vrednost, u)

    @app.post("/predlog/{pid}/zavrni")
    async def zavrni(pid: str, v: Razlog | None = None, u: str = Depends(uporabnik)):
        return C().zavrni(pid, u, v.razlog if v else None)

    # ---------- dogodki in plan ----------
    @app.post("/dogodek")
    async def dogodek(v: DogodekVhod, u: str = Depends(uporabnik)):
        from dnevnik.model import TIPI_DOGODKOV, dogodek as nov
        if v.tip not in TIPI_DOGODKOV:
            raise HTTPException(422, "neznan tip dogodka")
        d = nov(v.tip, v.parametri, "dispecer", "sprejeto")  # ročni vnos dispečerja = potrjen
        d["potrdil"] = u
        plan = await C().uveljavi_dogodek(d, u, planiraj=v.tip != "ovira_na_cesti")
        return {"dogodek": d, "plan_predlog": plan}

    @app.get("/plan/aktiven")
    async def plan_aktiven():
        return C().plan_aktiven()

    @app.get("/plan/predlog")
    async def plan_predlog():
        return next((p for p in C().db.vsi("plan", stanje="predlog") if not p.get("alternativa_od")), None)

    @app.get("/plan/{plan_id}")
    async def plan(plan_id: str):
        return C().plan(plan_id)

    @app.post("/plan/{plan_id}/potrdi")
    async def potrdi(plan_id: str, u: str = Depends(uporabnik)):
        return C().potrdi_plan(plan_id, u)

    @app.post("/plan/{plan_id}/zavrni")
    async def zavrni_plan(plan_id: str, v: Razlog | None = None, u: str = Depends(uporabnik)):
        return C().zavrni_plan(plan_id, u, v.razlog if v else None)

    @app.get("/plan/{plan_id}/alternative")
    async def alternative(plan_id: str):
        return C().alternative(plan_id)

    # ---------- viri ----------
    @app.get("/enote")
    async def enote():
        return C().enote()

    @app.get("/centri")
    async def centri():
        return C().db.vsi("center")

    @app.get("/naloge")
    async def naloge():
        return C().db.vsi("naloga")

    @app.get("/eta")
    async def eta(lat: float, lon: float, request: Request, n: int = 3):
        omeji("eta", request)
        preveri_obmocje(lat, lon)
        return await C().eta_enot(lat, lon, max(1, min(n, 10)))

    @app.get("/pot")
    async def pot(enota_id: str, lat: float, lon: float, request: Request):
        omeji("pot", request)
        preveri_obmocje(lat, lon)
        return await C().pot_enote(enota_id, lat, lon)

    @app.post("/poti")
    async def poti(v: OdsekiVhod, request: Request):
        omeji("poti", request)
        for o in v.odseki:
            preveri_obmocje(*o.od)
            preveri_obmocje(*o.do)
        return await C().poti([o.model_dump() for o in v.odseki])

    @app.post("/klic/{klic_id}/najblizja")
    async def najblizja(klic_id: str, u: str = Depends(uporabnik)):
        return await C().osvezi_najblizjo(klic_id, u)

    @app.get("/revizija")
    async def revizija(objekt_id: str | None = None, omejitev: int = 300):
        return C().db.revizije(objekt_id, omejitev)

    # ---------- povezava: polling rezerva za WebSocket ----------
    @app.get("/stanje")
    async def dogodki_od(od: int = 0):
        c = C()
        # položaji niso v zgodovini dogodkov (enota_premik je samo WS) -> posnetek za polling
        polozaji = [{"enota_id": e["id"], "lat": e["lat"], "lon": e["lon"], **e["polozaj"]} for e in c.enote()]
        return {"seq": c.objava.seq, "dogodki": c.objava.od(od), "ai_na_voljo": c.ai_na_voljo,
                "kakovost_poti": c.usmerjanje.javna_kakovost(), "razlog_poti": c.usmerjanje.razlog_ocene(),
                "cas": time.time(), "polozaji": polozaji}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        c = C()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        c.objava.narocniki.add(q)
        try:
            od = int(sock.query_params.get("od", "0") or 0)
            for s in c.objava.od(od):  # zamujeno med prekinitvijo
                await sock.send_json(s)
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            c.objava.narocniki.discard(q)

    # ---------- skrbnik ----------
    @app.get("/interno/zdravje")
    async def zdravje(_=Depends(preveri_skrbnika)):
        c = C()
        return {"usmerjanje": c.usmerjanje.zdravje(), "poslusalec_zadnji_vir": c.poslusalec.zadnji_vir,
                "poslusalec_napake": c.poslusalec.napake, "poslusalec_rezerva": c.poslusalec.rezerva,
                "ai_na_voljo": c.ai_na_voljo, "ws_narocniki": len(c.objava.narocniki)}

    # ---------- karta (posrednik ploščic; ključ ostane na strežniku) ----------
    ploscice = Ploscice()
    app.state.ploscice = ploscice

    @app.get("/ploscice/stanje")
    async def ploscice_stanje():
        return {"karta": ploscice.na_voljo("karta"), "promet": ploscice.na_voljo("promet"), "stevec": ploscice.stevec,
                "atribucija": os.environ.get("AID_KARTA_ATRIBUCIJA", "Podlaga karte in promet: zunanji vir")}

    @app.get("/ploscice/{sloj}/{z}/{x}/{y}.png")
    async def ploscica(sloj: str, z: int, x: int, y: int, request: Request):
        if not ploscica_v_obmocju(z, x, y):
            # zunaj Slovenije: prazna (prosojna) ploščica namesto napake – brskalnik je ne poskuša znova
            # in ne šteje kot izpad karte (pri oddaljeni karti je takih ploščic veliko)
            return Response(PRAZNA_PLOSCICA, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
        glave = {"Cache-Control": "private, max-age=" + ("120" if sloj in ("promet", "nesrece") else "86400")}
        vsebina = ploscice.iz_predpomnilnika(sloj, z, x, y)
        if vsebina is None:  # v omejitev štejejo le ploščice, ki jih je treba prenesti (premikanje karte jih ne porabi)
            omeji("ploscice", request)
            try:
                vsebina = await ploscice.ploscica(sloj, z, x, y)
            except Omejitev:
                # ponudnik začasno omejuje: brskalnik ploščico poskusi znova, karta ostane
                return Response(status_code=503, headers={"Retry-After": "2", "Cache-Control": "no-store"})
            except NiNaVoljo:
                raise HTTPException(404, "ploščica ni na voljo")
        return Response(vsebina, media_type="image/png", headers=glave)

    # ---------- demo ----------
    @app.post("/demo/klic")
    async def demo_klic(v: DemoKlic, u: str = Depends(uporabnik)):
        """Sprejme čakajoči demo klic in predvaja njegov pogovor iz demo/klici.json z realnimi časi (namesto VoIP)."""
        c = C()
        q = next((q for q in c.cakajoci if q["id"] == v.cakajoci_id), None) if v.cakajoci_id else \
            next((q for q in c.cakajoci if q.get("demo") and not q.get("zvok")), None)
        pogovor = c.demo_klic(q["demo"]) if q and q.get("demo") else None
        if not pogovor or not pogovor.get("segmenti"):
            raise HTTPException(404, "ni besedilnega demo pogovora")
        k = c.zacni_klic(u, cakajoci_id=q["id"])
        konec = lambda: (c.db.beri("klic", k["id"]) or {}).get("stanje") != "v_teku"  # noqa: E731
        hitrost = max(0.25, v.hitrost)

        async def tek():
            zanka = asyncio.get_running_loop()
            zacetek = zanka.time()
            for seg in pogovor["segmenti"]:  # segment se pojavi ob koncu izgovorjene replike
                await asyncio.sleep(max(0.0, zacetek + float(seg["do_s"]) / hitrost - zanka.time()))
                if konec():
                    return
                with contextlib.suppress(NapakaStoritve):
                    c.dodaj_segment(k["id"], seg["govorec"], seg["besedilo"], seg["od_s"], seg["do_s"])
        c._v_ozadju(tek())
        return k

    @app.post("/demo/klic-zvok")
    async def demo_klic_zvok(v: DemoKlic, u: str = Depends(uporabnik)):
        """Zvočni klic: posnetki poslusalec/zvok -> Whisper -> segmenti klica -> AI predlogi. Brez vnaprej določenega besedila."""
        from poslusalec import poslusalec as pp
        from zajem import zvok
        c = C()
        try:
            pp.posnetki_klica()
        except FileNotFoundError:
            raise HTTPException(503, "Manjkajo posnetki klica (poslusalec/zvok).")
        if zvok.nacin() == "whisper":  # demo (vnaprej določeno besedilo) Whisperja ne potrebuje
            if not zvok.whisper_na_voljo():
                raise HTTPException(503, zvok.razlog_ni_whisperja())
            if zvok.MODEL["stanje"] == "napaka" and not zvok.lahko_poskusi_znova():
                raise HTTPException(503, "Prepis govora ni na voljo: " + zvok.MODEL["razlog"])
        q = next((q for q in c.cakajoci if q["id"] == v.cakajoci_id), None) if v.cakajoci_id else \
            next((q for q in c.cakajoci if q.get("zvok")), None)
        k = c.zacni_klic(u, cakajoci_id=q["id"] if q else None, stevilka=None if q else "+386 51 318 774")
        konec = lambda: (c.db.beri("klic", k["id"]) or {}).get("stanje") != "v_teku"  # noqa: E731

        async def tek():
            try:
                await zvok.predvajaj_zvocni_klic(c, k["id"], max(0.25, v.hitrost), prekini=konec)
            except zvok.PrepisNiNaVoljo as e:
                c.objava.objavi("prepis_stanje", {"klic_id": k["id"], "stanje": "napaka", "sporocilo": str(e)})
                c.objava.objavi("opozorilo", {"vrsta": "prepis", "sporocilo": str(e) + " Nadaljujte z ročnim zapisom."})
            except Exception as e:  # noqa: BLE001 – klic ostane odprt za ročni zapis
                logging.getLogger("prepis").exception("zvočni klic")
                c.objava.objavi("opozorilo", {"vrsta": "prepis", "sporocilo": f"Prepis govora se je ustavil ({type(e).__name__}) – nadaljujte z ročnim zapisom."})
        c._v_ozadju(tek())
        return k

    @app.get("/klic/{klic_id}/zvok/{posnetek}")
    async def zvok_klica(klic_id: str, posnetek: str):
        """Posnetek zvočnega klica za brskalnik dispečerja (le prijavljen dispečer, le posnetki iz poslusalec/zvok)."""
        from poslusalec import poslusalec as pp
        if not (C().db.beri("klic", klic_id) or {}).get("id"):
            raise HTTPException(404, "ni klica")
        pot = next((p for p in pp.posnetki_klica() if p.name == posnetek), None)
        if pot is None:
            raise HTTPException(404, "ni posnetka")
        import mimetypes
        vrsta = {".wav": "audio/wav", ".opus": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4"}.get(pot.suffix.lower()) or mimetypes.guess_type(pot.name)[0] or "audio/wav"
        return FileResponse(pot, media_type=vrsta, headers={"Cache-Control": "private, max-age=600"})

    # ---------- heartbeat voznikov (shranjen v podatki/heartbeat/<datum>/<enota>.json) ----------
    @app.get("/heartbeat")
    async def heartbeat_zadnji():
        return list(C().heartbeati.zadnji.values())

    @app.get("/enote/{enota_id}/heartbeat")
    async def heartbeat_enote(enota_id: str, datum: str | None = None, omejitev: int = 200):
        if datum and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
            raise HTTPException(422, "datum: YYYY-MM-DD")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", enota_id):
            raise HTTPException(422, "neveljavna enota")
        return C().heartbeati.zgodovina(enota_id, datum, max(1, min(omejitev, 3000)))

    @app.get("/prepis/stanje")
    async def prepis_stanje():
        from zajem import zvok
        return zvok.stanje_modela()

    @app.post("/demo/izpad")
    async def demo_izpad(ai: bool = False):
        """Simuliran izpad AI poslušalca (vmesnik mora delati naprej z ročnim vnosom)."""
        C().poslusalec.simuliran_izpad = ai
        return {"ai_izpad": ai}

    # ---------- frontend ----------
    staticno = KOREN / "static"

    @app.get("/")
    async def index():
        return FileResponse(staticno / "index.html")

    app.mount("/", StaticFiles(directory=staticno), name="static")
    return app


def objavi_signale(c: Center, zdaj: float | None = None) -> list[dict]:
    spremembe = c.polozaji.spremembe_signala(c.db.vsi("enota"), zdaj)
    for s in spremembe:
        c.objava.objavi("enota_signal", s)
        if s["signal"] == "ni_signala":
            c.objava.objavi("opozorilo", {"vrsta": "signal", "enota_id": s["enota_id"],
                                          "sporocilo": f"{s['enota_id']}: ni signala GPS – prikazan je zadnji znani položaj."})
    return spremembe


app = ustvari_app()
