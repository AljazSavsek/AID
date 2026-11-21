"""Skrit, redundanten usmerjevalni servis (specifikacija 7).

izracunaj(od, do, tip_enote) -> {cas_s, razdalja_m, geometrija, kakovost}
matrika(izvori, cilji, tip_enote) -> [[{...}]]

- hedging: primarni ne odgovori v 1,5 s -> vzporedno rezerva; prvi veljaven odgovor zmaga
- skupni rok 4 s, nato zračna ocena
- circuit breaker: 3 zaporedne napake -> izločen 60 s, nato poskusni klic
- health check vsakih 30 s
- predpomnilnik (zaokrožene koordinate + tip + 5-min okno)
- preverjanje smiselnosti: razlika > 40 % -> konzervativnejši čas + opozorilo v interni dnevnik
Ime ponudnika gre samo v `usmerjanje.log` in `zdravje()`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from .ponudniki import NiIzpad, Pot, Ponudnik, helikopter, privzeti_ponudniki, zracna_ocena, zracna_razdalja_m

dnevnik = logging.getLogger("usmerjanje")


def nastavi_dnevnik(pot: str = "usmerjanje.log") -> None:
    if any(isinstance(h, logging.FileHandler) for h in dnevnik.handlers):
        return
    h = logging.FileHandler(pot, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    dnevnik.addHandler(h)
    dnevnik.setLevel(logging.INFO)
    dnevnik.propagate = False


@dataclass
class Varovalka:
    """Circuit breaker za enega ponudnika."""
    prag: int = 3
    izklop_s: float = 60.0
    napake: int = 0
    odprta_do: float = 0.0
    zadnja_napaka: str | None = None
    zadnji_uspeh: float | None = None
    poskusni: bool = False

    def dovoli(self, zdaj: float) -> bool:
        if self.odprta_do == 0.0:
            return True
        if zdaj >= self.odprta_do and not self.poskusni:
            self.poskusni = True  # pol-odprto: en poskusni klic
            return True
        return False

    def uspeh(self, zdaj: float) -> None:
        self.napake, self.odprta_do, self.poskusni, self.zadnji_uspeh = 0, 0.0, False, zdaj

    def napaka(self, zdaj: float, opis: str) -> bool:
        """Vrne True, če se je varovalka pravkar odprla."""
        self.zadnja_napaka = opis
        self.napake += 1
        if self.poskusni or self.napake >= self.prag:
            self.odprta_do, self.poskusni = zdaj + self.izklop_s, False
            return True
        return False

    @property
    def stanje(self) -> str:
        if self.odprta_do == 0.0:
            return "zaprta"
        return "pol-odprta" if time.time() >= self.odprta_do else "odprta"


@dataclass
class UsmerjevalniServis:
    ponudniki: list[Ponudnik] = field(default_factory=privzeti_ponudniki)
    hedge_s: float = 1.5
    rok_s: float = 4.0
    prag_smiselnosti: float = 0.40
    okno_predpomnilnika_s: int = 300
    ura: callable = time.time

    vzporedno: int = 4

    def __post_init__(self):
        self._mesta = None
        self.varovalke = {p.ime: Varovalka() for p in self.ponudniki}
        self._predpomnilnik: dict[tuple, Pot] = {}
        self.zdravje_stanje: dict[str, dict] = {}

    # ---------- javni vmesnik ----------
    async def izracunaj(self, od, do, tip_enote: str = "vozilo", izogibaj: list | None = None) -> dict:
        """izogibaj: območja (jug, zahod, sever, vzhod), npr. ovire na cesti."""
        iz = tuple(tuple(round(x, 5) for x in r) for r in (izogibaj or ()))
        return (await self._izracunaj(tuple(od), tuple(do), tip_enote, iz)).javno()

    def iz_predpomnilnika(self, od, do, tip_enote: str = "vozilo") -> dict | None:
        """Samo že izračunani časi (brez klica ponudnika)."""
        p = self._predpomnilnik.get(self._kljuc(tuple(od), tuple(do), tip_enote)) or self._iz_predpomnilnika_brez_okna(tuple(od), tuple(do), tip_enote)
        return p.javno() if p else None

    async def matrika(self, izvori: list, cilji: list, tip_enote: str = "vozilo", vzporedno: int = 8) -> list[list[dict]]:
        sem = asyncio.Semaphore(vzporedno)

        async def ena(a, b):
            async with sem:
                return await self.izracunaj(a, b, tip_enote)

        naloge = [[ena(a, b) for b in cilji] for a in izvori]
        return [list(await asyncio.gather(*vrsta)) for vrsta in naloge]

    def javna_kakovost(self) -> str:
        """Najboljša trenutno dosegljiva kakovost – brez imena ponudnika (za vmesnik)."""
        zdaj = self.ura()
        for p in self.ponudniki:
            v = self.varovalke[p.ime]
            if p.na_voljo() and (v.odprta_do == 0.0 or zdaj >= v.odprta_do):
                return p.kakovost
        return "ocena"

    def zdravje(self) -> dict:
        """SAMO za skrbnika (/interno/zdravje)."""
        return {
            p.ime: {
                "konfiguriran": p.na_voljo(),
                "varovalka": self.varovalke[p.ime].stanje,
                "zaporedne_napake": self.varovalke[p.ime].napake,
                "zadnja_napaka": self.varovalke[p.ime].zadnja_napaka,
                "zadnji_uspeh": self.varovalke[p.ime].zadnji_uspeh,
                **self.zdravje_stanje.get(p.ime, {}),
            }
            for p in self.ponudniki
        } | {"predpomnilnik": len(self._predpomnilnik)}

    async def preveri_zdravje(self) -> None:
        test_od, test_do = (46.0546, 14.5222), (46.0569, 14.5058)  # UKC -> center Ljubljane
        for p in self.ponudniki:
            if not p.na_voljo():
                self.zdravje_stanje[p.ime] = {"zdravje": "ni konfiguriran"}
                continue
            t0 = time.perf_counter()
            try:
                await asyncio.wait_for(p.izracunaj(test_od, test_do, "vozilo"), self.rok_s)
                self.zdravje_stanje[p.ime] = {"zdravje": "ok", "odziv_ms": round((time.perf_counter() - t0) * 1000)}
            except Exception as e:  # noqa: BLE001
                self.zdravje_stanje[p.ime] = {"zdravje": "napaka", "opis": str(e)[:200]}
                dnevnik.warning("health check %s: %s", p.ime, e)

    async def zanka_zdravja(self, interval_s: float = 30.0) -> None:
        while True:
            try:
                await self.preveri_zdravje()
            except Exception:  # noqa: BLE001
                dnevnik.exception("health check")
            await asyncio.sleep(interval_s)

    # ---------- notranjost ----------
    def _kljuc(self, od, do, tip, iz=()):
        okno = int(self.ura() // self.okno_predpomnilnika_s)
        return (round(od[0], 4), round(od[1], 4), round(do[0], 4), round(do[1], 4), tip, iz, okno)

    def _iz_predpomnilnika_brez_okna(self, od, do, tip):
        """Ob popolnem izpadu je stara vrednost boljša od zračne ocene."""
        osnova = (round(od[0], 4), round(od[1], 4), round(do[0], 4), round(do[1], 4), tip, ())
        kandidati = [(k[-1], v) for k, v in self._predpomnilnik.items() if k[:-1] == osnova]
        return max(kandidati, key=lambda x: x[0])[1] if kandidati else None

    async def _izracunaj(self, od, do, tip, iz=()) -> Pot:
        if tip == "helikopter":
            return helikopter(od, do)
        if zracna_razdalja_m(od, do) < 30:  # enota je že na cilju
            return Pot(0.0, 0.0, [list(od), list(do)], self._razpolozljivi()[0].kakovost if self._razpolozljivi() else "ocena")
        kljuc = self._kljuc(od, do, tip, iz)
        if kljuc in self._predpomnilnik:
            return self._predpomnilnik[kljuc]
        if self._mesta is None:
            self._mesta = asyncio.Semaphore(self.vzporedno)
        async with self._mesta:
            if kljuc in self._predpomnilnik:  # medtem izračunal nekdo drug
                return self._predpomnilnik[kljuc]
            pot = await self._hedging(od, do, tip, iz)
        if pot is None:
            star = None if iz else self._iz_predpomnilnika_brez_okna(od, do, tip)
            if star is not None:
                dnevnik.warning("vsi ponudniki nedosegljivi – uporabljen star predpomnilnik")
                return Pot(star.cas_s, star.razdalja_m, star.geometrija, "ocena")
            if any(p.na_voljo() for p in self.ponudniki):
                dnevnik.warning("vsi ponudniki nedosegljivi – zračna ocena")
            else:
                dnevnik.debug("noben ponudnik ni nastavljen – zračna ocena")
            return zracna_ocena(od, do, tip)
        self._predpomnilnik[kljuc] = pot
        if len(self._predpomnilnik) > 20000:
            self._predpomnilnik.pop(next(iter(self._predpomnilnik)))
        return pot

    def _razpolozljivi(self, iz=()) -> list[Ponudnik]:
        zdaj = self.ura()
        return [p for p in self.ponudniki if p.na_voljo() and (not iz or p.podpira_izogibanje)
                and self.varovalke[p.ime].dovoli(zdaj)]

    async def _klic(self, p: Ponudnik, od, do, tip, iz=()) -> Pot:
        try:
            pot = await (p.izracunaj(od, do, tip, izogibaj=list(iz)) if iz else p.izracunaj(od, do, tip))
            pot.izogiba = bool(iz)
            if not (pot.cas_s >= 0 and pot.razdalja_m >= 0):
                raise ValueError("neveljaven odgovor")
            self.varovalke[p.ime].uspeh(self.ura())
            return pot
        except asyncio.CancelledError:
            raise
        except NiIzpad as e:
            dnevnik.warning("ponudnik %s: %s (ne šteje kot izpad)", p.ime, e)
            raise
        except Exception as e:  # noqa: BLE001
            if self.varovalke[p.ime].napaka(self.ura(), str(e)[:200]):
                dnevnik.error("ponudnik %s izločen za %ss (%s)", p.ime, self.varovalke[p.ime].izklop_s, e)
            else:
                dnevnik.warning("ponudnik %s napaka: %s", p.ime, e)
            raise

    async def _hedging(self, od, do, tip, iz=()) -> Pot | None:
        vrsta = self._razpolozljivi(iz)
        if not vrsta:
            return None
        konec = asyncio.get_running_loop().time() + self.rok_s
        tekoci: dict[asyncio.Task, Ponudnik] = {}
        rezultati: list[tuple[Ponudnik, Pot]] = []

        def zazeni_naslednjega():
            if vrsta:
                p = vrsta.pop(0)
                tekoci[asyncio.ensure_future(self._klic(p, od, do, tip, iz))] = p
                if len(tekoci) > 1 or p is not self.ponudniki[0]:
                    dnevnik.info("hedging: vzporedno kličem %s", p.ime)

        zazeni_naslednjega()
        naslednji_hedge = asyncio.get_running_loop().time() + self.hedge_s
        try:
            while tekoci and not rezultati:
                zdaj = asyncio.get_running_loop().time()
                if zdaj >= konec:
                    dnevnik.warning("skupni rok %ss presežen", self.rok_s)
                    break
                cakaj = min(konec, naslednji_hedge if vrsta else konec) - zdaj
                koncani, _ = await asyncio.wait(list(tekoci), timeout=max(0.0, cakaj), return_when=asyncio.FIRST_COMPLETED)
                for t in koncani:
                    p = tekoci.pop(t)
                    if t.exception() is None:
                        rezultati.append((p, t.result()))
                    else:
                        zazeni_naslednjega()  # napaka -> takoj naslednji
                if not koncani and vrsta and asyncio.get_running_loop().time() >= naslednji_hedge:
                    zazeni_naslednjega()
                    naslednji_hedge = asyncio.get_running_loop().time() + self.hedge_s
                if not tekoci and vrsta and not rezultati:
                    zazeni_naslednjega()
            # preverjanje smiselnosti: kratko počakaj še na tekoče
            if rezultati and tekoci:
                koncani, _ = await asyncio.wait(list(tekoci), timeout=0.2)
                for t in koncani:
                    p = tekoci.pop(t)
                    if t.exception() is None:
                        rezultati.append((p, t.result()))
        finally:
            for t in tekoci:
                t.cancel()
        if not rezultati:
            return None
        if len(rezultati) >= 2:
            (pa, a), (pb, b) = rezultati[0], rezultati[1]
            krajsi, daljsi = sorted((a.cas_s, b.cas_s))
            if krajsi > 0 and (daljsi - krajsi) / krajsi > self.prag_smiselnosti:
                dnevnik.warning("smiselnost: %s=%.0fs, %s=%.0fs (>%d %%) – uporabljen daljši",
                                pa.ime, a.cas_s, pb.ime, b.cas_s, self.prag_smiselnosti * 100)
                return a if a.cas_s >= b.cas_s else b
        return rezultati[0][1]


_privzeti: UsmerjevalniServis | None = None


def servis() -> UsmerjevalniServis:
    global _privzeti
    if _privzeti is None:
        nastavi_dnevnik(os.environ.get("AID_USMERJANJE_LOG", "usmerjanje.log"))
        _privzeti = UsmerjevalniServis()
    return _privzeti
