"""Ekipa na terenu: pregled iz potrjenega plana, lokacija, status, sporočilo -> predlog dogodka."""
import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False), headers={"X-Uporabnik": "voznik"}) as k:
        k.center = c
        yield k


def enota_z_nalogo(cl):
    plan = cl.get("/plan/aktiven").json()
    return next(e for e, ks in plan["koraki_po_enoti"].items() if ks and not e.startswith("HELI"))


def test_pregled_iz_aktivnega_plana(cl):
    eid = enota_z_nalogo(cl)
    r = cl.get(f"/voznik/{eid}")
    d = r.json()
    assert d["korak"] and d["pot"]["cas_s"] >= 0 and len(d["pot"]["geometrija"]) >= 2
    assert d["korak"]["naloga"]["prioriteta"] in range(1, 6)
    assert brez_ponudnikov(r.text) == []
    assert cl.get("/voznik/XX").status_code == 404


def test_lokacija_objavi_premik(cl):
    seq = cl.get("/stanje").json()["seq"]
    cl.post("/enota/RV-2/lokacija", json={"lat": 46.06, "lon": 14.50, "smer": 90, "hitrost_kmh": 50})
    e = next(x for x in cl.get("/enote").json() if x["id"] == "RV-2")
    assert (e["lat"], e["lon"]) == (46.06, 14.50)
    assert any(d["tip"] == "enota_premik" for d in cl.get("/stanje", params={"od": seq}).json()["dogodki"])


def test_status_prevzeto_in_dostavljeno(cl):
    eid = enota_z_nalogo(cl)
    korak = cl.get(f"/voznik/{eid}").json()["korak"]
    nid = korak["naloga_id"]
    cl.post(f"/enota/{eid}/status", json={"naloga_id": nid, "akcija": "na_poti"})
    cl.post(f"/enota/{eid}/status", json={"naloga_id": nid, "akcija": "prevzeto"})
    nov = cl.get(f"/voznik/{eid}").json()["korak"]
    assert not nov or (nov["naloga_id"], nov["akcija"]) != (nid, korak["akcija"])
    cl.post(f"/enota/{eid}/status", json={"naloga_id": nid, "akcija": "dostavljeno"})
    n = next(x for x in cl.get("/naloge").json() if x["id"] == nid)
    assert n["stanje"] == "opravljena"
    rev = cl.get("/revizija", params={"objekt_id": nid}).json()
    assert {r["akcija"] for r in rev} >= {"status_na_poti", "status_prevzeto", "status_dostavljeno"}


def test_sporocilo_je_le_predlog(cl):
    r = cl.post("/sporocilo", json={"enota_id": "RV-1", "besedilo": "Vozilo je obstalo. Oprema je še v njem."}).json()
    assert len(r["predlogi_dogodkov"]) == 1
    d = r["predlogi_dogodkov"][0]
    assert d["tip"] == "okvara_vozila" and d["stanje"] == "predlagano" and d["parametri"]["enota_id"] == "RV-1"
    # dokler dispečer ne potrdi, se nič ne spremeni
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] != "okvara"
    assert cl.get("/dogodki", params={"stanje": "predlagano"}).json()[0]["id"] == d["id"]
    r = cl.post(f"/dogodek/{d['id']}/sprejmi", headers={"X-Uporabnik": "M. Novak"}).json()
    assert r["dogodek"]["stanje"] == "sprejeto" and r["plan_predlog"]["stanje"] == "predlog"
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] == "okvara"
    assert cl.post(f"/dogodek/{d['id']}/zavrni").status_code == 409


def test_zavrnjen_dogodek_nic_ne_spremeni(cl):
    d = cl.post("/sporocilo", json={"enota_id": "RV-1", "besedilo": "Vozilo je obstalo."}).json()["predlogi_dogodkov"][0]
    assert cl.post(f"/dogodek/{d['id']}/zavrni", json={"razlog": "lažni alarm"}).json()["stanje"] == "zavrnjeno"
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] != "okvara"


def test_ime_s_sumniki_v_glavi(cl):
    from urllib.parse import quote
    kid = cl.post("/klic/zacni", json={}, headers={"X-Uporabnik": quote("Žiga Čeč")}).json()["id"]
    rev = cl.get("/revizija", params={"objekt_id": kid}).json()
    assert rev[0]["uporabnik"] == "Žiga Čeč"
