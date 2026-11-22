"""HTTP odjemalec za /voznik/* s samodejno osvežitvijo žetona.

Napake so razvrščene, ker se nanje odziva drugače:
- NiPovezave: omrežje, časovna omejitev, 5xx -> poskusi znova kasneje (fiksi ostanejo v vrsti);
- Odjavljen: seja ni več veljavna (nova naprava, nova koda, odjava) -> nazaj na prijavo;
- Zavrnjeno: 4xx z razlogom (npr. 409 prehod ni dovoljen) -> pokaži vozniku.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx


class NiPovezave(Exception):
    pass


class Odjavljen(Exception):
    pass


class Zavrnjeno(Exception):
    def __init__(self, koda: int, sporocilo: str):
        super().__init__(sporocilo)
        self.koda = koda


class Seja:
    """Žetoni na napravi (zasebna mapa aplikacije). Brez kode enote – ta se nikoli ne shrani."""

    def __init__(self, pot: Path | None):
        self.pot = pot
        self.podatki: dict = {}
        if pot and pot.exists():
            try:
                self.podatki = json.loads(pot.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.podatki = {}

    def shrani(self, **kw) -> None:
        self.podatki.update(kw)
        if self.pot:
            self.pot.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.pot.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.podatki), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.pot)

    def pocisti(self) -> None:
        streznik = self.podatki.get("streznik")
        self.podatki = {"streznik": streznik} if streznik else {}
        self.shrani()

    @property
    def prijavljen(self) -> bool:
        return bool(self.podatki.get("osvezitev"))


class Odjemalec:
    def __init__(self, seja: Seja, streznik: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 ura=time.time, rok_s: float = 10.0):
        self.seja, self.ura = seja, ura
        if streznik:
            seja.podatki["streznik"] = streznik.rstrip("/")
        self._transport, self._rok = transport, rok_s
        self._http: httpx.AsyncClient | None = None

    @property
    def streznik(self) -> str:
        return self.seja.podatki.get("streznik", "http://localhost:8000")

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or str(self._http.base_url).rstrip("/") != self.streznik:
            self._http = httpx.AsyncClient(base_url=self.streznik, timeout=self._rok, transport=self._transport)
        return self._http

    async def zapri(self) -> None:
        if self._http:
            await self._http.aclose()

    # ---------- nizko ----------
    async def _poslji(self, metoda: str, pot: str, telo=None, zeton: str | None = None, params=None) -> httpx.Response:
        try:
            return await self.http.request(metoda, pot, json=telo, params=params,
                                           headers={"Authorization": "Bearer " + zeton} if zeton else None)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise NiPovezave(str(e) or type(e).__name__) from e

    @staticmethod
    def _napaka(r: httpx.Response) -> str:
        try:
            j = r.json()
            return str(j.get("napaka") or j.get("detail") or r.status_code)
        except ValueError:
            return str(r.status_code)

    async def _zahteva(self, metoda: str, pot: str, telo=None, params=None):
        if not self.seja.prijavljen:
            raise Odjavljen("ni prijave")
        if self.seja.podatki.get("dostop_potece", 0) - 60 < self.ura():
            await self.osvezi()
        r = await self._poslji(metoda, pot, telo, self.seja.podatki["dostop"], params)
        if r.status_code == 401:
            await self.osvezi()
            r = await self._poslji(metoda, pot, telo, self.seja.podatki["dostop"], params)
        return self._izid(r)

    def _izid(self, r: httpx.Response):
        if r.status_code >= 500:
            raise NiPovezave(f"strežnik {r.status_code}")
        if r.status_code == 401:
            self.seja.pocisti()
            raise Odjavljen(self._napaka(r))
        if r.status_code >= 400:
            raise Zavrnjeno(r.status_code, self._napaka(r))
        return r.json()

    # ---------- identiteta ----------
    async def prijava(self, enota_id: str, koda: str, naprava_id: str) -> dict:
        r = await self._poslji("POST", "/voznik/prijava", {"enota_id": enota_id.strip().upper(), "koda": koda.strip(),
                                                           "naprava_id": naprava_id})
        if r.status_code == 401:
            raise Zavrnjeno(401, self._napaka(r))
        j = self._izid(r)
        self.seja.shrani(dostop=j["dostop"], osvezitev=j["osvezitev"], dostop_potece=j["dostop_potece"],
                         enota=j["enota"], gps=j["gps"])
        return j

    async def osvezi(self) -> None:
        r = await self._poslji("POST", "/voznik/osvezi", {"osvezitev": self.seja.podatki.get("osvezitev", "")})
        if r.status_code in (401, 422):
            self.seja.pocisti()
            raise Odjavljen(self._napaka(r))
        j = self._izid(r)
        self.seja.shrani(dostop=j["dostop"], osvezitev=j["osvezitev"], dostop_potece=j["dostop_potece"])

    async def odjava(self) -> None:
        try:
            await self._zahteva("POST", "/voznik/odjava")
        finally:
            self.seja.pocisti()

    # ---------- naloge ----------
    async def naloga(self) -> dict:
        return await self._zahteva("GET", "/voznik/naloga")

    async def stanje(self, stanje: str, korak: dict | None = None) -> dict:
        telo = {"stanje": stanje}
        if korak:
            telo.update(naloga_id=korak["naloga_id"], akcija=korak["akcija"])
        return await self._zahteva("POST", "/voznik/stanje", telo)

    async def potrdi(self, obvestilo_id: str) -> dict:
        return await self._zahteva("POST", "/voznik/potrdi", {"obvestilo_id": obvestilo_id})

    async def sporocilo(self, koda: str | None = None, besedilo: str | None = None) -> dict:
        return await self._zahteva("POST", "/voznik/sporocilo", {"koda": koda} if koda else {"besedilo": besedilo})

    async def prebrano(self, sporocilo_id: str) -> dict:
        return await self._zahteva("POST", f"/voznik/sporocilo/{sporocilo_id}/prebrano")

    async def okvara(self, opis: str | None = None) -> dict:
        return await self._zahteva("POST", "/voznik/okvara", {"opis": opis})

    async def ovira(self, vrsta: str, opis: str | None = None) -> dict:
        return await self._zahteva("POST", "/voznik/ovira", {"vrsta": vrsta, "opis": opis})

    async def dogodki(self, od: int, zagon: str | None) -> dict:
        return await self._zahteva("GET", "/voznik/dogodki", params={"od": od, **({"zagon": zagon} if zagon else {})})

    async def polozaj(self, fiksi: list[dict], baterija: dict | None = None) -> dict:
        return await self._zahteva("POST", "/voznik/polozaj", {"fiksi": fiksi, "poslano_ob": self.ura(),
                                                               **({"baterija": baterija} if baterija else {})})

    async def heartbeat(self, paket: dict) -> dict:
        return await self._zahteva("POST", "/voznik/heartbeat", paket)

    def ws_url(self) -> str:
        s = self.streznik
        return ("wss://" + s[8:] if s.startswith("https://") else "ws://" + s.removeprefix("http://")) + "/ws/voznik"
