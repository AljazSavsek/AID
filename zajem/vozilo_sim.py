"""Simulator vozil (brez strojne opreme): vsako vozilo je samostojen odjemalec API voznika in uporablja
ISTO jedro kot aplikacija Flet (odjemalec z žetoni, trajna vrsta fiksov, pošiljanje, simuliran GPS).

Vozilo se prijavi s kodo enote, potrdi prejem plana, vozi po POTRJENI poti (geometrija iz /voznik/naloga),
pošilja GPS s frekvenco, ki jo določi strežnik, in sporoča stanja: prejeto -> na poti -> na prizorišču ->
pacient naložen / dostavljeno / opravljeno. Tako se celoten krog (klic -> plan -> potrditev -> vožnja ->
opravljeno) pokaže brez telefona.

    python -m zajem.vozilo_sim --url http://localhost:8000 --enote RV-1,RV-4
    python -m zajem.vozilo_sim --enote RV-1 --izpad 30:45        # 45 s brez omrežja po 30 s (vrsta brez povezave)
    python -m zajem.vozilo_sim --enote RV-1,RV-4 --ne-potrdi RV-4 # eskalacija pri dispečerju

Pospešek (--pospesek) skrajša postanke in pospeši vožnjo, vendar hitrost ostane pod mejo strežnika
za nemogoče skoke (privzeto 200 km/h); časi fiksov so vedno pravi (strežnik zavrne prihodnost).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

import httpx

KOREN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOREN / "aplikacija_voznik"))

from jedro.gps import SimuliranVir  # noqa: E402
from jedro.navigacija import razdalja_m  # noqa: E402
from jedro.odjemalec import NiPovezave, Odjavljen, Odjemalec, Seja, Zavrnjeno  # noqa: E402
from jedro.posiljalec import Posiljalec  # noqa: E402
from jedro.vrsta import VrstaFiksov  # noqa: E402

dnevnik = logging.getLogger("vozilo_sim")


class Izpad(httpx.AsyncBaseTransport):
    """Transport z načrtovanim izpadom omrežja (od, trajanje v s od zagona)."""

    def __init__(self, notranji: httpx.AsyncBaseTransport, od_s: float | None = None, trajanje_s: float = 0.0, ura=time.time):
        self.notranji, self.ura = notranji, ura
        self.t0 = ura()
        self.od_s, self.trajanje_s = od_s, trajanje_s

    @property
    def odklopljen(self) -> bool:
        if self.od_s is None:
            return False
        t = self.ura() - self.t0
        return self.od_s <= t < self.od_s + self.trajanje_s

    async def handle_async_request(self, request):
        if self.odklopljen:
            raise httpx.ConnectError("simuliran izpad omrežja", request=request)
        return await self.notranji.handle_async_request(request)


class SimVozilo:
    def __init__(self, enota_id: str, koda: str, url: str = "http://localhost:8000",
                 transport: httpx.AsyncBaseTransport | None = None, pospesek: float = 3.0,
                 hitrost_kmh: float = 50.0, max_kmh: float = 200.0, potrdi: bool = True, zamik_potrditve_s: float = 3.0,
                 postanek_s: float = 60.0, prevoz_z_nujne: bool = False, izpad: tuple | None = None,
                 korak_s: float = 1.0, ura=time.time):
        self.enota_id, self.koda, self.ura = enota_id, koda, ura
        self.transport = Izpad(transport or httpx.AsyncHTTPTransport(retries=0), *(izpad or (None, 0.0)), ura=ura)
        self.o = Odjemalec(Seja(None), url, transport=self.transport, ura=ura)
        self.vrsta = VrstaFiksov(":memory:")
        self.pos = Posiljalec(self.o, self.vrsta, ura=ura)
        self.hitrost_ms = min(hitrost_kmh * pospesek, max_kmh) / 3.6
        self.postanek_s = postanek_s / max(pospesek, 1.0)
        self.potrdi, self.zamik_potrditve_s = potrdi, zamik_potrditve_s
        self.prevoz_z_nujne = prevoz_z_nujne
        self.korak_s = korak_s
        self.gps: SimuliranVir | None = None
        self.na_prizoriscu_od: float | None = None
        self.videno_obvestilo: dict[str, float] = {}
        self.pot_za: tuple | None = None
        self.fiksov = 0
        self.dnevnik: list[str] = []

    def _log(self, besedilo: str) -> None:
        vrstica = f"[{self.enota_id}] {besedilo}"
        self.dnevnik.append(vrstica)
        dnevnik.info(vrstica)

    async def _stanje(self, st: str, k: dict | None) -> dict | None:
        try:
            n = await self.o.stanje(st, k)
            self._log(f"{st}" + (f" · {k['naloga_id']} {k['akcija']}" if k else ""))
            self.pos.zbudi()
            return n
        except NiPovezave:
            return None  # brez omrežja: poskusi znova v naslednjem koraku
        except Zavrnjeno as z:
            self._log(f"{st} zavrnjeno: {z}")
            return None

    async def korak(self, n: dict) -> None:
        """En korak odločanja glede na nalogo (vir resnice je strežnik)."""
        zdaj = self.ura()
        for o in n.get("obvestila", []):
            prvic = self.videno_obvestilo.setdefault(o["id"], zdaj)
            if self.potrdi and zdaj - prvic >= self.zamik_potrditve_s:
                try:
                    await self.o.potrdi(o["id"])
                    self._log(f"potrdil prejem plana ({len(o['razlike'].get('dodani', []))} dodanih korakov)")
                except (NiPovezave, Zavrnjeno):
                    pass
            return  # brez potrditve ni vožnje
        k = next((x for x in n.get("koraki", []) if x.get("trenutni")), None)
        if not k:
            self.na_prizoriscu_od = None
            return
        faza = n.get("faza")
        if faza is None:
            await self._stanje("prejeto", k)
        elif faza == "prejeto":
            if n.get("pot") or n["enota"]["tip"] == "helikopter":
                await self._stanje("na_poti", k)
        elif faza == "na_poti":
            kljuc = (k["naloga_id"], k["akcija"], (n.get("pot") or {}).get("izracunano"))
            if self.pot_za != kljuc:
                geo = (n.get("pot") or {}).get("geometrija") or [[k["lat"], k["lon"]]]
                self.gps.nastavi_pot(geo + [[k["lat"], k["lon"]]])
                self.pot_za = kljuc
            if not self.gps.pot and razdalja_m(self.gps.polozaj, (k["lat"], k["lon"])) < 40:
                await self._stanje("na_prizoriscu", k)
                self.na_prizoriscu_od = self.ura()
        elif faza == "na_prizoriscu":
            if self.na_prizoriscu_od is None:
                self.na_prizoriscu_od = zdaj
            if zdaj - self.na_prizoriscu_od < self.postanek_s:
                return
            if self.prevoz_z_nujne and "pacient_nalozen" in n.get("mogoci_predlogi", []):
                await self._stanje("pacient_nalozen", k)  # predlog: bolnišnico izbere dispečer
            elif n.get("dovoljena_stanja"):
                await self._stanje(n["dovoljena_stanja"][0], k)
            self.na_prizoriscu_od = None

    async def tek(self, trajanje_s: float | None = None, do_prostega: bool = False) -> None:
        j = await self.o.prijava(self.enota_id, self.koda, self.vrsta.naprava_id)
        self._log(f"prijavljen ({j['enota']['ime']})")
        n = await self.o.naloga()
        self.gps = SimuliranVir((n["enota"]["lat"], n["enota"]["lon"]), self.hitrost_ms, ura=self.ura)
        posiljalec = asyncio.ensure_future(self.pos.tek())
        konec = self.ura() + trajanje_s if trajanje_s else None
        zadnji_fiks = 0.0
        delal = False
        try:
            while konec is None or self.ura() < konec:
                if self.ura() - zadnji_fiks >= float(self.pos.gps.get("fiks_s", 2)):
                    dt = self.ura() - zadnji_fiks if zadnji_fiks else 0.0
                    zadnji_fiks = self.ura()
                    self.pos.nov_fiks(self.gps.korak(min(dt, 30.0)))
                    self.fiksov += 1
                try:
                    n = await self.o.naloga()
                    delal = delal or any(x.get("trenutni") for x in n.get("koraki", []))
                    await self.korak(n)
                    if do_prostega and delal and not any(x.get("trenutni") for x in n.get("koraki", [])) and not n.get("obvestila"):
                        self._log("vse naloge opravljene – prosto")
                        break
                except NiPovezave:
                    pass
                except Odjavljen:
                    self._log("odjavljen (druga naprava ali nova koda) – konec")
                    break
                await asyncio.sleep(self.korak_s)
            # počakaj, da se vrsta izprazni (zadnji položaji)
            for _ in range(20):
                if not len(self.vrsta) or self.transport.odklopljen:
                    break
                self.pos.zbudi()
                await asyncio.sleep(max(self.korak_s, 0.05))
        finally:
            self.pos.ustavi()
            posiljalec.cancel()
            await self.o.zapri()


def _demo_kode() -> dict:
    try:
        return json.loads((KOREN / "demo" / "scenarij.json").read_text(encoding="utf-8")).get("kode_voznikov", {})
    except (OSError, ValueError):
        return {}


async def _main(a) -> None:
    kode = {**_demo_kode(), **dict(x.split("=", 1) for x in a.koda)}
    enote = [e.strip().upper() for e in a.enote.split(",") if e.strip()]
    if not enote:
        async with httpx.AsyncClient(base_url=a.url, timeout=10) as c:
            plan = (await c.get("/plan/aktiven")).json() or {}
        enote = [e for e, ks in plan.get("koraki_po_enoti", {}).items() if ks and not e.startswith("HELI")][:3]
    izpad = tuple(float(x) for x in a.izpad.split(":")) if a.izpad else None
    ne = {x.strip().upper() for x in a.ne_potrdi.split(",") if x.strip()}
    vozila = [SimVozilo(e, kode.get(e, ""), a.url, pospesek=a.pospesek, potrdi=e not in ne, izpad=izpad,
                        prevoz_z_nujne=a.prevoz) for e in enote]
    print("Vozila: " + ", ".join(enote) + " (Ctrl+C za konec)")
    await asyncio.gather(*(asyncio.sleep(i * random.uniform(0.3, 1.0)) for i in range(len(vozila))))
    await asyncio.gather(*(v.tek(a.trajanje, a.do_prostega) for v in vozila), return_exceptions=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--enote", default="", help="npr. RV-1,RV-4 (privzeto: do 3 enote z nalogami v aktivnem planu)")
    ap.add_argument("--koda", action="append", default=[], help="ENOTA=KODA (privzeto demo kode iz scenarija)")
    ap.add_argument("--pospesek", type=float, default=3.0)
    ap.add_argument("--ne-potrdi", default="", help="enote, ki ne potrdijo prejema (prikaz eskalacije)")
    ap.add_argument("--izpad", default="", help="OD:TRAJANJE v sekundah, npr. 30:45 (vrsta fiksov brez povezave)")
    ap.add_argument("--prevoz", action="store_true", help="na nujni vožnji predlagaj prevoz pacienta")
    ap.add_argument("--trajanje", type=float, default=None, help="sekunde (privzeto do Ctrl+C)")
    ap.add_argument("--do-prostega", action="store_true", help="ustavi vozilo, ko opravi vse naloge")
    try:
        asyncio.run(_main(ap.parse_args()))
    except KeyboardInterrupt:
        pass
