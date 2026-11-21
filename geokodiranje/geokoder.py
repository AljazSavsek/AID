"""Geokodiranje naslov -> koordinate z rezervo (specifikacija 7).

Vrstni red: GURS register naslovov (storitev, nastavljena z GURS_GEOKODER_URL)
-> lokalni izvoz registra naslovov (deluje brez interneta)
-> drug geokoder (Nominatim)
-> nič: dispečer klikne na karto.

Naslov se ne ugiba: vrnemo vse kandidate; več kandidatov = dvoumno.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path

import httpx

dnevnik = logging.getLogger("geokodiranje")
KOREN = Path(__file__).resolve().parent.parent


def normaliziraj(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\bul\.\s*", "ulica ", s)
    s = re.sub(r"\bc\.\s*", "cesta ", s)
    s = re.sub(r"[,.;]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def brez_sumnikov(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


class LokalniRegister:
    """Lokalni izvoz registra naslovov (za demo: demo/naslovi.json)."""
    ime = "lokalni_register"

    def __init__(self, pot: str | os.PathLike | None = None):
        pot = Path(pot or os.environ.get("AID_REGISTER_NASLOVOV", KOREN / "demo" / "naslovi.json"))
        self.naslovi = json.loads(pot.read_text(encoding="utf-8")) if pot.exists() else []

    async def isci(self, naslov: str) -> list[dict]:
        q = brez_sumnikov(normaliziraj(naslov))
        m = re.match(r"(.+?)\s+(\d+[a-z]?)(?:\s+(.+))?$", q)
        ulica, hs, kraj = (m.group(1), m.group(2), m.group(3)) if m else (q, None, None)
        out = []
        for z in self.naslovi:
            zu = brez_sumnikov(normaliziraj(z["ulica"]))
            if not (zu == ulica or zu.startswith(ulica) or ulica.startswith(zu)):
                continue
            if hs and str(z["hisna_stevilka"]).lower() != hs:
                continue
            if kraj and not brez_sumnikov(normaliziraj(z["kraj"])).startswith(kraj.split()[0][:5]):
                continue
            out.append({"naslov": f'{z["ulica"]} {z["hisna_stevilka"]}, {z["kraj"]}', "lat": z["lat"], "lon": z["lon"],
                        "ujemanje": 1.0 if hs else 0.5})
        return out


class GursGeokoder:
    """Storitev nad registrom prostorskih enot (RPE). URL nastavi skrbnik."""
    ime = "gurs"

    def na_voljo(self) -> bool:
        return bool(os.environ.get("GURS_GEOKODER_URL"))

    async def isci(self, naslov: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(os.environ["GURS_GEOKODER_URL"], params={"q": naslov})
            r.raise_for_status()
            return [{"naslov": x["naslov"], "lat": float(x["lat"]), "lon": float(x["lon"]), "ujemanje": float(x.get("ujemanje", 1))}
                    for x in r.json()]


class Nominatim:
    ime = "nominatim"

    def na_voljo(self) -> bool:
        return os.environ.get("AID_NOMINATIM", "1") == "1"

    async def isci(self, naslov: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=3.0, headers={"User-Agent": "aid-dispecer-prototip/0.1"}) as c:
            r = await c.get(os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
                            params={"q": naslov, "countrycodes": "si", "format": "jsonv2", "limit": 5})
            r.raise_for_status()
            return [{"naslov": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"]),
                     "ujemanje": float(x.get("importance", 0.5))} for x in r.json()]


class Geokoder:
    def __init__(self, viri: list | None = None):
        self.viri = viri if viri is not None else [GursGeokoder(), LokalniRegister(), Nominatim()]

    async def geokodiraj(self, naslov: str) -> list[dict]:
        """Vrne seznam kandidatov (lahko prazen -> dispečer klikne na karto)."""
        for vir in self.viri:
            if hasattr(vir, "na_voljo") and not vir.na_voljo():
                continue
            try:
                kandidati = await vir.isci(naslov)
            except Exception as e:  # noqa: BLE001
                dnevnik.warning("geokoder %s napaka: %s", vir.ime, e)
                continue
            if kandidati:
                return kandidati
        return []
