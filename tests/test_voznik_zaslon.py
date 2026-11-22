"""Spletni zaslon voznika in AI iz besedila ekipe (zavarovano z žetonom voznika).
Spletni zaslon voznika uporablja isti API kot Flet aplikacija."""
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


def test_besedilo_ekipe_ai_predlog(cl):
    """Specifikacija 9: »Vozilo je obstalo. Oprema je še v njem.« -> AI -> predlog okvare (čaka na dispečerja)."""
    h = prijava(cl)
    r = cl.post("/voznik/sporocilo", json={"besedilo": "Vozilo je obstalo. Oprema je še v njem."}, headers=h)
    assert r.status_code == 200 and brez_ponudnikov(r.text) == []
    assert r.json()["predlog_ai"]["tip"] == "okvara_vozila"
    pr = [d for d in cl.get("/ekipe/predlogi").json() if d["tip"] == "okvara_vozila"]
    assert len(pr) == 1 and pr[0]["stanje"] == "predlagano" and pr[0]["parametri"]["enota_id"] == "RV-1"
    assert pr[0]["dokazi"] and pr[0]["zaupanje"]
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] != "okvara"  # brez potrditve nič
    r = cl.post(f"/ekipe/predlog/{pr[0]['id']}/sprejmi").json()
    assert next(e for e in cl.get("/enote").json() if e["id"] == "RV-1")["stanje"] == "okvara"
    rev = {x["akcija"] for x in cl.get("/revizija", params={"omejitev": 1000}).json()}
    assert {"sporocilo_voznika", "predlog_ai"} <= rev


def test_hitro_sporocilo_ne_gre_skozi_ai(cl):
    h = prijava(cl)
    r = cl.post("/voznik/sporocilo", json={"koda": "na_poti"}, headers=h).json()
    assert "predlog_ai" not in r and cl.get("/ekipe/predlogi").json() == []


def test_besedilo_brez_dogodka_je_le_sporocilo(cl):
    h = prijava(cl, "RV-2", "DEMO-RV2")
    r = cl.post("/voznik/sporocilo", json={"besedilo": "Na kraju bomo čez pet minut."}, headers=h).json()
    assert r["predlog_ai"] is None and cl.get("/ekipe/predlogi").json() == []


def test_naloga_voznika_ima_navodila_in_opozorila(cl):
    plan = cl.get("/plan/aktiven").json()
    eid = next(e for e, ks in plan["koraki_po_enoti"].items() if ks and e.startswith("RV"))
    h = prijava(cl, eid, "DEMO-" + eid.replace("-", ""))
    n = cl.get("/voznik/naloga", headers=h).json()
    assert n["pot"] and {"navodila", "opozorila", "geometrija"} <= set(n["pot"])
    assert brez_ponudnikov(str(n)) == []


def test_ime_iz_seje_s_sumniki_glava_se_ne_uposteva(cl):
    from conftest import prijavi
    prijavi(cl, "Žiga Čeč")
    kid = cl.post("/klic/zacni", json={}, headers={"X-Uporabnik": "Ponarejen"}).json()["id"]
    assert cl.get("/revizija", params={"objekt_id": kid}).json()[0]["uporabnik"] == "Žiga Čeč"


def test_stari_nezavarovani_endpointi_ne_obstajajo(cl):
    assert cl.post("/enota/RV-1/lokacija", json={"lat": 46, "lon": 14}).status_code in (404, 405)
    assert cl.post("/sporocilo", json={"enota_id": "RV-1", "besedilo": "x"}).status_code in (404, 405)
    assert cl.get("/voznik/RV-1").status_code in (401, 404, 405)
