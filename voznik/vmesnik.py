"""API za aplikacijo voznika (/voznik/*) in dodatki za dispečerja/skrbnika.

Pravila:
- enota pride iz žetona; `enota_id` v telesu je dovoljen le, če se ujema (sicer 403);
- vsako dejanje voznika gre v revizijo (položaji imajo svojo zgodovino v tabeli `polozaj`);
- odgovori ne vsebujejo imen ponudnikov poti ali ključev.
"""
from __future__ import annotations

from typing import Callable

import asyncio
import contextlib
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .avtentikacija import NapakaPrijave
from .omejitve import Omejevalnik

ID_VZOREC = r"^[A-Za-z0-9._:-]{1,64}$"


class Prijava(BaseModel):
    enota_id: str = Field(pattern=ID_VZOREC)
    koda: str = Field(min_length=1, max_length=64)
    naprava_id: str = Field(pattern=ID_VZOREC)


class Osvezitev(BaseModel):
    osvezitev: str = Field(min_length=10, max_length=2048)


class Fiks(BaseModel):
    seq: int = Field(ge=0, le=2 ** 53)
    ts: float = Field(gt=0)                       # čas fiksa na napravi (unix s)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    hitrost: float | None = Field(default=None, ge=0, le=200)        # m/s
    smer: float | None = Field(default=None, ge=0, le=360)           # stopinje
    natancnost: float | None = Field(default=None, ge=0, le=100000)  # m
    lazno: bool = False


class StanjeVhod(BaseModel):
    stanje: str = Field(pattern=r"^[a-z_]{3,30}$")
    naloga_id: str | None = Field(default=None, pattern=ID_VZOREC)
    akcija: str | None = Field(default=None, pattern=r"^(prevzem|dostava)$")


class PotrditevVhod(BaseModel):
    obvestilo_id: str = Field(pattern=ID_VZOREC)


class SporociloVhod(BaseModel):  # voznik: hitro ALI besedilo; dispečer: hitro in/ali besedilo
    koda: str | None = Field(default=None, pattern=r"^[a-z_]{2,30}$")
    besedilo: str | None = Field(default=None, max_length=500)


class OkvaraVhod(BaseModel):
    opis: str | None = Field(default=None, max_length=200)


