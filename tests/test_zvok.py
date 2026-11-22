"""Zvočni klic: posnetki -> Whisper -> segmenti klica -> AI predlogi. Aplikacija nima vnaprej določenega besedila
in ni vezana na posnetke v poslusalec/zvok (ekipa jih lahko kadarkoli zamenja).

Testi ne prenašajo modela Whisper (500 MB), zato namesto njega uporabijo NadomestniWhisper, ki dejansko
dekodira podani posnetek (faster-whisper decode_audio). Večina testov uporablja lastne testne posnetke
(AID_ZVOK_MAPA) in testno besedilo (tests/podatki/whisper_odgovori.json – le testni podatek)."""
import asyncio
import json
import shutil
import wave
from pathlib import Path

import numpy as np

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

        if Path(pot).name in CELI_KLIC:  # cel klic v enem posnetku: več odsekov s časi
            return [S(od, do, t) for od, do, t in CELI_KLIC[Path(pot).name]], None
        besedilo = ODGOVORI.get(Path(pot).name, "") if len(zvok_) > 2 * 16000 else ""  # prekratek posnetek -> brez govora
        return [S(0.0, len(zvok_) / 16000, besedilo)], None


class S:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


CELI_KLIC = {"celoten_klic.wav": [(0.2, 3.0, "Reševalci, prosim."), (3.5, 8.0, "Moj oče je padel z lestve na Celovški cesti 105 v Ljubljani."),
                                  (8.5, 11.0, "Ne odziva se.")]}


def napravi_posnetek(pot: Path, sekunde: float):
    """Testni »govor« (šum in ton), 16 kHz mono PCM – vsebina ni pomembna, NadomestniWhisper ga le dekodira."""
    t = np.arange(int(sekunde * 16000)) / 16000
    x = (0.3 * np.sin(2 * np.pi * 220 * t) + 0.05 * np.random.default_rng(1).standard_normal(t.size)) * 32767
    with wave.open(str(pot), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)  # noqa: E702
        w.writeframes(x.astype("<i2").tobytes())


@pytest.fixture(autouse=True)
def testni_posnetki(tmp_path_factory, monkeypatch):
    """18 testnih posnetkov NN_vloga.wav (+ datoteka, ki ni zvok) v ločeni mapi; aplikacija jo dobi z AID_ZVOK_MAPA."""
    mapa = tmp_path_factory.mktemp("zvok")
    for i, ime in enumerate(sorted(ODGOVORI), 1):
        napravi_posnetek(mapa / ime, 3.0 + (i % 4))
    (mapa / "Vir.txt").write_text("vir posnetkov", encoding="utf-8")
    monkeypatch.setenv("AID_ZVOK_MAPA", str(mapa))
    monkeypatch.setenv("AID_ZVOK_VIR", "whisper")  # privzeto testiramo Whisper; demo ima svoje teste
    return mapa


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


def test_besedilni_demo_klic_ne_obstaja_vec():
    koren = Path(__file__).parent.parent
    assert not (koren / "demo" / "klic1.json").exists() and not (koren / "zajem" / "simulator.py").exists()
    for f in list((koren / "api").glob("*.py")) + [koren / "static" / "js" / "klic.js"]:
        t = f.read_text(encoding="utf-8")
        assert "klic1" not in t and "/demo/klic\"" not in t, f


def test_besedilo_je_odvisno_od_zvoka(tmp_path):
    """Če posnetek spremenimo, se spremeni tudi rezultat (besedilo ni vezano na ime klica)."""
    for p in pp.posnetki_klica():
        shutil.copy(p, tmp_path / p.name)
    napravi_posnetek(tmp_path / "02_klicatelj.wav", 1.3)
    m = NadomestniWhisper()
    odseki = asyncio.run(_zberi(pp.odseki_klica(m, mapa=tmp_path)))
    assert len(m.klici) == 18 and odseki[1]["besedilo"] == "" and odseki[3]["besedilo"]
    assert abs(odseki[1]["trajanje_s"] - 1.3) < 0.05


def test_pravi_posnetki_ekipe_v_mapi_poslusalec(monkeypatch):
    """Posnetki, ki so trenutno v poslusalec/zvok (koliko jih je in kaj vsebujejo, ni pomembno), gredo vsi skozi Whisper."""
    monkeypatch.delenv("AID_ZVOK_MAPA")
    posnetki = pp.posnetki_klica()
    assert posnetki and all(p.suffix.lower() in pp.ZVOK for p in posnetki)  # Vir.txt ipd. preskočeno
    m = NadomestniWhisper()
    odseki = asyncio.run(_zberi(pp.odseki_klica(m)))
    assert m.klici == [p.name for p in posnetki] and len(odseki) == len(posnetki)
    assert {o["vloga"] for o in odseki} <= {"dispecer", "klicatelj"} and all(o["trajanje_s"] > 0.5 for o in odseki)


def test_cel_klic_v_enem_posnetku(cl, testni_posnetki):
    """Posnetek brez vloge v imenu (cel klic) se razdeli na Whisperjeve odseke z dejanskimi časi."""
    for p in list(testni_posnetki.iterdir()):
        p.unlink()
    napravi_posnetek(testni_posnetki / "celoten_klic.wav", 12)
    c = cl.center
    k = c.zacni_klic("M. Novak")
    n = cl.portal.call(lambda: zvok.predvajaj_zvocni_klic(c, k["id"], hitrost=1000, model=NadomestniWhisper()))
    cl.portal.call(c.pocakaj_ozadje)
    seg = c.segmenti(k["id"])
    assert n == 3 and [(s["od_s"], s["do_s"]) for s in seg] == [(0.2, 3.0), (3.5, 8.0), (8.5, 11.0)]
    polja = {p["polje"]: p for p in c.predlogi(k["id"]) if p["stanje"] == "predlagano"}
    assert polja["vrsta_dogodka"]["vrednost"] == "padec" and "Celovška" in polja["naslov"]["vrednost"]


