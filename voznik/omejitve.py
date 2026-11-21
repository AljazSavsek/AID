"""Omejevanje zahtevkov (vedro z žetoni, v pomnilniku) za voznikove endpointe."""
from __future__ import annotations

import time


class Omejevalnik:
    def __init__(self, na_minuto: int, ura=time.monotonic):
        self.kapaciteta = max(1, int(na_minuto))
        self.hitrost = self.kapaciteta / 60.0
        self.ura = ura
        self._vedra: dict[str, tuple[float, float]] = {}

    def dovoli(self, kljuc: str) -> bool:
        zdaj = self.ura()
        zetoni, prej = self._vedra.get(kljuc, (float(self.kapaciteta), zdaj))
        zetoni = min(self.kapaciteta, zetoni + (zdaj - prej) * self.hitrost)
        if zetoni < 1:
            self._vedra[kljuc] = (zetoni, zdaj)
            return False
        self._vedra[kljuc] = (zetoni - 1, zdaj)
        if len(self._vedra) > 10000:
            self._vedra.pop(next(iter(self._vedra)))
        return True
