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
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dnevnik.shramba import Shramba
from karta.ploscice import NiNaVoljo, Ploscice
from usmerjanje.servis import nastavi_dnevnik
from zajem.simulator import nalozi_klic, predvajaj

from . import ekipa
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


for _ime in ("httpx", "httpcore", "usmerjanje", "karta", "geokodiranje", "poslusalec"):
    logging.getLogger(_ime).addFilter(_SkrijKljuce())
logging.getLogger("httpx").setLevel(logging.WARNING)


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


class Lokacija(BaseModel):
    lat: float
    lon: float
    smer: float | None = None
    hitrost_kmh: float | None = None


class StatusVhod(BaseModel):
    naloga_id: str
    akcija: str


class SporociloVhod(BaseModel):
    enota_id: str
    besedilo: str = Field(max_length=500)


class DogodekPotrditev(BaseModel):
    parametri: dict | None = None


class DemoKlic(BaseModel):
    hitrost: float = 1.0


def uporabnik(x_uporabnik: str | None = Header(default=None)) -> str:
    from urllib.parse import unquote
    return (unquote(x_uporabnik) if x_uporabnik else "dispečer").strip()[:60]


def ustvari_app(center: Center | None = None, ozadje: bool = True) -> FastAPI:
    stanje: dict = {"center": center}

    @contextlib.asynccontextmanager
    async def zivljenje(_app):
        await zagon()
        yield
        for t in stanje.get("naloge", []):
            t.cancel()

    app = FastAPI(title="AID – regijski dispečerski center", docs_url="/interno/docs", redoc_url=None,
                  openapi_url="/interno/openapi.json", lifespan=zivljenje)

    async def zagon():
        if stanje["center"] is None:
            nastavi_dnevnik(os.environ.get("AID_USMERJANJE_LOG", str(KOREN / "usmerjanje.log")))
            stanje["center"] = Center(Shramba(os.environ.get("AID_DB", KOREN / "podatki" / "aid.sqlite")))
        c: Center = stanje["center"]
        if not c.plan_aktiven():
            p = await c.predlagaj_plan("začetni plan izmene")
            if p:
                c.potrdi_plan(p["id"], "sistem (začetek izmene)")
        if ozadje:
            stanje["naloge"] = [asyncio.ensure_future(c.usmerjanje.zanka_zdravja(30)),
                                asyncio.ensure_future(_izvoz(c))]

    async def _izvoz(c: Center):
        pot = Path(os.environ.get("AID_IZVOZ", KOREN / "podatki" / "izvoz.json"))
        while True:
            await asyncio.sleep(60)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(c.db.izvozi_json, pot)

    def C() -> Center:
        return stanje["center"]

    @app.exception_handler(NapakaStoritve)
    async def napaka(_: Request, e: NapakaStoritve):
        return JSONResponse({"napaka": str(e)}, status_code=e.koda)

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
        plan = await C().uveljavi_dogodek(d, u)
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
        return C().db.vsi("enota")

    @app.get("/centri")
    async def centri():
        return C().db.vsi("center")

    @app.get("/naloge")
    async def naloge():
        return C().db.vsi("naloga")

    @app.get("/eta")
    async def eta(lat: float, lon: float, n: int = 3):
        return await C().eta_enot(lat, lon, n)

    @app.get("/pot")
    async def pot(enota_id: str, lat: float, lon: float):
        return await C().pot_enote(enota_id, lat, lon)

    @app.post("/poti")
    async def poti(v: OdsekiVhod):
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
        return {"seq": c.objava.seq, "dogodki": c.objava.od(od), "ai_na_voljo": c.ai_na_voljo,
                "kakovost_poti": c.usmerjanje.javna_kakovost()}

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
    async def zdravje(x_skrbnik_zeton: str | None = Header(default=None)):
        zeton = os.environ.get("AID_SKRBNIK_ZETON")
        if not zeton or not x_skrbnik_zeton or not hmac.compare_digest(zeton, x_skrbnik_zeton):
            raise HTTPException(403, "samo za skrbnika")
        c = C()
        return {"usmerjanje": c.usmerjanje.zdravje(), "poslusalec_zadnji_vir": c.poslusalec.zadnji_vir,
                "ai_na_voljo": c.ai_na_voljo, "ws_narocniki": len(c.objava.narocniki)}

    # ---------- ekipa na terenu (voznik) ----------
    @app.get("/voznik/{enota_id}")
    async def voznik(enota_id: str):
        return await ekipa.pregled(C(), enota_id)

    @app.post("/enota/{enota_id}/lokacija")
    async def enota_lokacija(enota_id: str, v: Lokacija):
        return ekipa.lokacija(C(), enota_id, v.lat, v.lon, v.smer, v.hitrost_kmh)

    @app.post("/enota/{enota_id}/status")
    async def enota_status(enota_id: str, v: StatusVhod, u: str = Depends(uporabnik)):
        return ekipa.status(C(), enota_id, v.naloga_id, v.akcija, u)

    @app.post("/sporocilo")
    async def sporocilo(v: SporociloVhod, u: str = Depends(uporabnik)):
        return await ekipa.sporocilo(C(), v.enota_id, v.besedilo, u)

    @app.get("/dogodki")
    async def dogodki(stanje: str | None = None):
        return [d for d in C().db.vsi("dogodek") if stanje is None or d["stanje"] == stanje]

    @app.post("/dogodek/{dogodek_id}/sprejmi")
    async def dogodek_sprejmi(dogodek_id: str, v: DogodekPotrditev | None = None, u: str = Depends(uporabnik)):
        return await ekipa.sprejmi_dogodek(C(), dogodek_id, u, v.parametri if v else None)

    @app.post("/dogodek/{dogodek_id}/zavrni")
    async def dogodek_zavrni(dogodek_id: str, v: Razlog | None = None, u: str = Depends(uporabnik)):
        return ekipa.zavrni_dogodek(C(), dogodek_id, u, v.razlog if v else None)

    # ---------- karta (posrednik ploščic; ključ ostane na strežniku) ----------
    ploscice = Ploscice()
    app.state.ploscice = ploscice

    @app.get("/ploscice/stanje")
    async def ploscice_stanje():
        return {"karta": ploscice.na_voljo("karta"), "promet": ploscice.na_voljo("promet"),
                "atribucija": os.environ.get("AID_KARTA_ATRIBUCIJA", "Podlaga karte in promet: zunanji vir")}

    @app.get("/ploscice/{sloj}/{z}/{x}/{y}.png")
    async def ploscica(sloj: str, z: int, x: int, y: int):
        try:
            vsebina = await ploscice.ploscica(sloj, z, x, y)
        except NiNaVoljo:
            raise HTTPException(404, "ploščica ni na voljo")
        return Response(vsebina, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=" + ("120" if sloj in ("promet", "nesrece") else "86400")})

    # ---------- demo ----------
    @app.post("/demo/klic")
    async def demo_klic(v: DemoKlic, u: str = Depends(uporabnik)):
        """Sprejme prvi čakajoči klic in predvaja demo/klic1.json (namesto VoIP + STT)."""
        c = C()
        q = next((q for q in c.cakajoci if q.get("demo")), None)
        k = c.zacni_klic(u, cakajoci_id=q["id"] if q else None, stevilka=None if q else "+386 41 555 238")
        segmenti = nalozi_klic()["segmenti"]

        async def poslji(seg):
            with contextlib.suppress(NapakaStoritve):
                c.dodaj_segment(k["id"], seg["govorec"], seg["besedilo"], seg["od_s"], seg["do_s"])

        c._v_ozadju(predvajaj(segmenti, poslji, max(0.25, v.hitrost),
                              prekini=lambda: (c.db.beri("klic", k["id"]) or {}).get("stanje") != "v_teku"))
        return k

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


app = ustvari_app()
