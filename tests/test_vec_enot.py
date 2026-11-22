"""Večja nesreča: AI predlaga več enot, dispečer odloči; vsaka potrjena enota dobi svojo nalogo na istem kraju."""
import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve
from test_izbira_vozila import klic_z_lokacijo


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.5, cas_alternative_s=0.2))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def predlog(cl, kid, polje):
    return next((x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == polje and x["stanje"] == "predlagano"), None)


def test_potrebne_enote():
    p = Center.potrebne_enote
    assert p(1, 2, "padec")["vecja"] is False
    assert p(None, 3, "prometna_nesreca")["vecja"] is False
    assert p(3, 2, "prometna_nesreca")["potreba"] == {"vozilo": 3, "motorist": 1, "helikopter": 0}
    assert p(5, 1, "prometna_nesreca")["potreba"] == {"vozilo": 5, "motorist": 1, "helikopter": 1}
    assert p(2, 4, "drugo")["potreba"] == {"vozilo": 2, "motorist": 0, "helikopter": 0}
    assert p(0, 1, "prometna_nesreca")["potreba"]["motorist"] == 1


def test_ena_poskodovana_ni_predloga_vec_enot(cl):
    kid = klic_z_lokacijo(cl)
    cl.post(f"/klic/{kid}/rocno", json={"polje": "stevilo_poskodovanih", "vrednost": 1})
    assert predlog(cl, kid, "dodatne_enote") is None


