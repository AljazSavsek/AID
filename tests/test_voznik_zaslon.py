"""Sporočilo ekipe (zavarovano z žetonom voznika) -> predlog dogodka -> odločitev dispečerja.
Spletni zaslon voznika uporablja isti API kot Flet aplikacija."""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False), headers={"X-Uporabnik": "M. Novak"}) as k:
        k.center = c
        yield k


def prijava(cl, enota="RV-1", koda="DEMO-RV1"):
    r = cl.post("/voznik/prijava", json={"enota_id": enota, "koda": koda, "naprava_id": "splet-test"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["dostop"]}


def test_sporocilo_zahteva_prijavo(cl):
    assert cl.post("/voznik/sporocilo", json={"besedilo": "Vozilo je obstalo."}).status_code == 401


def test_sporocilo_je_le_predlog(cl):
    h = prijava(cl)
    r = cl.post("/voznik/sporocilo", json={"besedilo": "Vozilo je obstalo. Oprema je še v njem."}, headers=h)
    assert r.status_code == 200 and brez_ponudnikov(r.text) == []
    d = r.json()["predlogi_dogodkov"][0]
    assert d["tip"] == "okvara_vozila" and d["stanje"] == "predlagano" and d["parametri"]["enota_id"] == "RV-1"
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] != "okvara"
    assert [x["id"] for x in cl.get("/dogodki", params={"stanje": "predlagano"}).json()] == [d["id"]]
    r = cl.post(f"/dogodek/{d['id']}/sprejmi").json()
    assert r["dogodek"]["stanje"] == "sprejeto" and r["plan_predlog"]["stanje"] == "predlog"
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] == "okvara"
    assert cl.post(f"/dogodek/{d['id']}/zavrni").status_code == 409
    rev = {x["akcija"] for x in cl.get("/revizija", params={"omejitev": 1000}).json()}
    assert {"sporocilo_ekipe", "predlog_ai", "uveljavi"} <= rev


def test_enota_iz_zetona_ne_iz_telesa(cl):
    h = prijava(cl, "RV-2", "DEMO-RV2")
    d = cl.post("/voznik/sporocilo", json={"besedilo": "Vozilo je obstalo."}, headers=h).json()["predlogi_dogodkov"][0]
    assert d["enota_posiljatelj"] == "RV-2" and d["parametri"]["enota_id"] == "RV-2"


def test_zavrnjen_dogodek_nic_ne_spremeni(cl):
    h = prijava(cl)
    d = cl.post("/voznik/sporocilo", json={"besedilo": "Vozilo je obstalo."}, headers=h).json()["predlogi_dogodkov"][0]
    assert cl.post(f"/dogodek/{d['id']}/zavrni", json={"razlog": "lažni alarm"}).json()["stanje"] == "zavrnjeno"
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] != "okvara"


def test_naloga_voznika_ima_navodila_in_opozorila(cl):
    plan = cl.get("/plan/aktiven").json()
    eid = next(e for e, ks in plan["koraki_po_enoti"].items() if ks and e.startswith("RV"))
    h = prijava(cl, eid, "DEMO-" + eid.replace("-", ""))
    n = cl.get("/voznik/naloga", headers=h).json()
    assert n["pot"] and {"navodila", "opozorila", "geometrija"} <= set(n["pot"])
    assert brez_ponudnikov(str(n)) == []


def test_ime_s_sumniki_v_glavi(cl):
    kid = cl.post("/klic/zacni", json={}, headers={"X-Uporabnik": quote("Žiga Čeč")}).json()["id"]
    assert cl.get("/revizija", params={"objekt_id": kid}).json()[0]["uporabnik"] == "Žiga Čeč"


def test_stari_nezavarovani_endpointi_ne_obstajajo(cl):
    assert cl.post("/enota/RV-1/lokacija", json={"lat": 46, "lon": 14}).status_code in (404, 405)
    assert cl.post("/sporocilo", json={"enota_id": "RV-1", "besedilo": "x"}).status_code in (404, 405)