def test_oznaka_in_vloga():
    assert pp.oznaka_in_vloga("03_dispecer.wav") == ("03", "dispecer")
    assert pp.oznaka_in_vloga("07_Dispečer.mp3") == ("07", "dispecer")
    assert pp.oznaka_in_vloga("klic_2026.ogg") == ("klic_2026", None)


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
    # brskalnik dispečerja dobi vsak posnetek ob začetku odseka in ga predvaja
    zv = [d["podatki"] for d in cl.get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == "zvok_odsek"]
    assert [z["st"] for z in zv] == list(range(1, 19)) and zv[1]["posnetek"] == "02_klicatelj.wav" and zv[0]["skupaj"] == 18
    r = cl.get(f"/klic/{k['id']}/zvok/02_klicatelj.wav")
    assert cl.get(f"/klic/{k['id']}/zvok/Vir.txt").status_code == 404  # le zvočni posnetki
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert cl.get(f"/klic/{k['id']}/zvok/..%2F..%2Fdemo%2Fscenarij.json").status_code == 404
    assert cl.get("/klic/ne-obstaja/zvok/02_klicatelj.wav").status_code == 404


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


# ---------------- demo: zvok se predvaja, besedilo je vnaprej določeno ----------------
def test_demo_iz_prepis_json_brez_whisperja(cl, testni_posnetki, monkeypatch):
    monkeypatch.setenv("AID_ZVOK_VIR", "samodejno")
    besedila = {ime: f"Besedilo {ime}" for ime in ODGOVORI}
    (testni_posnetki / "prepis.json").write_text(json.dumps({"_opomba": "test", **besedila}), encoding="utf-8")
    monkeypatch.setattr(pp, "nalozi_model", lambda ime=None: (_ for _ in ()).throw(AssertionError("Whisper se ne sme naložiti")))
    monkeypatch.setattr(zvok, "whisper_na_voljo", lambda: False)  # tudi brez faster-whisper
    assert zvok.nacin() == "demo" and cl.get("/prepis/stanje").json()["stanje"] == "demo"
    k = cl.post("/demo/klic-zvok", json={"hitrost": 1000}).json()
    cl.portal.call(cl.center.pocakaj_ozadje)
    seg = cl.get(f"/klic/{k['id']}").json()["segmenti"]
    assert [s["besedilo"] for s in seg] == [besedila[i] for i in sorted(besedila)]
    dog = cl.get("/stanje", params={"od": 0}).json()["dogodki"]
    zv = [d["podatki"] for d in dog if d["tip"] == "zvok_odsek"]
    assert len(zv) == 18 and all(z["vir"] == "demo" for z in zv)  # zvok se predvaja kot prej
    assert [d["podatki"]["stanje"] for d in dog if d["tip"] == "prepis_stanje"] == ["v_zivo", "konec"]


def test_demo_iz_zadnjega_shranjenega_prepisa(tmp_path, testni_posnetki, monkeypatch):
    monkeypatch.setenv("AID_ZVOK_VIR", "samodejno")
    mapa = tmp_path / "prepisi"
    mapa.mkdir()
    monkeypatch.setenv("AID_PREPISI_MAPA", str(mapa))
    assert zvok.nacin() == "whisper"  # brez besedila: Whisper
    imena = sorted(ODGOVORI)
    tekst = " ".join(f"{'Dispečer' if '_dispecer' in i else 'Klicatelj'}: stavek {i}." for i in imena)
    (mapa / "klic_a.json").write_text(json.dumps({"text": tekst}), encoding="utf-8")
    (mapa / "klic_b.json").write_text(json.dumps({"text": "Dispečer: drug klic."}), encoding="utf-8")  # se ne ujema
    besedila, vir = zvok.besedilo_demo()
    assert vir == "klic_a.json" and besedila[imena[1]] == f"stavek {imena[1]}." and zvok.nacin() == "demo"
    monkeypatch.setenv("AID_ZVOK_VIR", "whisper")
    assert zvok.nacin() == "whisper"  # izrecno Whisper


def test_zamrzni_prepis(tmp_path, testni_posnetki, monkeypatch):
    from zajem import zamrzni_prepis
    mapa = tmp_path / "prepisi"
    mapa.mkdir()
    monkeypatch.setenv("AID_PREPISI_MAPA", str(mapa))
    imena = sorted(ODGOVORI)
    tekst = " ".join(f"{'Dispečer' if '_dispecer' in i else 'Klicatelj'}: {i}" for i in imena)
    (mapa / "klic_x.json").write_text(json.dumps({"text": tekst}), encoding="utf-8")
    assert zamrzni_prepis.main([]) == 0
    p = json.loads((testni_posnetki / "prepis.json").read_text(encoding="utf-8"))
    assert p[imena[0]] == imena[0] and "_opomba" in p
    assert zamrzni_prepis.main([]) == 1  # ne prepiše brez --prepisi
