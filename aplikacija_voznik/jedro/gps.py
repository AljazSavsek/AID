"""Viri GPS. Na napravi je vir Flet Geolocator (naprava.py); tu je vmesnik in simuliran vir
za razvoj na računalniku in za simulator vozila (vozi po geometriji poti)."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Callable

from .navigacija import azimut, razdalja_m


def fiks(lat: float, lon: float, ts: float | None = None, hitrost: float | None = None, smer: float | None = None,
         natancnost: float | None = None, lazno: bool = False) -> dict:
    return {"ts": ts or time.time(), "lat": round(lat, 7), "lon": round(lon, 7),
            "hitrost": None if hitrost is None else max(0.0, round(hitrost, 2)),
            "smer": None if smer is None else round(smer % 360, 1),
            "natancnost": None if natancnost is None else round(natancnost, 1), "lazno": bool(lazno)}


class SimuliranVir:
    """Premika se po poti (seznam [lat, lon]) s hitrostjo `hitrost_ms`; brez poti stoji na mestu."""

    def __init__(self, zacetek: tuple[float, float], hitrost_ms: float = 13.9, ura=time.time):
        self.polozaj = tuple(zacetek)
        self.hitrost_ms = hitrost_ms
        self.ura = ura
        self.pot: list[tuple] = []
        self._tece = False
        self.fiks_s = 2.0

    def nastavi_pot(self, geometrija: list) -> None:
        self.pot = [tuple(t) for t in geometrija or []]

    def korak(self, dt: float) -> dict:
        ostane = self.hitrost_ms * dt
        smer = None
        while ostane > 0 and self.pot:
            cilj = self.pot[0]
            d = razdalja_m(self.polozaj, cilj)
            smer = azimut(self.polozaj, cilj) if d > 0.5 else smer
            if d <= ostane:
                self.polozaj, ostane = cilj, ostane - d
                self.pot.pop(0)
            else:
                f = ostane / d
                self.polozaj = (self.polozaj[0] + (cilj[0] - self.polozaj[0]) * f,
                                self.polozaj[1] + (cilj[1] - self.polozaj[1]) * f)
                ostane = 0
        premika = bool(self.pot) or (ostane < self.hitrost_ms * dt)
        return fiks(self.polozaj[0], self.polozaj[1], self.ura(), self.hitrost_ms if premika else 0.0, smer, 5.0)

    async def zacni(self, ob_fiksu: Callable[[dict], None], fiks_s: float = 2.0) -> None:
        self._tece, self.fiks_s = True, fiks_s
        while self._tece:
            ob_fiksu(self.korak(self.fiks_s))
            await asyncio.sleep(self.fiks_s)

    def nastavi_interval(self, fiks_s: float) -> None:
        self.fiks_s = max(0.2, float(fiks_s))

    def ustavi(self) -> None:
        self._tece = False


def iz_geolocatorja(p) -> dict | None:
    """GeolocatorPosition (Flet) -> fiks. Na napravi 0.0 pomeni »ni podatka« za hitrost/smer."""
    if p is None or p.latitude is None or p.longitude is None:
        return None
    ts = p.timestamp.timestamp() if getattr(p, "timestamp", None) else time.time()
    if not (math.isfinite(p.latitude) and math.isfinite(p.longitude)):
        return None
    return fiks(p.latitude, p.longitude, ts, p.speed if p.speed and p.speed > 0 else (0.0 if p.speed == 0 else None),
                p.heading if p.heading and p.heading > 0 else None, p.accuracy or None, bool(p.mocked))
