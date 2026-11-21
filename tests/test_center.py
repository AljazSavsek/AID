"""Pravila potrjevanja, revizijska sled, dogodki in planiranje skozi API."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve

SEGMENTI = json.loads((Path(__file__).parent.parent / "demo" / "klic1.json").read_text(encoding="utf-8"))["segmenti"]
HITRO = Nastavitve(casovna_omejitev_s=0.5, cas_alternative_s=0.2)


@pytest.fixture()
def odjemalec():
    c = Center(nastavitve_planerja=HITRO)
    with TestClient(ustvari_app(c, ozadje=False), headers={"X-Uporabnik": "M. Novak"}) as cl:
        cl.center = c
        yield cl


def pocakaj(cl):
    """AI poslušalec teče v ozadju (ne blokira API) – počakaj, da konča."""
    cl.portal.call(cl.center.pocakaj_ozadje)


def segmenti(cl, kid, n):
    for s in SEGMENTI[:n]:
        assert cl.post(f"/klic/{kid}/segment", json=s).status_code == 200
    pocakaj(cl)


def odprt(cl, kid, polje):
    return [p for p in cl.get(f"/klic/{kid}/predlogi").json() if p["polje"] == polje and p["stanje"] == "predlagano"]


def test_potrjeno_polje_ni_prepisano(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 4)
    naslov = odprt(cl, kid, "naslov")[0]
    assert naslov["vrednost"].startswith("Celovška cesta 150")
    assert cl.post(f"/predlog/{naslov['id']}/sprejmi").json()["stanje"] == "sprejeto"
    for s in SEGMENTI[4:8]:
        cl.post(f"/klic/{kid}/segment", json=s)
    pocakaj(cl)
    vsi = [p for p in cl.get(f"/klic/{kid}/predlogi").json() if p["polje"] == "naslov"]
    potrjen = [p for p in vsi if p["stanje"] == "sprejeto"]
    assert len(potrjen) == 1 and potrjen[0]["vrednost"].startswith("Celovška cesta 150")
    konflikt = [p for p in vsi if p["nasprotuje_potrjenemu"]]
    assert konflikt and konflikt[0]["vrednost"].startswith("Celovška cesta 105") and konflikt[0]["stanje"] == "predlagano"


def test_predlagano_postane_zastarelo(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 8)
    naslovi = [p for p in cl.get(f"/klic/{kid}/predlogi").json() if p["polje"] == "naslov"]
    assert {p["stanje"] for p in naslovi} == {"zastarelo", "predlagano"}


def test_sprejmi_vse_izpusti_prioriteto_in_enoto(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 12)
    sprejeti = {p["polje"] for p in cl.post(f"/klic/{kid}/sprejmi-vse").json()}
    assert sprejeti and not sprejeti & {"prioriteta", "predlagana_enota"}


def test_odobritev_zahteva_potrditve(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 8)
    r = cl.post(f"/klic/{kid}/odobri")
    assert r.status_code == 422 and "prioriteta" in r.json()["napaka"]


def test_celoten_scenarij_in_revizija(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 12)
    # dispečer popravi naslov (spremeni) in potrdi ključna polja posebej
    nasl = odprt(cl, kid, "naslov")[0]
    assert cl.post(f"/predlog/{nasl['id']}/spremeni", json={"vrednost": "Celovška cesta 105, Ljubljana"}).status_code == 200
    pocakaj(cl)
    for polje in ("lokacija", "prioriteta", "predlagana_enota"):
        p = odprt(cl, kid, polje)[0]
        assert cl.post(f"/predlog/{p['id']}/sprejmi").status_code == 200
        pocakaj(cl)
    kont = odprt(cl, kid, "kontakt")[0]
    assert cl.post(f"/predlog/{kont['id']}/zavrni", json={"razlog": "preveri"}).json()["stanje"] == "zavrnjeno"

    t = time.perf_counter()
    r = cl.post(f"/klic/{kid}/odobri").json()
    assert time.perf_counter() - t < 5
    plan = r["plan_predlog"]
    assert plan["stanje"] == "predlog"
    assert any(k["naloga_id"] == r["naloga"]["id"] for ks in plan["koraki_po_enoti"].values() for k in ks)
    assert cl.get("/plan/aktiven").json()["id"] != plan["id"]  # brez potrditve ni aktiven
    assert cl.post(f"/plan/{plan['id']}/potrdi").json()["stanje"] == "aktiven"

    # revizija: vsako dejanje z uporabnikom, časom, staro in novo vrednostjo
    rev = cl.get("/revizija", params={"omejitev": 1000}).json()
    akcije = {(r["akcija"], r["uporabnik"]) for r in rev}
    for a in ("predlog_ai", "spremeni", "sprejmi", "zavrni", "odobri_in_poslji", "potrdi_plan"):
        assert any(x[0] == a for x in akcije), a
    spr = next(r for r in rev if r["akcija"] == "spremeni" and r["objekt_id"] == nasl["id"])
    assert spr["uporabnik"] == "M. Novak" and spr["cas"] > 0
    assert spr["stara_vrednost"]["vrednost"] == nasl["vrednost"] and spr["nova_vrednost"]["vrednost"] == "Celovška cesta 105, Ljubljana"
    p_rev = [r for r in rev if r["objekt_id"] == nasl["id"]]
    assert {r["akcija"] for r in p_rev} >= {"predlog_ai", "spremeni"}


def test_dogodki_okvara_in_megla(odjemalec):
    cl = odjemalec
    r = cl.post("/dogodek", json={"tip": "okvara_vozila", "parametri": {"enota_id": "RV-1", "tovor_ostane": True}}).json()
    plan = r["plan_predlog"]
    assert "RV-1" not in {e for e, ks in plan["koraki_po_enoti"].items() if ks}
    nove = [n for n in cl.get("/naloge").json() if n.get("izvor") == "N-3"]
    assert nove and any(k["naloga_id"] == nove[0]["id"] for ks in plan["koraki_po_enoti"].values() for k in ks)
    assert plan["primerjava"]["razlaga"]
    cl.post(f"/plan/{plan['id']}/potrdi")
    r = cl.post("/dogodek", json={"tip": "megla", "parametri": {"enota_id": "HELI-1", "zamik_min": 60}}).json()
    heli = next(e for e in cl.get("/enote").json() if e["id"] == "HELI-1")
    assert heli["na_voljo_od"] > time.time() + 59 * 60
    for k in r["plan_predlog"]["koraki_po_enoti"].get("HELI-1", []):
        assert k["eta"] >= heli["na_voljo_od"] - 1


def test_okvara_brez_enote_zahteva_dopolnitev(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    cl.post(f"/klic/{kid}/segment", json={"govorec": "klicatelj", "besedilo": "Vozilo je obstalo. Oprema je še v njem."})
    pocakaj(cl)
    d = odprt(cl, kid, "zaznani_dogodek_plana")[0]
    assert cl.post(f"/predlog/{d['id']}/sprejmi").status_code == 400
    v = dict(d["vrednost"], parametri=dict(d["vrednost"]["parametri"], enota_id="RV-1"))
    assert cl.post(f"/predlog/{d['id']}/spremeni", json={"vrednost": v}).status_code == 200
    assert cl.get("/plan/predlog").json()["razlog"].startswith("okvara_vozila")


def test_rocni_vnos_brez_ai(odjemalec):
    cl = odjemalec
    cl.post("/demo/izpad", params={"ai": True})
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 4)
    assert cl.get(f"/klic/{kid}/predlogi").json() == []
    r = cl.post(f"/klic/{kid}/rocno", json={"polje": "naslov", "vrednost": "Celovška cesta 105, Ljubljana"})
    assert r.json()["stanje"] == "spremenjeno" and r.json()["predlagal"] == "dispecer"
    pocakaj(cl)
    lok = odprt(cl, kid, "lokacija")
    assert lok and lok[0]["stanje"] == "predlagano"  # geokodiranje predlaga, ne potrdi


def test_ime_ponudnika_ni_nikjer_v_odgovorih(odjemalec):
    cl = odjemalec
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    segmenti(cl, kid, 12)
    for pot in ("/enote", "/centri", "/naloge", "/plan/aktiven", f"/klic/{kid}", f"/klic/{kid}/predlogi",
                "/eta?lat=46.07&lon=14.49", "/revizija", "/stanje", "/cakajoci"):
        r = cl.get(pot)
        assert r.status_code == 200, pot
        assert brez_ponudnikov(r.text) == [], pot


def test_interno_zdravje_samo_za_skrbnika(odjemalec, monkeypatch):
    cl = odjemalec
    assert cl.get("/interno/zdravje").status_code == 403
    monkeypatch.setenv("AID_SKRBNIK_ZETON", "skrivnost")
    assert cl.get("/interno/zdravje", headers={"X-Skrbnik-Zeton": "napacen"}).status_code == 403
    r = cl.get("/interno/zdravje", headers={"X-Skrbnik-Zeton": "skrivnost"})
    assert r.status_code == 200 and "tomtom" in r.json()["usmerjanje"]


def test_polling_rezerva(odjemalec):
    cl = odjemalec
    seq = cl.get("/stanje").json()["seq"]
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    cl.post(f"/klic/{kid}/segment", json=SEGMENTI[1])
    pocakaj(cl)
    tipi = {d["tip"] for d in cl.get("/stanje", params={"od": seq}).json()["dogodki"]}
    assert {"klic", "segment", "predlog_nov"} <= tipi


def test_websocket(odjemalec):
    cl = odjemalec
    seq = cl.get("/stanje").json()["seq"]
    with cl.websocket_connect(f"/ws?od={seq}") as ws:
        kid = cl.post("/klic/zacni", json={}).json()["id"]
        assert ws.receive_json()["tip"] == "klic"
        cl.post(f"/klic/{kid}/segment", json=SEGMENTI[1])
        assert ws.receive_json()["tip"] == "segment"


def test_ime_iz_seje_s_sumniki_glava_se_ne_uposteva(odjemalec):
    from conftest import prijavi
    prijavi(odjemalec, "Žiga Čeč")
    kid = odjemalec.post("/klic/zacni", json={}, headers={"X-Uporabnik": "Ponarejen"}).json()["id"]
    assert odjemalec.get("/revizija", params={"objekt_id": kid}).json()[0]["uporabnik"] == "Žiga Čeč"
