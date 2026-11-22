"""Demo pogovori iz demo/klici.json: vsak s svojo številko v čakalni vrsti; po koncu klica je na vrsti naslednji,
ko so vsi odigrani, se vrsta napolni znova. Besedilni pogovori se predvajajo z realnimi časi."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve

KLICI = json.loads((Path(__file__).parent.parent / "demo" / "klici.json").read_text(encoding="utf-8"))["klici"]


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def test_vsi_pogovori_v_json_razlicne_stevilke():
    assert len(KLICI) >= 3 and len({k["stevilka"] for k in KLICI}) == len(KLICI)
    assert sum(1 for k in KLICI if k.get("posnetki")) == 1
    for k in KLICI:
        for s in k.get("segmenti", []):
            assert s["govorec"] in ("dispecer", "klicatelj") and s["do_s"] >= s["od_s"] and s["besedilo"].strip()


def test_vrsta_po_vrsti_in_naslednji_po_koncu(cl):
    q = cl.get("/cakajoci").json()
    assert [x["stevilka"] for x in q] == [k["stevilka"] for k in KLICI] and q[0]["zvok"] and not q[1]["zvok"]
    # drugi pogovor (besedilni) za svojo številko: segmenti iz datoteke
    k = cl.post("/demo/klic", json={"hitrost": 1000, "cakajoci_id": q[1]["id"]}).json()
    assert k["stevilka"] == KLICI[1]["stevilka"]
    cl.portal.call(cl.center.pocakaj_ozadje)
    seg = cl.get(f"/klic/{k['id']}").json()["segmenti"]
    assert [s["besedilo"] for s in seg] == [s["besedilo"] for s in KLICI[1]["segmenti"]]
    assert any(p["polje"] == "vrsta_dogodka" for p in cl.get(f"/klic/{k['id']}/predlogi").json())  # AI dela iz pogovora
    cl.post(f"/klic/{k['id']}/koncaj")
    ostali = cl.get("/cakajoci").json()
    assert [x["stevilka"] for x in ostali] == [KLICI[0]["stevilka"], KLICI[2]["stevilka"]]


def test_ko_so_vsi_odigrani_se_vrsta_napolni(cl):
    for _ in range(len(KLICI)):
        q = [x for x in cl.get("/cakajoci").json() if not x["zvok"]]
        if not q:  # ostal je zvočni: začni ga brez predvajanja posnetkov
            q = cl.get("/cakajoci").json()
            k = cl.post("/klic/zacni", json={"cakajoci_id": q[0]["id"]}).json()
        else:
            k = cl.post("/demo/klic", json={"hitrost": 1000, "cakajoci_id": q[0]["id"]}).json()
        cl.post(f"/klic/{k['id']}/koncaj")
    nova = cl.get("/cakajoci").json()
    assert [x["stevilka"] for x in nova] == [k["stevilka"] for k in KLICI]  # znova od začetka
    assert len({x["id"] for x in nova}) == len(KLICI)
    st = [d for d in cl.get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == "cakajoci"]
    assert st and len(st[-1]["podatki"]) == len(KLICI)  # vmesnik dobi novo vrsto takoj


def test_konec_klica_ustavi_predvajanje(cl):
    q = [x for x in cl.get("/cakajoci").json() if not x["zvok"]][0]
    k = cl.post("/demo/klic", json={"hitrost": 1, "cakajoci_id": q["id"]}).json()
    cl.post(f"/klic/{k['id']}/koncaj")
    cl.portal.call(cl.center.pocakaj_ozadje)
    assert cl.get(f"/klic/{k['id']}").json()["segmenti"] == []
