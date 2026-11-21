"""Strežniški posrednik za ploščice karte (osnovna karta in sloj prometa).

- ključ ostane na strežniku (pravilo 2.7): brskalnik kliče /ploscice/..., ne ponudnika
- odgovor ne vsebuje imena ponudnika (pravilo 2.4): pošljemo samo slikovne bajte
- predpomnilnik v pomnilniku: osnovna karta 24 h, promet 2 min
- brez ključa ali ob napaki vmesnik uporabi brezplačno podlago (OSM/CARTO)

Ključ: TOMTOM_MAP_KEY (Map Display + Traffic API), sicer TOMTOM_API_KEY.
"""
from __future__ import annotations

import collections
import logging
import os
import time

import httpx

dnevnik = logging.getLogger("karta")

SLOJI = {
    # sloj: (predloga URL, TTL v sekundah)
    "karta": ("https://api.tomtom.com/map/1/tile/basic/main/{z}/{x}/{y}.png", 24 * 3600),
    "karta-nocna": ("https://api.tomtom.com/map/1/tile/basic/night/{z}/{x}/{y}.png", 24 * 3600),
    "promet": ("https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png", 120),
    "nesrece": ("https://api.tomtom.com/traffic/map/4/tile/incidents/s3/{z}/{x}/{y}.png", 120),
}


class NiNaVoljo(Exception):
    pass


def kljuc() -> str | None:
    return os.environ.get("TOMTOM_MAP_KEY") or os.environ.get("TOMTOM_API_KEY")


class Ploscice:
    def __init__(self, najvec: int = 4000, odjemalec: httpx.AsyncClient | None = None):
        self._predpomnilnik: collections.OrderedDict[tuple, tuple[float, bytes]] = collections.OrderedDict()
        self._najvec = najvec
        self._odjemalec = odjemalec
        self.napake = 0
        self.izklop_do = 0.0

    def na_voljo(self) -> bool:
        return bool(kljuc()) and time.time() >= self.izklop_do

    @property
    def http(self) -> httpx.AsyncClient:
        if self._odjemalec is None:
            self._odjemalec = httpx.AsyncClient(timeout=4.0)
        return self._odjemalec

    async def ploscica(self, sloj: str, z: int, x: int, y: int) -> bytes:
        if sloj not in SLOJI or not (0 <= z <= 22):
            raise NiNaVoljo("neznan sloj")
        k = (sloj, z, x, y)
        zdaj = time.time()
        if k in self._predpomnilnik:
            potece, vsebina = self._predpomnilnik[k]
            if zdaj < potece:
                self._predpomnilnik.move_to_end(k)
                return vsebina
        if not self.na_voljo():
            if k in self._predpomnilnik:  # ob izpadu je stara ploščica boljša kot nič
                return self._predpomnilnik[k][1]
            raise NiNaVoljo("ni ključa ali je izločen")
        url, ttl = SLOJI[sloj]
        try:
            r = await self.http.get(url.format(z=z, x=x, y=y), params={"key": kljuc(), "tileSize": 256})
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                raise NiNaVoljo(f"HTTP {r.status_code}")
        except (httpx.HTTPError, NiNaVoljo) as e:
            self.napake += 1
            if self.napake >= 5:  # varovalka: 60 s brez klicev
                self.izklop_do, self.napake = zdaj + 60, 0
                dnevnik.error("ploščice karte izločene za 60 s: %s", e)
            if k in self._predpomnilnik:
                return self._predpomnilnik[k][1]
            raise NiNaVoljo(str(e)) from e
        self.napake = 0
        self._predpomnilnik[k] = (zdaj + ttl, r.content)
        if len(self._predpomnilnik) > self._najvec:
            self._predpomnilnik.popitem(last=False)
        return r.content
