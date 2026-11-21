"""Dispečer sam izbere vozilo (prosta najprej, po času prihoda); izbira je zavezujoča za planer."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve

SEG = json.loads((Path(__file__).parent.parent / "demo" / "klic1.json").read_text(encoding="utf-8"))["segmenti"]


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.5, cas_alternative_s=0.2))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def klic_z_lokacijo(cl):
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    for s in SEG:
        cl.post(f"/klic/{kid}/segment", json=s)
    cl.portal.call(cl.center.pocakaj_ozadje)
    cl.post(f"/klic/{kid}/sprejmi-vse")
    for polje in ("naslov", "lokacija", "prioriteta"):
        p = [x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == polje and x["stanje"] == "predlagano"]
        if p:
            cl.post(f"/predlog/{p[0]['id']}/sprejmi")
    return kid


def enota_predlog(cl, kid):
    return next(x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota" and x["stanje"] != "zastarelo")


def test_seznam_vseh_vozil_prosta_najprej(cl):
    kid = klic_z_lokacijo(cl)
    seznam = enota_predlog(cl, kid)["dodatno"]["enote"]
    razpolozljive = [e for e in cl.get("/enote").json() if e["stanje"] not in ("okvara", "ni_na_voljo")]
    assert len(seznam) == len(razpolozljive) > 3
    stanja = [x["stanje"] == "prosto" for x in seznam]
    assert stanja == sorted(stanja, reverse=True)  # prosta najprej
    prosti = [x["cas_s"] for x in seznam if x["stanje"] == "prosto"]
    assert prosti == sorted(prosti)  # po času prihoda


@pytest.mark.parametrize("izbira", ["MOT-2", "RV-4"])
def test_izbira_dispecerja_je_zavezujoca(cl, izbira):
    kid = klic_z_lokacijo(cl)
    p = enota_predlog(cl, kid)
    assert cl.post(f"/predlog/{p['id']}/spremeni", json={"vrednost": izbira}).json()["vrednost"] == izbira
    r = cl.post(f"/klic/{kid}/odobri").json()
    nid = r["naloga"]["id"]
    for plan in [r["plan_predlog"]] + cl.get(f"/plan/{r['plan_predlog']['id']}/alternative").json():
        kdo = [e for e, ks in plan["koraki_po_enoti"].items() if any(k["naloga_id"] == nid for k in ks)]
        assert kdo == [izbira], plan["oznaka"]
    cl.post(f"/plan/{r['plan_predlog']['id']}/potrdi")
    koda = "DEMO-" + izbira.replace("-", "")
    h = {"Authorization": "Bearer " + cl.post("/voznik/prijava", json={"enota_id": izbira, "koda": koda, "naprava_id": "t"}).json()["dostop"]}
    n = cl.get("/voznik/naloga", headers=h).json()
    assert any(k["naloga_id"] == nid for k in n["koraki"]) and n["obvestila"]


def test_okvara_izbranega_vozila_prerazporedi(cl):
    kid = klic_z_lokacijo(cl)
    p = enota_predlog(cl, kid)
    cl.post(f"/predlog/{p['id']}/spremeni", json={"vrednost": "RV-3"})
    r = cl.post(f"/klic/{kid}/odobri").json()
    cl.post(f"/plan/{r['plan_predlog']['id']}/potrdi")
    nov = cl.post("/dogodek", json={"tip": "okvara_vozila", "parametri": {"enota_id": "RV-3", "tovor_ostane": True}}).json()["plan_predlog"]
    kdo = [e for e, ks in nov["koraki_po_enoti"].items() if any(k["naloga_id"] == r["naloga"]["id"] for k in ks)]
    assert kdo and kdo != ["RV-3"]
