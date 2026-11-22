"""Zvočni klic: posnetki -> Whisper -> segmenti klica -> AI predlogi. Aplikacija nima vnaprej določenega besedila.

Testi ne prenašajo modela Whisper (500 MB), zato namesto njega uporabijo NadomestniWhisper:
ta dejansko dekodira podani posnetek (faster-whisper decode_audio) in vrne besedilo, ki ga je za
ta posnetek že prepoznal pravi Whisper (tests/podatki/whisper_odgovori.json – le testni podatek)."""
import asyncio
import json
import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve
from poslusalec import poslusalec as pp
from zajem import zvok

ODGOVORI = json.loads((Path(__file__).parent / "podatki" / "whisper_odgovori.json").read_text(encoding="utf-8"))


class NadomestniWhisper:
    """Vmesnik kot faster-whisper (transcribe -> (segmenti, info)); posnetek mora biti pravi zvok."""
    def __init__(self):
        self.klici = []

    def transcribe(self, pot, **kw):
        from faster_whisper.audio import decode_audio
        assert kw.get("language") == "sl"
        zvok_ = decode_audio(str(pot), sampling_rate=16000)
        assert len(zvok_) > 16000  # vsaj sekunda zvoka
        self.klici.append(Path(pot).name)

        class S:
            text = ODGOVORI.get(Path(pot).name, "") if len(zvok_) > 2 * 16000 else ""  # prekratek posnetek -> brez govora
        return [S()], None


@pytest.fixture(autouse=True)
def cist_model(monkeypatch):
    monkeypatch.setattr(zvok, "MODEL", {"stanje": "ni_zagnan", "razlog": "", "cas_napake": 0.0, "model": None, "naloga": None})


@pytest.fixture()
def cl(tmp_path, monkeypatch):
    monkeypatch.setenv("AID_PREPISI_MAPA", str(tmp_path / "prepisi"))
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def test_v_aplikaciji_ni_vnaprej_dolocenega_besedila():
    koren = Path(__file__).parent.parent
    assert not hasattr(pp, "ShranjenPrepis")
    assert not (koren / "demo" / "klic1.json").exists() and not (koren / "zajem" / "simulator.py").exists()
    for f in list((koren / "api").glob("*.py")) + list((koren / "zajem").glob("*.py")) + [koren / "static" / "js" / "klic.js"]:
        t = f.read_text(encoding="utf-8")
        assert "klic1" not in t and "/demo/klic\"" not in t and "prepisi/klic_" not in t, f


def test_besedilo_je_odvisno_od_zvoka(tmp_path):
    """Če posnetek spremenimo, se spremeni tudi rezultat (besedilo ni vezano na ime klica)."""
    for p in pp.posnetki_klica():
        shutil.copy(p, tmp_path / p.name)
    with wave.open(str(tmp_path / "02_klicatelj.wav")) as w:
        par, fr = w.getparams(), w.readframes(w.getnframes())
    with wave.open(str(tmp_path / "02_klicatelj.wav"), "wb") as w:
        w.setparams(par)
        w.writeframes(fr[: len(fr) // 4])  # le še ~1,3 s
    m = NadomestniWhisper()
    odseki = asyncio.run(_zberi(pp.odseki_klica(m, mapa=tmp_path)))
    assert len(m.klici) == 18 and odseki[1]["besedilo"] == "" and odseki[3]["besedilo"]
    assert abs(odseki[1]["trajanje_s"] - 1.35) < 0.1


async def _zberi(gen):
    return [o async for o in gen]


def test_zvocni_klic_doda_segmente_in_ai_predloge(cl, tmp_path):
    c = cl.center
    k = c.zacni_klic("M. Novak")
    model = NadomestniWhisper()
    n = cl.portal.call(lambda: zvok.predvajaj_zvocni_klic(c, k["id"], hitrost=1000, model=model))
    cl.portal.call(c.pocakaj_ozadje)
    assert n == 18 and len(model.klici) == 18
    seg = c.segmenti(k["id"])
    assert len(seg) == 18 and seg[1]["govorec"] == "klicatelj" and seg[-1]["do_s"] > 80  # realni časi posnetkov
    polja = {p["polje"]: p for p in c.predlogi(k["id"]) if p["stanje"] == "predlagano"}
    assert polja["vrsta_dogodka"]["vrednost"] == "prometna_nesreca"
    assert polja["naslov"]["vrednost"] == "Zagorje ob Savi" and polja["naslov"]["zaupanje"] < 0.5  # negotovo
    assert "most" in polja["orientacija"]["vrednost"]
    assert "lokacija" in polja and polja["lokacija"]["zaupanje"] < 0.5  # le središče kraja -> dispečer klikne na karto
    shranjeni = list((tmp_path / "prepisi").glob("klic_*.json"))  # prepis shranjen kot pri samostojnem poslušalcu
    assert shranjeni and json.loads(shranjeni[0].read_text(encoding="utf-8"))["text"].startswith("Dispečer:")


def test_endpoint_zvocni_klic_z_whisperjem(cl, monkeypatch):
    monkeypatch.setattr(pp, "nalozi_model", lambda ime=None: NadomestniWhisper())
    assert cl.get("/prepis/stanje").json()["stanje"] in ("ni_zagnan", "ni_namescen")
    assert any(q.get("demo") == "zvok" for q in cl.get("/cakajoci").json())
    k = cl.post("/demo/klic-zvok", json={"hitrost": 1000}).json()
    assert not any(q.get("demo") == "zvok" for q in cl.get("/cakajoci").json())
    cl.portal.call(cl.center.pocakaj_ozadje)
    assert len(cl.get(f"/klic/{k['id']}").json()["segmenti"]) == 18
    st = [d["podatki"] for d in cl.get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == "prepis_stanje"]
    assert [x["stanje"] for x in st] == ["nalagam", "v_zivo", "konec"] and all(x.get("vir", "whisper") == "whisper" for x in st)
    assert cl.get("/prepis/stanje").json()["stanje"] == "pripravljen"


def test_brez_whisperja_ni_besedila_le_rocni_zapis(cl, monkeypatch):
    monkeypatch.setattr(zvok, "whisper_na_voljo", lambda: False)
    r = cl.post("/demo/klic-zvok", json={"hitrost": 1000})
    assert r.status_code == 503 and "faster-whisper" in r.json()["detail"]
    assert any(q.get("demo") == "zvok" for q in cl.get("/cakajoci").json())  # klic ostane v vrsti
    assert cl.get("/prepis/stanje").json()["stanje"] == "ni_namescen"


def test_model_se_ne_nalozi_klic_odprt_za_rocni_zapis(cl, monkeypatch):
    def pade(ime=None):
        raise OSError("ni povezave do huggingface.co")
    monkeypatch.setattr(pp, "nalozi_model", pade)
    k = cl.post("/demo/klic-zvok", json={"hitrost": 1000}).json()
    cl.portal.call(cl.center.pocakaj_ozadje)
    klic = cl.get(f"/klic/{k['id']}").json()
    assert klic["segmenti"] == [] and klic["stanje"] == "v_teku"  # nič izmišljenega besedila
    st = cl.get("/prepis/stanje").json()
    assert st["stanje"] == "napaka" and "huggingface" in st["razlog"]
    assert cl.post("/demo/klic-zvok", json={"hitrost": 1000}).status_code == 503  # ne poskuša takoj znova
    dog = [d["podatki"] for d in cl.get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == "prepis_stanje"]
    assert dog[-1]["stanje"] == "napaka"
