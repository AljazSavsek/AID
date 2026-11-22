"""Ovira na poti: najprej obvoz (dispečer spremeni pot), nato prerazporeditev, ko vozilo obtiči."""
import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve
from usmerjanje.ponudniki import Pot, Ponudnik, zracna_ocena
from usmerjanje.servis import UsmerjevalniServis


class Ravna(Ponudnik):
    """Lažen ponudnik s prometom: ravna pot z gostimi točkami; z izogibanjem nekoliko daljša in drugačna."""
    ime = "tomtom"
    kakovost = "natančno"
    podpira_izogibanje = True
    klici_z_izogibanjem = 0

    async def izracunaj(self, od, do, tip, izogibaj=None):
        o = zracna_ocena(od, do, tip)
        n = 30
        odmik = 0.004 if izogibaj else 0.0
        geo = [[od[0] + (do[0] - od[0]) * i / n + (odmik if 0 < i < n else 0), od[1] + (do[1] - od[1]) * i / n] for i in range(n + 1)]
        if izogibaj:
            Ravna.klici_z_izogibanjem += 1
        return Pot(o.cas_s + (90 if izogibaj else 0), o.razdalja_m * (1.1 if izogibaj else 1), geo, self.kakovost,
                   navodila=[{"sporocilo": "Zavijte desno", "odmik_m": 200, "ulica": "Obvozna", "manever": "TURN_RIGHT"}])


@pytest.fixture()
def cl():
    c = Center(usmerjanje=UsmerjevalniServis(ponudniki=[Ravna()]), nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.4, cas_alternative_s=0.2))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def voznik(cl, eid):
    koda = "DEMO-" + eid.replace("-", "")
    return {"Authorization": "Bearer " + cl.post("/voznik/prijava", json={"enota_id": eid, "koda": koda, "naprava_id": "t-" + eid}).json()["dostop"]}


def enota_na_poti(cl):
    """Reševalno vozilo, ki res vozi do prvega koraka (ne stoji že na mestu prevzema)."""
    from usmerjanje.ponudniki import zracna_razdalja_m
    plan = cl.get("/plan/aktiven").json()
    enote = {e["id"]: e for e in cl.get("/enote").json()}
    eid = next(e for e, ks in plan["koraki_po_enoti"].items() if ks and e.startswith("RV") and e != "RV-1"
               and zracna_razdalja_m((enote[e]["lat"], enote[e]["lon"]), (ks[0]["lat"], ks[0]["lon"])) > 500)
    h = voznik(cl, eid)
    n = cl.get("/voznik/naloga", headers=h).json()
    assert n["pot"]
    return eid, h, n


def test_zapora_obvoz_dispecer_potrdi_voznik_prejme(cl):
    eid, h, n = enota_na_poti(cl)
    r = cl.post("/demo/ovira", json={"faza": "zapora", "enota_id": eid}).json()
    vpliv = [x for x in r["vpliv"] if x["enota_id"] == eid]
    assert vpliv and vpliv[0]["obvoz_mogoc"] and not vpliv[0]["ujeta"]
    # predlog obvoza: sedanja proti novi poti, ki se izogne oviri; brez potrditve se pri vozniku nič ne spremeni
    p = cl.get(f"/enote/{eid}/obvoz").json()
    assert p["mogoc"] and p["nova"]["izogiba_oviram"] and p["razlika_s"] == 90 and brez_ponudnikov(str(p)) == []
    assert not cl.get("/voznik/naloga", headers=h).json()["pot"].get("obvoz")
    po = cl.post(f"/enote/{eid}/obvoz").json()
    assert po["obvoz"] and r["dogodek"]["id"] in po["ovire"]
    n2 = cl.get("/voznik/naloga", headers=h).json()
    assert n2["pot"]["obvoz"] and n2["pot"]["navodila"] and n2["ovire"]
    assert any(m["smer"] == "od_dispecerja" and "OBVOZ" in m["besedilo"] for m in n2["sporocila"])
    # ovira ni več na poti tega vozila
    assert eid not in {x["enota_id"] for x in cl.get("/ovire/vpliv").json()["vpliv"]}
    assert any(x["akcija"] == "potrdi_obvoz" and x["uporabnik"] == "M. Novak" for x in cl.get("/revizija").json())


def test_obtical_druga_enota(cl):
    eid, h, n = enota_na_poti(cl)
    nid = next(k["naloga_id"] for k in n["koraki"] if k["trenutni"])
    r = cl.post("/demo/ovira", json={"faza": "obtical", "enota_id": eid}).json()
    v = [x for x in r["vpliv"] if x["enota_id"] == eid][0]
    assert v["ujeta"] and not v["obvoz_mogoc"]
    assert cl.get(f"/enote/{eid}/obvoz").json()["mogoc"] is False
    assert cl.post(f"/enote/{eid}/obvoz").status_code == 409
    kand = cl.get(f"/naloge/{nid}/kandidati").json()
    obt = next(x for x in kand if x["enota_id"] == eid)
    assert obt["obtical"]
    neprim = [x for x in kand if not x["primerno"]]
    for x in neprim:  # vozilo, ki naloge ne more opraviti, ni ponujeno in ga ni mogoče dodeliti
        assert cl.post(f"/naloge/{nid}/dodeli", json={"enota_id": x["enota_id"]}).status_code == 422
    nova = next(x["enota_id"] for x in kand if x["primerno"] and not x.get("obtical") and x["enota_id"] != eid)
    plan = cl.post(f"/naloge/{nid}/dodeli", json={"enota_id": nova}).json()["plan_predlog"]
    assert any(k["naloga_id"] == nid for k in plan["koraki_po_enoti"][nova])
    assert not any(k["naloga_id"] == nid for k in plan["koraki_po_enoti"].get(eid, []))
    cl.post(f"/plan/{plan['id']}/potrdi")
    # novo vozilo dobi nalogo; staro obvestilo, da je nalogo prevzela druga enota
    hn = voznik(cl, nova)
    assert any(k["naloga_id"] == nid for k in cl.get("/voznik/naloga", headers=hn).json()["koraki"])
    stara = cl.get("/voznik/naloga", headers=h).json()
    assert any([nid, "prevzem"] in o["razlike"]["odstranjeni"] for o in stara["obvestila"] + [stara["zadnje_obvestilo"]])
    assert any(x["akcija"] == "prerazporedi" for x in cl.get("/revizija", params={"objekt_id": nid}).json())


def test_ovira_brez_prijave_zavrnjena(cl):
    with TestClient(cl.app, headers={"X-Brez-Prijave": "1"}) as anon:
        assert anon.post("/demo/ovira", json={"faza": "zapora"}).status_code == 401
        assert anon.get("/ovire/vpliv").status_code == 401
