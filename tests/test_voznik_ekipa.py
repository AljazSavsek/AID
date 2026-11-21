"""Voznik M3: sporočila, predlogi ekip (okvara, ovira, prevoz z nujne vožnje, ni na voljo) in eskalacije.
Predlog ekipe NE spremeni plana, dokler ga dispečer ne sprejme; nov plan dispečer potrdi posebej."""
import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve
from voznik.nastavitve import NastavitveVoznika

HITRO = Nastavitve(casovna_omejitev_s=0.3, cas_alternative_s=0.1, alternative=False)
D = {"X-Uporabnik": "M. Novak"}


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=HITRO, nastavitve_voznika=NastavitveVoznika(skrivnost=b"k" * 32))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def prijava(cl, eid):
    koda = "DEMO-HELI" if eid == "HELI-1" else "DEMO-" + eid.replace("-", "")
    z = cl.post("/voznik/prijava", json={"enota_id": eid, "koda": koda, "naprava_id": "tel"}).json()
    return {"Authorization": "Bearer " + z["dostop"]}


def naloga(cl, H):
    return cl.get("/voznik/naloga", headers=H).json()


def stanje(cl, H, st):
    k = next((x for x in naloga(cl, H)["koraki"] if x["trenutni"]), None)
    return cl.post("/voznik/stanje", headers=H, json={"stanje": st, **({"naloga_id": k["naloga_id"], "akcija": k["akcija"]} if k else {})})


def dogodki(c, od):
    return [d for d in c.objava.od(od)]


# ---------- sporočila ----------
def test_sporocila_voznik_dispecer(cl):
    c = cl.center
    H = prijava(cl, "RV-2")
    seq = c.objava.seq
    s = cl.post("/voznik/sporocilo", headers=H, json={"koda": "potrebujem_pomoc"}).json()
    assert s["besedilo"] == "Potrebujem pomoč" and s["nujno"] and s["enota_id"] == "RV-2"
    tipi = [(d["tip"], d["podatki"].get("nujno")) for d in dogodki(c, seq)]
    assert ("sporocilo", True) in tipi and ("opozorilo", True) in tipi
    assert cl.post("/voznik/sporocilo", headers=H, json={"besedilo": "Gneča\x07 na obvoznici"}).json()["besedilo"] == "Gneča na obvoznici"
    assert cl.post("/voznik/sporocilo", headers=H, json={"koda": "zamuda", "besedilo": "x"}).status_code == 422
    assert cl.post("/voznik/sporocilo", headers=H, json={"koda": "izmisljeno"}).status_code == 422
    assert cl.post("/voznik/sporocilo", headers=H, json={}).status_code == 422
    # dispečer -> voznik
    m = cl.post("/enote/RV-2/sporocilo", headers=D, json={"koda": "poklici_center"}).json()
    assert m["smer"] == "od_dispecerja" and m["poslal"] == "M. Novak"
    assert any(d["tip"] == "sporocilo" for d in cl.get("/voznik/dogodki?od=0", headers=H).json()["dogodki"])
    assert any(x["id"] == m["id"] for x in naloga(cl, H)["sporocila"])
    # druga enota ga ne vidi in ne more označiti
    H3 = prijava(cl, "RV-3")
    assert all(x["id"] != m["id"] for x in naloga(cl, H3)["sporocila"])
    assert cl.post(f"/voznik/sporocilo/{m['id']}/prebrano", headers=H3).status_code == 404
    assert cl.post(f"/voznik/sporocilo/{m['id']}/prebrano", headers=H).status_code == 200
    assert cl.get("/ekipe/sporocila?enota_id=RV-2").json()[0]["prebrano"]
    akcije = {r["akcija"] for r in c.db.revizije(omejitev=50)}
    assert {"sporocilo_voznika", "sporocilo_dispecerja"} <= akcije
    assert cl.post("/enote/NI-TAKE/sporocilo", headers=D, json={"besedilo": "x"}).status_code == 404


