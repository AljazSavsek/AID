import pytest
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


@pytest.fixture(autouse=True)
def _heartbeat_v_zacasno_mapo(tmp_path_factory, monkeypatch):
    """Heartbeati iz testov ne smejo v podatki/heartbeat."""
    if not os.environ.get("AID_HEARTBEAT_MAPA") or "pytest" not in os.environ["AID_HEARTBEAT_MAPA"]:
        monkeypatch.setenv("AID_HEARTBEAT_MAPA", str(tmp_path_factory.mktemp("heartbeat")))


def nujna_za_enoto(cl, enota_id: str, lat: float = 46.0395, lon: float = 14.493) -> str:
    """Dodeli enoti novo nujno vožnjo (zavezujoče, kot ob izbiri dispečerja) in potrdi plan. Vrne id naloge."""
    import time

    from dnevnik import model as M
    c = cl.center
    n = M.naloga("nujna_voznja", {"lat": lat, "lon": lon, "naslov": "test"}, 2, okno_od=time.time(), okno_do=time.time() + 900, opis="test")
    n.update(dodeljena_enota=enota_id, zelena_enota=enota_id)
    plan = cl.portal.call(lambda: c.uveljavi_dogodek(M.dogodek("nujna_naloga", {"naloga": n}, "klic", "sprejeto"), "M. Novak",
                                                     zelena_enota=(n["id"], enota_id)))
    c.potrdi_plan(plan["id"], "M. Novak")
    return n["id"]
