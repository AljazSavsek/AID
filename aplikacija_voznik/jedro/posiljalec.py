"""Pošiljanje GPS: vrsta -> paketi -> POST /voznik/polozaj.

Frekvence določa strežnik (odgovor `gps`), ob izpadu povezave fiksi ostanejo v vrsti in se po
priklopu pošljejo po vrsti (najstarejši najprej), več paketov zapored.
Stanje za prikaz: posiljam | v_vrsti | ni_gps | odjavljen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from .odjemalec import NiPovezave, Odjavljen, Odjemalec, Zavrnjeno
from .vrsta import VrstaFiksov

dnevnik = logging.getLogger("voznik.posiljalec")


class Posiljalec:
    def __init__(self, odjemalec: Odjemalec, vrsta: VrstaFiksov, ob_spremembi: Callable[[dict], None] | None = None,
                 velikost_paketa: int = 50, ura=time.time):
        self.o, self.vrsta, self.ura = odjemalec, vrsta, ura
        self.ob_spremembi = ob_spremembi or (lambda s: None)
        self.velikost_paketa = velikost_paketa
        self.gps = dict(odjemalec.seja.podatki.get("gps") or {"fiks_s": 30, "posiljanje_s": 60, "nacin": "prosto"})
        self.baterija: dict | None = None
        self.zadnji_uspeh: float | None = None
        self.zadnji_fiks: float | None = None
        self.stanje = "v_vrsti"
        self.napaka: str | None = None
        self._zbudi = asyncio.Event()
        self._tece = False

    def nov_fiks(self, fiks: dict) -> None:
        self.vrsta.dodaj(fiks)
        self.zadnji_fiks = self.ura()

    def zbudi(self) -> None:
        """Takoj pošlji (npr. ob spremembi stanja ali vrnitvi povezave)."""
        self._zbudi.set()

    def povzetek(self) -> dict:
        return {"stanje": self.stanje, "v_vrsti": len(self.vrsta), "zadnji_uspeh": self.zadnji_uspeh,
                "zadnji_fiks": self.zadnji_fiks, "gps": self.gps, "napaka": self.napaka, "zavrzeni": self.vrsta.zavrzeni}

    def _sporoci(self, stanje: str, napaka: str | None = None) -> None:
        self.stanje, self.napaka = stanje, napaka
        self.ob_spremembi(self.povzetek())

    async def poslji_vse(self, najvec_paketov: int = 20) -> bool:
        """Pošlje do `najvec_paketov` paketov. Vrne True, ko je vrsta prazna."""
        for _ in range(najvec_paketov):
            paket = self.vrsta.paket(self.velikost_paketa)
            if not paket:
                self._sporoci("posiljam")
                return True
            try:
                r = await self.o.polozaj(paket, self.baterija)
            except Zavrnjeno as e:
                if e.koda == 422:
                    # neveljaven paket bi zamašil vrsto -> zavrži ga
                    dnevnik.warning("paket zavrnjen (%s) – zavržen", e)
                    self.vrsta.potrdi_do(paket[-1]["seq"])
                    continue
                self._sporoci("v_vrsti", str(e))
                return False
            self.vrsta.potrdi_do(r.get("potrjeno_do_seq"))
            self.zadnji_uspeh = self.ura()
            if r.get("gps"):
                self.gps = r["gps"]
                self.o.seja.podatki["gps"] = r["gps"]
        self._sporoci("v_vrsti" if len(self.vrsta) else "posiljam")
        return not len(self.vrsta)

    async def tek(self) -> None:
        self._tece = True
        zamik = 1.0
        while self._tece:
            try:
                await self.poslji_vse()
                zamik = 1.0
                cakaj = float(self.gps.get("posiljanje_s", 5))
            except NiPovezave as e:
                self._sporoci("v_vrsti", str(e))
                zamik = min(zamik * 2, max(5.0, float(self.gps.get("posiljanje_s", 5))))
                cakaj = zamik
            except Odjavljen as e:
                self._sporoci("odjavljen", str(e))
                self._tece = False
                return
            except Exception as e:  # noqa: BLE001
                dnevnik.exception("pošiljanje")
                self._sporoci("v_vrsti", str(e))
                cakaj = 5.0
            self._zbudi.clear()
            try:
                await asyncio.wait_for(self._zbudi.wait(), cakaj)
            except asyncio.TimeoutError:
                pass

    def ustavi(self) -> None:
        self._tece = False
        self._zbudi.set()
