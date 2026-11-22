"""Zvočni klic: posnetki -> prepis govora (Whisper ali shranjen prepis) -> segmenti klica -> AI predlogi."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve
from poslusalec import poslusalec as pp
from zajem import zvok


@pytest.fixture()
def cl(tmp_path, monkeypatch):
    monkeypatch.setenv("AID_PREPISI_MAPA", str(tmp_path / "prepisi"))
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def test_odseki_posnetkov_in_shranjen_prepis():
    m = pp.ShranjenPrepis()
    odseki = asyncio.run(_zberi(pp.odseki_klica(m)))
    assert len(odseki) == 18 and odseki[0]["vloga"] == "dispecer" and odseki[1]["vloga"] == "klicatelj"
    assert all(o["trajanje_s"] > 0 for o in odseki) and "zaletela" in odseki[1]["besedilo"]


async def _zberi(gen):
    return [o async for o in gen]


class LazenWhisper:
    """Kot faster-whisper: transcribe vrne (segmenti, info)."""
    def __init__(self):
        self.shranjen = pp.ShranjenPrepis()
        self.klici = 0

    def transcribe(self, pot, **kw):
        assert kw.get("language") == "sl"
        self.klici += 1
        return self.shranjen.transcribe(pot)


def test_zvocni_klic_doda_segmente_in_ai_predloge(cl, tmp_path):
    c = cl.center
    k = c.zacni_klic("M. Novak")
    model = LazenWhisper()
    n = cl.portal.call(lambda: zvok.predvajaj_zvocni_klic(c, k["id"], hitrost=1000, model=model))
    cl.portal.call(c.pocakaj_ozadje)
    assert n == 18 and model.klici == 18
    seg = c.segmenti(k["id"])
    assert len(seg) == 18 and seg[1]["govorec"] == "klicatelj" and seg[-1]["do_s"] > 80  # realni časi posnetkov
    polja = {p["polje"]: p for p in c.predlogi(k["id"]) if p["stanje"] == "predlagano"}
    assert polja["vrsta_dogodka"]["vrednost"] == "prometna_nesreca"
    assert polja["naslov"]["vrednost"] == "Zagorje ob Savi" and polja["naslov"]["zaupanje"] < 0.5  # negotovo
    assert "most" in polja["orientacija"]["vrednost"]
    assert "lokacija" in polja and polja["lokacija"]["zaupanje"] < 0.5  # le središče kraja -> dispečer klikne na karto
    # prepis shranjen kot pri samostojnem poslušalcu
    shranjeni = list((tmp_path / "prepisi").glob("klic_*.json"))
    assert shranjeni and json.loads(shranjeni[0].read_text(encoding="utf-8"))["text"].startswith("Dispečer:")


def test_endpoint_zvocni_klic_z_rezervo(cl, monkeypatch):
    monkeypatch.setattr(zvok, "whisper_na_voljo", lambda: False)  # brez faster-whisper -> shranjen prepis
    cakajoci = cl.get("/cakajoci").json()
    assert any(q.get("demo") == "zvok" for q in cakajoci)
    k = cl.post("/demo/klic-zvok", json={"hitrost": 1000}).json()
    assert not any(q.get("demo") == "zvok" for q in cl.get("/cakajoci").json())
    cl.portal.call(cl.center.pocakaj_ozadje)
    klic = cl.get(f"/klic/{k['id']}").json()
    assert len(klic["segmenti"]) == 18
    st = [d["podatki"] for d in cl.get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == "prepis_stanje"]
    assert st and st[-1]["stanje"] == "konec" and any(x.get("vir") == "shranjen" for x in st)


def test_besedilni_demo_ne_vzame_zvocnega_klica(cl):
    k = cl.post("/demo/klic", json={"hitrost": 1000}).json()
    assert k["stevilka"] == "+386 41 555 238"
    assert any(q.get("demo") == "zvok" for q in cl.get("/cakajoci").json())


def test_brez_prepisa_503(cl, monkeypatch):
    monkeypatch.setattr(zvok, "whisper_na_voljo", lambda: False)

    def ni(*a, **k):
        raise FileNotFoundError("ni")
    monkeypatch.setattr(pp, "ShranjenPrepis", ni)
    r = cl.post("/demo/klic-zvok", json={"hitrost": 1000})
    assert r.status_code == 503 and "faster-whisper" in r.json()["detail"]