def test_vecja_nesreca_predlog_in_odobritev(cl):
    kid = klic_z_lokacijo(cl)
    cl.post(f"/klic/{kid}/rocno", json={"polje": "prioriteta", "vrednost": 2})
    cl.post(f"/klic/{kid}/rocno", json={"polje": "stevilo_poskodovanih", "vrednost": 3})
    p = predlog(cl, kid, "dodatne_enote")
    assert p and "3 poškodovani → 3 reševalna vozila" in p["dodatno"]["razlog"] and p["dokazi"]
    glavna = next(x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota" and x["stanje"] != "zastarelo")
    enote = {e["id"]: e for e in cl.get("/enote").json()}
    izbrane = [glavna["vrednost"]] + p["vrednost"]
    tipi = [enote[e]["tip"] for e in izbrane]
    assert len(set(izbrane)) == len(izbrane) and tipi.count("vozilo") <= 3 and tipi.count("motorist") <= 1
    assert all(enote[e]["stanje"] == "prosto" for e in p["vrednost"]) or p["dodatno"]["manjka"] == {}
    # dispečer odloči: sprejme predlog (lahko bi ga uredil ali zavrnil)
    cl.post(f"/predlog/{glavna['id']}/sprejmi")
    assert cl.post(f"/predlog/{p['id']}/sprejmi").json()["stanje"] == "sprejeto"
    r = cl.post(f"/klic/{kid}/odobri").json()
    assert {n["dodeljena_enota"] for n in r["dodatne_naloge"]} == set(p["vrednost"])
    assert all(n["prevzem"]["lat"] == r["naloga"]["prevzem"]["lat"] and n["glavna_naloga"] == r["naloga"]["id"] for n in r["dodatne_naloge"])
    plan = r["plan_predlog"]
    for n in [r["naloga"]] + r["dodatne_naloge"]:
        kdo = [e for e, ks in plan["koraki_po_enoti"].items() if any(k["naloga_id"] == n["id"] for k in ks)]
        assert kdo == [n["dodeljena_enota"]], n["id"]


def test_sprejmi_vse_ne_potrdi_dodatnih_enot(cl):
    kid = klic_z_lokacijo(cl)
    cl.post(f"/klic/{kid}/rocno", json={"polje": "stevilo_poskodovanih", "vrednost": 2})
    assert predlog(cl, kid, "dodatne_enote")
    cl.post(f"/klic/{kid}/sprejmi-vse")
    assert predlog(cl, kid, "dodatne_enote")  # ključna odločitev ostane dispečerju


def test_dispecer_uredi_dodatne_enote(cl):
    kid = klic_z_lokacijo(cl)
    cl.post(f"/klic/{kid}/rocno", json={"polje": "stevilo_poskodovanih", "vrednost": 2})
    p = predlog(cl, kid, "dodatne_enote")
    glavna = next(x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota" and x["stanje"] != "zastarelo")
    cl.post(f"/predlog/{glavna['id']}/sprejmi")
    drugo = next(e["id"] for e in cl.get("/enote").json() if e["id"] not in (glavna["vrednost"], *p["vrednost"]) and e["tip"] == "vozilo")
    assert cl.post(f"/predlog/{p['id']}/spremeni", json={"vrednost": [drugo]}).json()["vrednost"] == [drugo]
    r = cl.post(f"/klic/{kid}/odobri").json()
    assert [n["dodeljena_enota"] for n in r["dodatne_naloge"]] == [drugo]


def test_vec_enot_ze_od_zacetka_odloci_dispecer(cl):
    """Dispečer sme poslati več vozil že od začetka, tudi preden je znano število poškodovanih; nad mejo, ki jo
    utemelji število poškodovanih, je to dovoljeno, a zabeleženo kot opozorilo."""
    kid = klic_z_lokacijo(cl)
    glavna = next(x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota" and x["stanje"] != "zastarelo")
    cl.post(f"/predlog/{glavna['id']}/sprejmi")
    ostale = [e["id"] for e in cl.get("/enote").json() if e["id"] != glavna["vrednost"] and e["stanje"] not in ("okvara", "ni_na_voljo")]
    cl.post(f"/klic/{kid}/rocno", json={"polje": "dodatne_enote", "vrednost": ostale[:2]})
    r = cl.post(f"/klic/{kid}/odobri")
    assert r.status_code == 200, r.text
    d = r.json()
    assert sorted(n["dodeljena_enota"] for n in d["dodatne_naloge"]) == sorted(ostale[:2])
    assert d["opozorilo"] and "utemelji 0" in d["opozorilo"]  # en poškodovani (AI) -> brez dodatnih, a dispečer odloči
    rev = next(x for x in cl.get("/revizija", params={"objekt_id": kid}).json() if x["akcija"] == "odobri_in_poslji")
    assert "opozorilo" in rev["nova_vrednost"]
    plan = d["plan_predlog"]
    for n in d["dodatne_naloge"]:
        assert [e for e, ks in plan["koraki_po_enoti"].items() if any(k["naloga_id"] == n["id"] for k in ks)] == [n["dodeljena_enota"]]


def test_v_meji_ni_opozorila(cl):
    kid = klic_z_lokacijo(cl)
    glavna = next(x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota" and x["stanje"] != "zastarelo")
    cl.post(f"/predlog/{glavna['id']}/sprejmi")
    ostale = [e["id"] for e in cl.get("/enote").json() if e["id"] != glavna["vrednost"] and e["stanje"] not in ("okvara", "ni_na_voljo")]
    cl.post(f"/klic/{kid}/rocno", json={"polje": "stevilo_poskodovanih", "vrednost": 2})
    cl.post(f"/klic/{kid}/rocno", json={"polje": "dodatne_enote", "vrednost": ostale[:3]})
    r = cl.post(f"/klic/{kid}/odobri").json()
    assert len(r["dodatne_naloge"]) == 3 and r["opozorilo"] is None


def test_predlog_ai_je_vedno_v_meji():
    """Kar predlaga AI, mora strežnik ob odobritvi sprejeti (npr. prometna nesreča P1, en poškodovani -> še motorist)."""
    for n in range(0, 7):
        for prio in range(1, 6):
            for vrsta in ("prometna_nesreca", "padec", None):
                assert Center.potrebne_enote(n, prio, vrsta)["skupaj"] - 1 <= Center.najvec_dodatnih(n, prio, vrsta)
    assert Center.najvec_dodatnih(1, 1, "prometna_nesreca") == 1 and Center.najvec_dodatnih(1, 3, "padec") == 0
