"""Ponudniki poti. TO JE EDINO MESTO (poleg internega dnevnika in /interno/zdravje),
kjer se pojavijo imena ponudnikov. Navzven gre le `Pot` brez imena (pravilo 2.4).

Ključi so samo v okoljskih spremenljivkah (pravilo 2.7).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import httpx

KAKOVOST_PROMET = "natančno"
KAKOVOST_BREZ_PROMETA = "brez prometa"
KAKOVOST_OCENA = "ocena"

# cestna vozila: hitrost glede na tip enote (motorist je v mestu hitrejši)
FAKTOR_TIPA = {"vozilo": 1.0, "motorist": 0.9}


class NapakaPonudnika(Exception):
    pass


class NiIzpad(NapakaPonudnika):
    """Napaka za to zahtevo, ponudnik pa deluje (ne šteje v varovalko)."""


class Omejitev(NiIzpad):
    """Ponudnik je zavrnil zaradi omejitve hitrosti (HTTP 429) – ni izpad."""


class NiPoti(NiIzpad):
    """Za ti točki ponudnik ne najde poti (npr. točka ni ob cesti) – ni izpad."""


class Omejevalnik:
    """Največ N hkratnih zahtev in največ Q zahtev na sekundo za enega ponudnika."""

    def __init__(self, hkrati: int, na_sekundo: float):
        self.hkrati, self.interval = hkrati, 1.0 / max(0.1, na_sekundo)
        self._sem = None
        self._naslednji = 0.0
        self._lock = None

    async def __aenter__(self):
        import asyncio
        if self._sem is None:
            self._sem, self._lock = asyncio.Semaphore(self.hkrati), asyncio.Lock()
        await self._sem.acquire()
        async with self._lock:
            zanka = asyncio.get_running_loop()
            cakaj = self._naslednji - zanka.time()
            if cakaj > 0:
                await asyncio.sleep(cakaj)
            self._naslednji = max(zanka.time(), self._naslednji) + self.interval
        return self

    async def __aexit__(self, *_):
        self._sem.release()


@dataclass
class Pot:
    cas_s: float
    razdalja_m: float
    geometrija: list = field(default_factory=list)  # [[lat, lon], ...]
    kakovost: str = KAKOVOST_OCENA
    navodila: list = field(default_factory=list)    # [{sporocilo, odmik_m, ulica, manever, lat, lon}]
    opozorila: list = field(default_factory=list)   # [{vrsta, od_m, do_m, zamuda_s, hitrost_kmh, lat, lon}]
    zamuda_prometa_s: float = 0.0
    izogiba: bool = False   # pot se izogiba aktivnim oviram

    def javno(self) -> dict:
        """Edina oblika, ki zapusti servis (brez imena ponudnika)."""
        return {"cas_s": round(self.cas_s), "razdalja_m": round(self.razdalja_m),
                "geometrija": self.geometrija, "kakovost": self.kakovost, "izogiba_oviram": self.izogiba,
                "navodila": self.navodila, "opozorila": self.opozorila,
                "zamuda_prometa_s": round(self.zamuda_prometa_s)}


def zracna_razdalja_m(a: tuple, b: tuple) -> float:
    r = math.pi / 180
    dlat, dlon = (b[0] - a[0]) * r, (b[1] - a[1]) * r
    x = math.sin(dlat / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(x))


class Ponudnik:
    ime = "ponudnik"
    kakovost = KAKOVOST_BREZ_PROMETA
    podpira_izogibanje = False  # izogibanje območjem (ovire na cesti)

    def na_voljo(self) -> bool:
        return True

    async def izracunaj(self, od: tuple, do: tuple, tip_enote: str) -> Pot:  # pragma: no cover
        raise NotImplementedError


class _HttpPonudnik(Ponudnik):
    def __init__(self, odjemalec: httpx.AsyncClient | None = None):
        self._odjemalec = odjemalec

    @property
    def http(self) -> httpx.AsyncClient:
        if self._odjemalec is None:
            self._odjemalec = httpx.AsyncClient(timeout=4.0)
        return self._odjemalec


class TomTom(_HttpPonudnik):
    ime = "tomtom"
    kakovost = KAKOVOST_PROMET

    def na_voljo(self) -> bool:
        return bool(os.environ.get("TOMTOM_API_KEY"))

    omejevalnik = Omejevalnik(int(os.environ.get("AID_TOMTOM_HKRATI", 3)), float(os.environ.get("AID_TOMTOM_QPS", 5)))

    podpira_izogibanje = True

    async def izracunaj(self, od, do, tip_enote, izogibaj=None):
        import asyncio
        kljuc = os.environ.get("TOMTOM_API_KEY")
        if not kljuc:
            raise NapakaPonudnika("ni ključa")
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{od[0]},{od[1]}:{do[0]},{do[1]}/json"
        for poskus in (1, 2):
            parametri = {"key": kljuc, "traffic": "true", "routeType": "fastest", "travelMode": "car",
                         "sectionType": "traffic", "instructionsType": "text", "language": "sl-SI"}
            async with self.omejevalnik:
                if izogibaj:  # ovire: pravokotniki (jug, zahod, sever, vzhod)
                    telo = {"avoidAreas": {"rectangles": [
                        {"southWestCorner": {"latitude": j, "longitude": z}, "northEastCorner": {"latitude": s_, "longitude": v}}
                        for j, z, s_, v in izogibaj]}}
                    r = await self.http.post(url, params=parametri, json=telo)
                else:
                    r = await self.http.get(url, params=parametri)
            if r.status_code == 429 or (r.status_code == 403 and "rate" in r.text.lower()):
                if poskus == 1:
                    await asyncio.sleep(min(1.0, float(r.headers.get("retry-after", 0.5) or 0.5)))
                    continue
                raise Omejitev("omejitev hitrosti")
            break
        if r.status_code == 400 and "NO_ROUTE_FOUND" in r.text:
            raise NiPoti("ni poti med točkama")
        if r.status_code == 400:
            raise NiIzpad(f"HTTP 400: {r.text[:120]}")
        if r.status_code != 200:
            raise NapakaPonudnika(f"HTTP {r.status_code}: {r.text[:120]}")
        route = r.json()["routes"][0]
        s = route["summary"]
        tocke = [[p["latitude"], p["longitude"]] for leg in route.get("legs", []) for p in leg.get("points", [])]
        return Pot(s["travelTimeInSeconds"] * FAKTOR_TIPA.get(tip_enote, 1.0), s["lengthInMeters"], tocke, self.kakovost,
                   navodila=_navodila(route), opozorila=_opozorila(route, tocke),
                   zamuda_prometa_s=float(s.get("trafficDelayInSeconds", 0)))


VRSTE_PROMETA = {"JAM": "zastoj", "ROAD_WORK": "dela na cesti", "ROAD_CLOSURE": "zaprta cesta", "OTHER": "promet"}


def _navodila(route: dict) -> list[dict]:
    out = []
    for i in route.get("guidance", {}).get("instructions", []):
        t = i.get("point", {})
        out.append({"sporocilo": i.get("message", ""), "odmik_m": i.get("routeOffsetInMeters", 0), "ulica": i.get("street"),
                    "manever": i.get("maneuver"), "lat": t.get("latitude"), "lon": t.get("longitude")})
    return out


def _opozorila(route: dict, tocke: list) -> list[dict]:
    kum = [0.0]
    for a, b in zip(tocke, tocke[1:]):
        kum.append(kum[-1] + zracna_razdalja_m(a, b))
    out = []
    for sec in route.get("sections", []):
        if sec.get("sectionType") != "TRAFFIC":
            continue
        i, j = sec.get("startPointIndex", 0), sec.get("endPointIndex", 0)
        i, j = min(i, len(tocke) - 1), min(j, len(tocke) - 1)
        out.append({"vrsta": VRSTE_PROMETA.get(sec.get("simpleCategory"), "promet"), "od_m": round(kum[i]), "do_m": round(kum[j]),
                    "zamuda_s": sec.get("delayInSeconds", 0), "hitrost_kmh": sec.get("effectiveSpeedInKmh"),
                    "lat": tocke[i][0] if tocke else None, "lon": tocke[i][1] if tocke else None})
    return out


class OpenRouteService(_HttpPonudnik):
    ime = "ors"
    kakovost = KAKOVOST_BREZ_PROMETA

    def na_voljo(self) -> bool:
        return bool(os.environ.get("ORS_API_KEY"))

    podpira_izogibanje = True

    async def izracunaj(self, od, do, tip_enote, izogibaj=None):
        kljuc = os.environ.get("ORS_API_KEY")
        if not kljuc:
            raise NapakaPonudnika("ni ključa")
        r = await self.http.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            headers={"Authorization": kljuc},
            json={"coordinates": [[od[1], od[0]], [do[1], do[0]]],
                  **({"options": {"avoid_polygons": {"type": "MultiPolygon", "coordinates": [
                      [[[z, j], [v, j], [v, s_], [z, s_], [z, j]]] for j, z, s_, v in izogibaj]}}} if izogibaj else {})},
        )
        if r.status_code != 200:
            raise NapakaPonudnika(f"HTTP {r.status_code}")
        f = r.json()["features"][0]
        s = f["properties"]["summary"]
        tocke = [[c[1], c[0]] for c in f["geometry"]["coordinates"]]
        return Pot(s["duration"] * FAKTOR_TIPA.get(tip_enote, 1.0), s["distance"], tocke, self.kakovost)


class Osrm(_HttpPonudnik):
    ime = "osrm"
    kakovost = KAKOVOST_BREZ_PROMETA

    def na_voljo(self) -> bool:
        return bool(os.environ.get("OSRM_URL"))

    async def izracunaj(self, od, do, tip_enote):
        osnova = os.environ.get("OSRM_URL")
        if not osnova:
            raise NapakaPonudnika("ni naslova strežnika")
        r = await self.http.get(f"{osnova.rstrip('/')}/route/v1/driving/{od[1]},{od[0]};{do[1]},{do[0]}",
                                params={"overview": "full", "geometries": "geojson"})
        if r.status_code != 200:
            raise NapakaPonudnika(f"HTTP {r.status_code}")
        route = r.json()["routes"][0]
        tocke = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        return Pot(route["duration"] * FAKTOR_TIPA.get(tip_enote, 1.0), route["distance"], tocke, self.kakovost)


def zracna_ocena(od, do, tip_enote: str, hitrost_kmh: float = 45.0, faktor_ceste: float = 1.35) -> Pot:
    """Zadnja rezerva: vedno odgovori, tudi brez interneta."""
    d = zracna_razdalja_m(od, do) * faktor_ceste
    v = hitrost_kmh / 3.6 / FAKTOR_TIPA.get(tip_enote, 1.0)
    return Pot(d / v, d, [list(od), list(do)], KAKOVOST_OCENA)


def helikopter(od, do, hitrost_kmh: float | None = None, vzlet_pristanek_min: float | None = None) -> Pot:
    """Zračna razdalja / 200 km/h + 5 min (nastavljivo). Ne kliče cestnih API-jev."""
    hitrost = hitrost_kmh or float(os.environ.get("AID_HELI_KMH", 200))
    dodatek = vzlet_pristanek_min if vzlet_pristanek_min is not None else float(os.environ.get("AID_HELI_DODATEK_MIN", 5))
    d = zracna_razdalja_m(od, do)
    return Pot(d / (hitrost / 3.6) + dodatek * 60, d, [list(od), list(do)], KAKOVOST_OCENA)


def privzeti_ponudniki() -> list[Ponudnik]:
    """Vrstni red = prioriteta (primarni s prometom, nato rezerve)."""
    return [TomTom(), OpenRouteService(), Osrm()]