# ---------- okvara ----------
def test_okvara_je_predlog_do_odlocitve(cl):
    c = cl.center
    H = prijava(cl, "RV-2")
    aktiven = c.plan_aktiven()["id"]
    d = cl.post("/voznik/okvara", headers=H, json={"opis": "pregreva se"}).json()
    assert d["vir"] == "ekipa" and d["stanje"] == "predlagano" and d["parametri"]["enota_id"] == "RV-2"
    assert c.db.beri("enota", "RV-2")["stanje"] != "okvara" and c.plan_aktiven()["id"] == aktiven
    assert cl.get("/plan/predlog").json() is None  # planer ni računal
    assert cl.post("/voznik/okvara", headers=H, json={}).status_code == 409  # ena odprta naenkrat
    assert [p["id"] for p in cl.get("/ekipe/predlogi").json()] == [d["id"]]
    # zavrni
    r = cl.post(f"/ekipe/predlog/{d['id']}/zavrni", headers=D, json={"razlog": "preveri ponovno"}).json()
    assert r["dogodek"]["stanje"] == "zavrnjeno" and r["plan_predlog"] is None
    assert naloga(cl, H)["predlogi"][-1]["stanje"] == "zavrnjeno"
    assert cl.post(f"/ekipe/predlog/{d['id']}/sprejmi", headers=D).status_code == 409
    # nov predlog -> sprejmi -> okvara + PREDLOG plana (ne aktiven)
    d2 = cl.post("/voznik/okvara", headers=H, json={}).json()
    r = cl.post(f"/ekipe/predlog/{d2['id']}/sprejmi", headers=D).json()
    assert r["dogodek"]["stanje"] == "sprejeto" and r["plan_predlog"]["stanje"] == "predlog"
    assert c.db.beri("enota", "RV-2")["stanje"] == "okvara" and c.plan_aktiven()["id"] == aktiven
    assert "RV-2" not in r["plan_predlog"]["koraki_po_enoti"]
    rev = [x["akcija"] for x in c.db.revizije(d2["id"])]
    assert "predlog_ekipe" in rev and "sprejmi_predlog_ekipe" in rev
    assert cl.post("/ekipe/predlog/x/izbrisi", headers=D).status_code == 404


# ---------- ovira ----------
def test_ovira_brez_novega_plana(cl):
    c = cl.center
    H = prijava(cl, "RV-3")
    assert cl.post("/voznik/ovira", headers=H, json={"vrsta": "tornado"}).status_code == 422
    d = cl.post("/voznik/ovira", headers=H, json={"vrsta": "zapora", "opis": "drevo čez cesto",
                                                   "lokacija": {"lat": 46.06, "lon": 14.5}}).json()
    assert d["parametri"]["porocal"] == "RV-3"
    r = cl.post(f"/ekipe/predlog/{d['id']}/spremeni", headers=D,
                json={"parametri": {"vrsta": "nesreca", "porocal": "PONAREJENO"}}).json()
    assert r["plan_predlog"] is None and r["dogodek"]["parametri"]["vrsta"] == "nesreca"
    assert r["dogodek"]["parametri"]["porocal"] == "RV-3"  # neurejljivo polje ostane
    ovire = cl.get("/ovire").json()
    assert [o["id"] for o in ovire] == [d["id"]]
    assert not brez_ponudnikov(cl.get("/ovire").text)


# ---------- prevoz z nujne vožnje ----------
def test_prevoz_z_nujne_voznje(cl):
    c = cl.center
    plan = c.plan_aktiven()
    k = next(k for ks in plan["koraki_po_enoti"].values() for k in ks if k["nujna"])
    plan["koraki_po_enoti"] = {**{e: [] for e in plan["koraki_po_enoti"]}, "RV-3": [k]}  # nujna vožnja za vozilo
    c.db.shrani("plan", plan)
    H = prijava(cl, "RV-3")
    for st in ("prejeto", "na_poti", "na_prizoriscu"):
        assert stanje(cl, H, st).status_code == 200
    n = naloga(cl, H)
    assert n["dovoljena_stanja"] == ["opravljeno"] and "pacient_nalozen" in n["mogoci_predlogi"]
    n = stanje(cl, H, "pacient_nalozen").json()
    pr = n["predlogi"][-1]
    assert pr["tip"] == "prevoz_z_nujne_voznje" and pr["stanje"] == "predlagano" and pr["parametri"]["center_id"]
    assert "pacient_nalozen" not in n["mogoci_predlogi"]  # ne dvakrat
    assert c.db.beri("enota", "RV-3")["tovor"] == []  # do odločitve brez sprememb
    r = cl.post(f"/ekipe/predlog/{pr['id']}/spremeni", headers=D, json={"parametri": {"center_id": "C-PD"}}).json()
    assert r["plan_predlog"]
    nal = c.db.beri("naloga", k["naloga_id"])
    assert nal["dostava"]["center_id"] == "C-PD" and nal["kolicina"]["pacienti"] == 1
    assert [t["naloga_id"] for t in c.db.beri("enota", "RV-3")["tovor"]] == [k["naloga_id"]]
    # dostavo mora opraviti isto vozilo (tovor)
    assert {(x["naloga_id"], x["akcija"]) for x in r["plan_predlog"]["koraki_po_enoti"]["RV-3"]} >= {(k["naloga_id"], "dostava")}
    cl.post(f"/plan/{r['plan_predlog']['id']}/potrdi", headers=D)
    n = naloga(cl, H)
    t = next(x for x in n["koraki"] if x["trenutni"])
    assert (t["naloga_id"], t["akcija"]) == (k["naloga_id"], "dostava") and n["obvestila"]


