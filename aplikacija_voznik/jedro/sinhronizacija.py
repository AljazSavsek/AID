"""Sinhronizacija naloge: WebSocket /ws/voznik -> ob vsakem obvestilu ponovno GET /voznik/naloga.

Naloga je vir resnice (stanje), WS je le opozorilo »nekaj se je spremenilo«, zato izgubljen
dogodek ne more pustiti zastarele naloge. Rezerva: polling /voznik/dogodki vsakih 5 s in
celotna naloga vsakih 30 s. Mrtva povezava: brez sporočila 40 s (strežnik pošilja »ziv« na 15 s).
Stanje povezave: ws | polling | brez.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Awaitable, Callable

from .odjemalec import NiPovezave, Odjavljen, Odjemalec, Zavrnjeno

dnevnik = logging.getLogger("voznik.sinhronizacija")


class Sinhronizacija:
    def __init__(self, odjemalec: Odjemalec, ob_nalogi: Callable[[dict], Awaitable[None] | None],
                 ob_povezavi: Callable[[str], None] | None = None, ob_odjavi: Callable[[], None] | None = None,
                 ura=time.time):
        self.o, self.ura = odjemalec, ura
        self.ob_nalogi, self.ob_povezavi = ob_nalogi, ob_povezavi or (lambda s: None)
        self.ob_odjavi = ob_odjavi or (lambda: None)
        self.seq, self.zagon = 0, None
        self.povezava = "brez"
        self.naloga: dict | None = None
        self._tece = False
        self._zadnja_naloga = 0.0

    def _nastavi_povezavo(self, p: str) -> None:
        if p != self.povezava:
            self.povezava = p
            self.ob_povezavi(p)

    async def osvezi_nalogo(self) -> dict | None:
        try:
            n = await self.o.naloga()
        except Odjavljen:
            self._tece = False
            self.ob_odjavi()
            return None
        except (NiPovezave, Zavrnjeno) as e:
            dnevnik.info("naloga: %s", e)
            return None
        self.naloga, self._zadnja_naloga = n, self.ura()
        if n.get("zagon") and n["zagon"] != self.zagon:
            self.zagon, self.seq = n["zagon"], n.get("seq", 0)
        r = self.ob_nalogi(n)
        if asyncio.iscoroutine(r):
            await r
        return n

    def _obdelaj(self, m: dict) -> bool:
        """Vrne True, če je treba osvežiti nalogo."""
        if m.get("zagon") and m["zagon"] != self.zagon:
            self.zagon, self.seq = m["zagon"], 0
            return True
        if m.get("seq"):
            self.seq = max(self.seq, m["seq"])
        return m.get("tip") in ("plan", "pot", "naloga", "pozdrav", "sporocilo", "predlog")

    async def _ws(self) -> None:
        import websockets  # neobvezno na strežniku; v aplikaciji je odvisnost
        if self.o.seja.podatki.get("dostop_potece", 0) - 60 < self.ura():
            await self.o.osvezi()
        async with websockets.connect(self.o.ws_url(), open_timeout=10, ping_interval=None) as ws:
            await ws.send(json.dumps({"zeton": self.o.seja.podatki["dostop"], "od": self.seq, "zagon": self.zagon}))
            self._nastavi_povezavo("ws")
            while self._tece:
                m = json.loads(await asyncio.wait_for(ws.recv(), 40))
                if m.get("tip") == "napaka":
                    await self.o.osvezi()  # morda potekel žeton; Odjavljen, če seja ni več veljavna
                    return
                if self._obdelaj(m):
                    await self.osvezi_nalogo()

    async def _polling_korak(self) -> None:
        r = await self.o.dogodki(self.seq, self.zagon)
        nov_zagon = r["zagon"] != self.zagon
        spremembe = [self._obdelaj(d) for d in r["dogodki"]]
        self.zagon = r["zagon"]
        self.seq = max(self.seq, r.get("seq", 0))
        if nov_zagon or any(spremembe) or self.ura() - self._zadnja_naloga > 30:
            await self.osvezi_nalogo()

    async def tek(self) -> None:
        self._tece = True
        await self.osvezi_nalogo()
        zamik = 1.0
        while self._tece:
            try:
                await self._ws()
                zamik = 1.0
            except Odjavljen:
                self._tece = False
                self.ob_odjavi()
                return
            except Exception as e:  # noqa: BLE001 – vse napake WS -> rezerva
                dnevnik.info("ws: %r", e)
            if not self._tece:
                return
            # rezerva do naslednjega poskusa WS
            konec = self.ura() + zamik
            while self._tece and self.ura() < konec:
                try:
                    await self._polling_korak()
                    self._nastavi_povezavo("polling")
                except Odjavljen:
                    self._tece = False
                    self.ob_odjavi()
                    return
                except (NiPovezave, Zavrnjeno):
                    self._nastavi_povezavo("brez")
                await asyncio.sleep(min(5.0, max(0.5, konec - self.ura())))
            zamik = min(zamik * 2, 30.0)

    def ustavi(self) -> None:
        self._tece = False