class Lokacija(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class OviraVhod(BaseModel):
    vrsta: str = Field(pattern=r"^[a-z_]{2,30}$")
    opis: str | None = Field(default=None, max_length=200)
    lokacija: Lokacija | None = None


class OdlocitevVhod(BaseModel):
    parametri: dict | None = None
    razlog: str | None = Field(default=None, max_length=200)


class Baterija(BaseModel):
    nivo: int | None = Field(default=None, ge=0, le=100)
    polni: bool = False


class PaketPolozajev(BaseModel):
    enota_id: str | None = Field(default=None, pattern=ID_VZOREC)
    naprava_id: str | None = Field(default=None, pattern=ID_VZOREC)
    poslano_ob: float | None = Field(default=None, gt=0)
    baterija: Baterija | None = None
    fiksi: list[Fiks] = Field(min_length=1, max_length=200)


def ustvari_usmerjevalnik(C: Callable, preveri_skrbnika: Callable) -> APIRouter:
    r = APIRouter()
    omejitev_prijava: dict = {}
    omejitev_polozaj: dict = {}
    omejitev_ekipa: dict = {}

    def omeji_ekipo(enota_id: str) -> None:
        if not omejevalnik(omejitev_ekipa, 20).dovoli(enota_id):
            raise HTTPException(429, "preveč sporočil")

    def omejevalnik(shramba: dict, na_minuto: int) -> Omejevalnik:
        if "o" not in shramba:
            shramba["o"] = Omejevalnik(na_minuto)
        return shramba["o"]

    def voznik(authorization: str | None = Header(default=None)) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise NapakaPrijave("manjka žeton")
        c = C()
        v = c.avtentikacija.preveri_dostop(authorization.split(" ", 1)[1].strip())
        e = c.db.beri("enota", v["enota"])
        if not e:
            raise NapakaPrijave("enota ne obstaja")
        return {**v, "enota_obj": e}

    def ista_enota(v: dict, enota_id: str | None, naprava_id: str | None = None) -> None:
        if enota_id is not None and enota_id != v["enota"]:
            C().db.revizija(f"voznik {v['enota']}", "Enota", enota_id, "dostop_zavrnjen", None, {"zahteval": v["enota"]})
            raise HTTPException(403, "dostop samo do lastne enote")
        if naprava_id is not None and naprava_id != v["naprava"]:
            raise HTTPException(403, "naprava se ne ujema s prijavo")

    # ---------- identiteta ----------
    @r.post("/voznik/prijava")
    async def prijava(v: Prijava, request: Request):
        c = C()
        ip = request.client.host if request.client else "?"
        if not omejevalnik(omejitev_prijava, c.nast_voznika.omejitev_prijava_min).dovoli(ip):
            raise HTTPException(429, "preveč poskusov prijave")
        e = c.db.beri("enota", v.enota_id)
        if not e:
            raise NapakaPrijave("napačna enota ali koda")
        zetoni, preklicane = c.avtentikacija.prijava(v.enota_id, v.koda, v.naprava_id)
        c.objava.objavi("voznik_prijava", {"enota_id": v.enota_id, "prijavljen": True})
        if preklicane:
            c.objava.objavi("opozorilo", {"vrsta": "voznik", "enota_id": v.enota_id,
                                          "sporocilo": f"{v.enota_id}: prijava z nove naprave – prejšnja naprava je odjavljena."})
        return {**zetoni, "enota": {"id": e["id"], "ime": e["ime"], "tip": e["tip"]},
                "gps": c.nast_voznika.frekvence(e.get("stanje") == "na_nalogi")}

    @r.post("/voznik/osvezi")
    async def osvezi(v: Osvezitev):
        return C().avtentikacija.osvezi(v.osvezitev)

    @r.post("/voznik/odjava")
    async def odjava(v: dict = Depends(voznik)):
        c = C()
        c.avtentikacija.odjava(v["seja"], v["enota"])
        c.objava.objavi("voznik_prijava", {"enota_id": v["enota"], "prijavljen": False})
        return {"odjavljen": True}

    @r.get("/voznik/jaz")
    async def jaz(v: dict = Depends(voznik)):
        e = v["enota_obj"]
        return {"enota": {"id": e["id"], "ime": e["ime"], "tip": e["tip"], "stanje": e["stanje"]},
                "gps": C().nast_voznika.frekvence(e.get("stanje") == "na_nalogi")}

    # ---------- GPS ----------
    @r.post("/voznik/polozaj")
    async def polozaj(p: PaketPolozajev, v: dict = Depends(voznik)):
        c = C()
        ista_enota(v, p.enota_id, p.naprava_id)
        if not omejevalnik(omejitev_polozaj, c.nast_voznika.omejitev_polozaj_min).dovoli(v["enota"]):
            raise HTTPException(429, "prepogosto pošiljanje")
        e = v["enota_obj"]
        frek = c.nast_voznika.frekvence(e.get("stanje") == "na_nalogi", p.baterija.model_dump() if p.baterija else None)
        izid = c.polozaji.sprejmi(e, v["naprava"], [f.model_dump() for f in p.fiksi], p.poslano_ob, frek["posiljanje_s"])
        if izid["premik"]:
            m = izid["premik"]
            opis = c.polozaji.opis(e, m)
            c.objava.objavi("enota_premik", {"enota_id": e["id"], "lat": m["lat"], "lon": m["lon"], **opis},
                            v_zgodovino=False)
        return {"potrjeno_do_seq": izid["potrjeno_do_seq"], "novi": izid["novi"], "podvojeni": izid["podvojeni"],
                "zavrnjeni": izid["zavrnjeni"], "gps": frek}

    # ---------- naloge in stanja ----------
    @r.get("/voznik/naloga")
    async def naloga(v: dict = Depends(voznik)):
        nv = C().naloge_voznikov
        if nv.trenutni(v["enota"]) and not nv.pot(v["enota"]):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(nv.izracunaj_pot(v["enota"]), 5)
        return nv.za_voznika(v["enota"])

    @r.post("/voznik/stanje")
    async def stanje(s: StanjeVhod, v: dict = Depends(voznik)):
        return C().naloge_voznikov.spremeni(v["enota"], s.stanje, s.naloga_id, s.akcija)

    @r.post("/voznik/potrdi")
    async def potrdi(p: PotrditevVhod, v: dict = Depends(voznik)):
        return C().naloge_voznikov.potrdi(v["enota"], p.obvestilo_id)

    # ---------- sporočila in predlogi ekipe ----------
    @r.post("/voznik/sporocilo")
    async def sporocilo_voznika(m: SporociloVhod, v: dict = Depends(voznik)):
        omeji_ekipo(v["enota"])
        s = C().ekipa.od_voznika(v["enota"], m.koda, m.besedilo)
        if m.besedilo and not m.koda:
            # specifikacija 9: besedilo ekipe -> AI -> PREDLOG dogodka (dispečer odloči)
            s["predlog_ai"] = await C().ekipa.ai_predlog(v["enota"], m.besedilo)
        return s

    @r.post("/voznik/sporocilo/{sporocilo_id}/prebrano")
    async def prebrano(sporocilo_id: str, v: dict = Depends(voznik)):
        return C().ekipa.prebrano(v["enota"], sporocilo_id)

    @r.post("/voznik/okvara")
    async def okvara(o: OkvaraVhod, v: dict = Depends(voznik)):
        omeji_ekipo(v["enota"])
        return C().ekipa.predlagaj(v["enota"], "okvara_vozila", o.model_dump())

    @r.post("/voznik/ovira")
    async def ovira(o: OviraVhod, v: dict = Depends(voznik)):
        omeji_ekipo(v["enota"])
        return C().ekipa.predlagaj(v["enota"], "ovira_na_cesti", o.model_dump())

    @r.get("/voznik/dogodki")
    async def dogodki(od: int = 0, zagon: str | None = None, v: dict = Depends(voznik)):
        """Polling rezerva za /ws/voznik."""
        o = C().naloge_voznikov.objava
        return {"zagon": o.zagon, "seq": o.seq, "dogodki": o.od(v["enota"], od, zagon)}

    @r.websocket("/ws/voznik")
    async def ws_voznik(sock: WebSocket):
        """Prvo sporočilo: {"zeton": dostopni, "od": seq, "zagon": id}. Žeton ni v URL (dnevniki).
        Strežnik vsakih 15 s pošlje {"tip": "ziv"} in ob tem preveri, da seja še velja."""
        await sock.accept()
        c = C()
        try:
            prvo = await asyncio.wait_for(sock.receive_json(), 10)
            v = c.avtentikacija.preveri_dostop(str(prvo.get("zeton", "")))
        except (NapakaPrijave, asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError, AttributeError):
            with contextlib.suppress(Exception):
                await sock.send_json({"tip": "napaka", "podatki": {"napaka": "neveljaven žeton"}})
                await sock.close(code=4401)
            return
        o = c.naloge_voznikov.objava
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        o.narocniki.setdefault(v["enota"], set()).add(q)
        try:
            await sock.send_json({"tip": "pozdrav", "zagon": o.zagon, "seq": o.seq, "podatki": {"enota_id": v["enota"]}})
            for s in o.od(v["enota"], int(prvo.get("od") or 0), prvo.get("zagon")):
                await sock.send_json(s)
            while True:
                try:
                    await sock.send_json(await asyncio.wait_for(q.get(), 15))
                except asyncio.TimeoutError:
                    c.avtentikacija.preveri_dostop(str(prvo.get("zeton", "")))  # potekel/preklican -> zapri
                    await sock.send_json({"tip": "ziv", "cas": time.time()})
        except NapakaPrijave:
            with contextlib.suppress(Exception):
                await sock.close(code=4401)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            o.narocniki.get(v["enota"], set()).discard(q)

    # ---------- dispečer: ekipe ----------
    def uporabnik(x_uporabnik: str | None = Header(default=None)) -> str:
        return (x_uporabnik or "dispečer").strip()[:60]

    @r.get("/ekipe/predlogi")
    async def predlogi_ekip(vsi: bool = False):
        return C().ekipa.predlogi(not vsi)

    @r.post("/ekipe/predlog/{dogodek_id}/{akcija}")
    async def odloci(dogodek_id: str, akcija: str, o: OdlocitevVhod | None = None, u: str = Depends(uporabnik)):
        if akcija not in ("sprejmi", "spremeni", "zavrni"):
            raise HTTPException(404, "neznano dejanje")
        o = o or OdlocitevVhod()
        return await C().ekipa.odloci(dogodek_id, u, akcija, o.parametri, o.razlog)

    @r.get("/ekipe/sporocila")
    async def sporocila(enota_id: str | None = None):
        return C().ekipa.sporocila(enota_id, 50)

    @r.post("/enote/{enota_id}/sporocilo")
    async def sporocilo_dispecerja(enota_id: str, m: SporociloVhod, u: str = Depends(uporabnik)):
        return C().ekipa.od_dispecerja(enota_id, u, m.koda, m.besedilo)

    @r.get("/ekipe/eskalacije")
    async def eskalacije():
        return C().naloge_voznikov.eskalacije()

    @r.get("/ekipe/hitra-sporocila")
    async def hitra():
        from .ekipa import HITRA_DISPECER, HITRA_VOZNIK
        return {"dispecer": HITRA_DISPECER, "voznik": HITRA_VOZNIK}

    @r.get("/ovire")
    async def ovire():
        return C().ekipa.ovire()

    # ---------- dispečer / skrbnik ----------
    @r.post("/enote/{enota_id}/koda")
    async def izdaj_kodo(enota_id: str, x_uporabnik: str | None = Header(default=None), _=Depends(preveri_skrbnika)):
        c = C()
        if not c.db.beri("enota", enota_id):
            raise HTTPException(404, "enota ne obstaja")
        koda = c.avtentikacija.izdaj_kodo(enota_id, (x_uporabnik or "skrbnik").strip()[:60])
        c.objava.objavi("voznik_prijava", {"enota_id": enota_id, "prijavljen": False})
        return {"enota_id": enota_id, "koda": koda}

    @r.get("/interno/polozaji/{enota_id}")
    async def zgodovina(enota_id: str, od: float | None = None, do: float | None = None,
                        x_uporabnik: str | None = Header(default=None), _=Depends(preveri_skrbnika)):
        c = C()
        c.db.revizija((x_uporabnik or "skrbnik").strip()[:60], "Enota", enota_id, "vpogled_zgodovina_polozajev",
                      None, {"od": od, "do": do})
        return c.polozaji.zgodovina(enota_id, od, do)

    return r
