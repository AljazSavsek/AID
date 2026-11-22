"""Strežniški posrednik za ploščice karte (osnovna karta in sloj prometa).

- ključ ostane na strežniku (pravilo 2.7): brskalnik kliče /ploscice/..., ne ponudnika
- odgovor ne vsebuje imena ponudnika (pravilo 2.4): pošljemo samo slikovne bajte
- predpomnilnik v pomnilniku: osnovna karta 24 h, promet 2 min
- brez ključa ali ob napaki vmesnik uporabi brezplačno podlago (OSM/CARTO)

Ključ: TOMTOM_MAP_KEY (Map Display + Traffic API), sicer TOMTOM_API_KEY.
"""
from __future__ import annotations

import asyncio
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


class Omejitev(NiNaVoljo):
    """Ponudnik je zavrnil zaradi preveč zahtevkov (429) – ni izpad, sloja ne izklopi; brskalnik poskusi znova."""


def kljuc() -> str | None:
    return os.environ.get("TOMTOM_MAP_KEY") or os.environ.get("TOMTOM_API_KEY")


class Ploscice:
    def __init__(self, najvec: int = 6000, odjemalec: httpx.AsyncClient | None = None,
                 hkrati: int | None = None, qps: float | None = None):
        self._predpomnilnik: collections.OrderedDict[tuple, tuple[float, bytes]] = collections.OrderedDict()
        self._najvec = najvec
        self._odjemalec = odjemalec
        self.napake: dict[str, int] = {}
        self.izklop_do: dict[str, float] = {}
        # do ponudnika: največ `hkrati` zahtevkov naenkrat in `qps` na sekundo (sicer ponudnik vrne 429)
        self._hkrati = hkrati or int(os.environ.get("AID_PLOSCICE_HKRATI", 6))
        self._razmik = 1.0 / float(qps or os.environ.get("AID_PLOSCICE_QPS", 20))
        self._mesta: asyncio.Semaphore | None = None
        self._naslednji = 0.0
        self._v_teku: dict[tuple, asyncio.Future] = {}  # isto ploščico zahteva več odjemalcev -> en klic
        self._omejen_do = 0.0
        self.stevec = {"predpomnilnik": 0, "ponudnik": 0, "omejitev": 0, "napaka": 0}

    def iz_predpomnilnika(self, sloj: str, z: int, x: int, y: int) -> bytes | None:
        """Sveža ploščica iz pomnilnika (brez klica ponudnika in brez štetja v omejitev)."""
        v = self._predpomnilnik.get((sloj, z, x, y))
        if v and time.time() < v[0]:
            self._predpomnilnik.move_to_end((sloj, z, x, y))
            self.stevec["predpomnilnik"] += 1
            return v[1]
        return None

    def na_voljo(self, sloj: str = "karta") -> bool:
        """Vsak sloj ima svojo varovalko (napaka prometa ne izklopi osnovne karte)."""
        return bool(kljuc()) and time.time() >= self.izklop_do.get(sloj, 0.0)

    @property
    def http(self) -> httpx.AsyncClient:
        if self._odjemalec is None:
            self._odjemalec = httpx.AsyncClient(timeout=4.0)
        return self._odjemalec

    async def ploscica(self, sloj: str, z: int, x: int, y: int) -> bytes:
        if sloj not in SLOJI or not (0 <= z <= 22):
            raise NiNaVoljo("neznan sloj")
        k = (sloj, z, x, y)
        sveza = self.iz_predpomnilnika(*k)
        if sveza is not None:
            return sveza
        if k in self._v_teku:  # isto ploščico že nalaga drug zahtevek
            return await asyncio.shield(self._v_teku[k])
        f = asyncio.get_running_loop().create_future()
        self._v_teku[k] = f
        try:
            vsebina = await self._prenesi(k)
            f.set_result(vsebina)
            return vsebina
        except BaseException as e:
            f.set_exception(e)
            f.exception()  # da asyncio ne opozarja, če nihče drug ne čaka
            raise
        finally:
            self._v_teku.pop(k, None)

    def _stara(self, k):
        return self._predpomnilnik[k][1] if k in self._predpomnilnik else None

    async def _prenesi(self, k: tuple) -> bytes:
        sloj, z, x, y = k
        zdaj = time.time()
        if not self.na_voljo(sloj) or zdaj < self._omejen_do:
            stara = self._stara(k)  # ob izpadu ali omejitvi je stara ploščica boljša kot nič
            if stara is not None:
                return stara
            if zdaj < self._omejen_do:
                raise Omejitev("ponudnik omejuje – poskusi znova")
            raise NiNaVoljo("ni ključa ali je izločen")
        if self._mesta is None:
            self._mesta = asyncio.Semaphore(self._hkrati)
        url, ttl = SLOJI[sloj]
        async with self._mesta:
            cakaj = self._naslednji - time.monotonic()  # enakomerno razporejeni zahtevki (ne v sunkih)
            self._naslednji = max(time.monotonic(), self._naslednji) + self._razmik
            if cakaj > 0:
                await asyncio.sleep(cakaj)
            try:
                r = await self.http.get(url.format(z=z, x=x, y=y), params={"key": kljuc(), "tileSize": 256})
            except httpx.HTTPError as e:
                return self._napaka(k, f"{type(e).__name__}")
        if r.status_code == 429 or (r.status_code == 403 and "rate" in r.text.lower()[:500]):
            self.stevec["omejitev"] += 1
            self._omejen_do = time.time() + 2.0  # kratek premor za vse sloje; ne šteje kot izpad
            dnevnik.warning("ponudnik ploščic omejuje (%s) – 2 s premora", r.status_code)
            stara = self._stara(k)
            if stara is not None:
                return stara
            raise Omejitev("ponudnik omejuje – poskusi znova")
        if r.status_code == 404:  # ploščice ni (npr. rob karte) – ni izpad
            raise NiNaVoljo("HTTP 404")
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return self._napaka(k, f"HTTP {r.status_code}")
        self.napake[sloj] = 0
        self.stevec["ponudnik"] += 1
        self._predpomnilnik[k] = (time.time() + ttl, r.content)
        self._predpomnilnik.move_to_end(k)
        while len(self._predpomnilnik) > self._najvec:
            self._predpomnilnik.popitem(last=False)
        return r.content

    def _napaka(self, k: tuple, opis: str) -> bytes:
        """Prava napaka (5xx, časovna omejitev, 401/403). 401/403 = API ni vklopljen -> sloj izklopi za 10 min;
        sicer po 8 zaporednih napakah za 30 s. Stara ploščica ima prednost pred napako."""
        sloj = k[0]
        self.stevec["napaka"] += 1
        dovoljenje = opis in ("HTTP 401", "HTTP 403")
        self.napake[sloj] = self.napake.get(sloj, 0) + 1
        if dovoljenje or self.napake[sloj] >= 8:
            trajanje = 600 if dovoljenje else 30
            self.izklop_do[sloj], self.napake[sloj] = time.time() + trajanje, 0
            dnevnik.error("sloj %s izločen za %d s: %s", sloj, trajanje, opis)
        stara = self._stara(k)
        if stara is not None:
            return stara
        raise NiNaVoljo(opis)
