import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AID_BREZ_ENV"] = "1"            # testi ne berejo lokalne .env
os.environ["AID_NOMINATIM"] = "0"          # testi ne smejo klicati interneta
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OLLAMA_URL", None)
for k in ("TOMTOM_API_KEY", "TOMTOM_MAP_KEY", "ORS_API_KEY", "OSRM_URL", "GURS_GEOKODER_URL"):
    os.environ.pop(k, None)

IMENA_PONUDNIKOV = ("tomtom", "openroute", "ors", "osrm", "google", "here", "nominatim", "mapbox")


def brez_ponudnikov(besedilo: str) -> list[str]:
    """Vrne najdena imena ponudnikov (kot cele besede) v besedilu odgovora."""
    import re
    low = besedilo.lower()
    return [i for i in IMENA_PONUDNIKOV if re.search(rf"(?<![a-zčšž]){i}(?![a-zčšž])", low)]


# ---------- prijava dispečerja v testih ----------
# Testni računi; vsak TestClient se samodejno prijavi kot »M. Novak« (razen z glavo X-Brez-Prijave),
# da obstoječi testi preverjajo vsebino, testi v test_varnost.py pa preverjajo zavrnitve brez prijave.
os.environ["AID_DISPECER_DEMO"] = "0"
os.environ["AID_DISPECERJI"] = "M. Novak:test-geslo;Žiga Čeč:test-geslo"
os.environ["AID_DISPECER_SKRIVNOST"] = "testna-skrivnost"

from fastapi.testclient import TestClient  # noqa: E402

_vstop = TestClient.__enter__


def _vstop_s_prijavo(self):
    k = _vstop(self)
    if "x-brez-prijave" not in {h.lower() for h in self.headers}:
        r = k.post("/dispecer/prijava", json={"ime": "M. Novak", "geslo": "test-geslo"})
        assert r.status_code == 200, r.text
    return k


TestClient.__enter__ = _vstop_s_prijavo


def prijavi(cl, ime="M. Novak", geslo="test-geslo"):
    r = cl.post("/dispecer/prijava", json={"ime": ime, "geslo": geslo})
    assert r.status_code == 200, r.text
    return r