def test_motorist_ne_predlaga_prevoza(cl):
    c = cl.center
    eid = next(e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if ks and ks[0]["nujna"] and e.startswith("MOT"))
    H = prijava(cl, eid)
    for st in ("prejeto", "na_poti", "na_prizoriscu"):
        stanje(cl, H, st)
    assert "pacient_nalozen" not in naloga(cl, H)["mogoci_predlogi"]
    assert stanje(cl, H, "pacient_nalozen").status_code == 409


# ---------- ni na voljo z aktivnimi koraki ----------
def test_ni_na_voljo_z_nalogami_je_predlog(cl):
    c = cl.center
    eid = next(e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if ks and not c.db.beri("enota", e).get("tovor")
               and e.startswith("RV"))
    H = prijava(cl, eid)
    n = stanje(cl, H, "ni_na_voljo").json()
    pr = n["predlogi"][-1]
    assert pr["tip"] == "enota_ni_na_voljo" and c.db.beri("enota", eid)["stanje"] == "na_nalogi"
    r = cl.post(f"/ekipe/predlog/{pr['id']}/sprejmi", headers=D).json()
    assert c.db.beri("enota", eid)["stanje"] == "ni_na_voljo" and eid not in r["plan_predlog"]["koraki_po_enoti"]


# ---------- eskalacije ----------
def test_eskalacija_neprijavljenih_takoj(cl):
    c = cl.center
    esk = {e["enota_id"]: e["razlog"] for e in cl.get("/ekipe/eskalacije").json()}
    s_koraki = {e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if ks}
    assert set(esk) == s_koraki and set(esk.values()) == {"voznik_ni_prijavljen"}
    eid = sorted(s_koraki)[0]
    H = prijava(cl, eid)
    seq = c.objava.seq
    cl.post("/voznik/potrdi", headers=H, json={"obvestilo_id": naloga(cl, H)["obvestila"][0]["id"]})
    assert any(d["tip"] == "eskalacija_razresena" and d["podatki"]["enota_id"] == eid for d in dogodki(c, seq))
    assert eid not in {e["enota_id"] for e in cl.get("/ekipe/eskalacije").json()}


def test_eskalacija_po_roku(cl):
    c = cl.center
    nv = c.naloge_voznikov
    eid = next(e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if ks)
    prijava(cl, eid)
    # nov plan (isti) po prijavi: obvestilo brez takojšnje eskalacije
    nv.db.izvedi("UPDATE voznik_obvestilo SET stanje='zamenjano' WHERE enota_id=?", (eid,))
    nv._po_potrditvi(None, c.plan_aktiven())
    o = nv.obvestila(eid)[0]
    assert o["eskalirano"] is None
    assert [e["enota_id"] for e in nv.preveri_eskalacije(o["rok"] - 1) if e["enota_id"] == eid] == []
    seq = c.objava.seq
    po = [e for e in nv.preveri_eskalacije(o["rok"] + 1) if e["enota_id"] == eid]
    assert po and po[0]["razlog"] == "ni_potrditve"
    assert any(d["tip"] == "opozorilo" and eid in d["podatki"]["sporocilo"] for d in dogodki(c, seq))
    assert [e for e in nv.preveri_eskalacije(o["rok"] + 50) if e["enota_id"] == eid] == []  # samo enkrat
    e = next(x for x in cl.get("/enote").json() if x["id"] == eid)
    assert e["voznik"]["obvestilo"]["razlog_eskalacije"] == "ni_potrditve"
