"""Heartbeat naprave: vsakih 30 s centrali pošlje hitrost, lokacijo in pot (POST /voznik/heartbeat).
Centrala ga shrani v JSON in ob zastoju ali oviri vozniku izračuna drugo pot (pride kot dogodek `pot`).
Brez odvisnosti od Flet: uporabljata ga aplikacija voznika in simulator vozil."""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from .navigacija import stanje_navigacije

INTERVAL_S = 30.0


def paket(fiks: dict | None, naloga: dict | None, ura: float | None = None) -> dict | None:
    """fiks: zadnji GPS fiks (hitrost v m/s); naloga: odgovor GET /voznik/naloga."""
    ura = ura or time.time()
    enota = (naloga or {}).get("enota") or {}
    if fiks:
        lokacija = {"lat": fiks["lat"], "lon": fiks["lon"]}
        for k in ("natancnost", "smer"):
            if fiks.get(k) is not None:
                lokacija[k] = fiks[k]
        ts = fiks.get("ts")
        svez = ts is None or ura - ts < INTERVAL_S + 5
        hitrost = round(fiks["hitrost"] * 3.6, 1) if svez and fiks.get("hitrost") is not None else 0.0
    elif enota.get("lat") is not None:
        lokacija, hitrost = {"lat": enota["lat"], "lon": enota["lon"]}, 0.0
    else:
        return None
    k = next((x for x in (naloga or {}).get("koraki", []) if x.get("trenutni")), None)
    pot = None
    if k:
        p = (naloga or {}).get("pot")
        nav = stanje_navigacije((lokacija["lat"], lokacija["lon"]), p, (k["lat"], k["lon"]), hitrost / 3.6 if hitrost else None)
        pot = {"naloga_id": k["naloga_id"], "akcija": k["akcija"], "izracunano": (p or {}).get("izracunano")}
        for ime, kljuc in (("preostalo_m", "preostalo_m"), ("preostalo_s", "eta_s"), ("odmik_m", "odmik_m")):
            if nav.get(kljuc) is not None:
                pot[ime] = round(nav[kljuc])
    return {"ts": ura, "hitrost_kmh": hitrost, "lokacija": lokacija, "pot": pot}


class Heartbeat:
    def __init__(self, odjemalec, vir: Callable[[], tuple], interval_s: float = INTERVAL_S, ura=time.time):
        self.o, self.vir, self.interval_s, self.ura = odjemalec, vir, interval_s, ura
        self.zadnji = 0.0
        self.poslanih = 0
        self._tece = False

    def je_cas(self) -> bool:
        return self.ura() - self.zadnji >= self.interval_s

    async def poslji(self) -> bool:
        fiks, naloga = self.vir()
        p = paket(fiks, naloga, self.ura())
        self.zadnji = self.ura()  # tudi ob napaki: naslednji poskus čez interval (ne zasujemo strežnika)
        if not p:
            return False
        try:
            await self.o.heartbeat(p)
        except Exception:  # noqa: BLE001 – brez povezave: naslednji heartbeat čez 30 s
            return False
        self.poslanih += 1
        return True

    async def tek(self) -> None:
        self._tece = True
        while self._tece:
            if self.je_cas():
                await self.poslji()
            await asyncio.sleep(1.0)

    def ustavi(self) -> None:
        self._tece = False
