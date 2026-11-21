"""API za aplikacijo voznika (/voznik/*) in dodatki za dispečerja/skrbnika.

Pravila:
- enota pride iz žetona; `enota_id` v telesu je dovoljen le, če se ujema (sicer 403);
- vsako dejanje voznika gre v revizijo (položaji imajo svojo zgodovino v tabeli `polozaj`);
- odgovori ne vsebujejo imen ponudnikov poti ali ključev.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
