"""Heartbeat voznika (vsakih 30 s): hitrost, lokacija, pot -> JSON datoteka v centrali; ob zastoju, oviri
ali prometu centrala vozniku izračuna drugo pot in to javi dispečerju."""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve
from usmerjanje.ponudniki import Pot, Ponudnik
from usmerjanje.servis import UsmerjevalniServis
from voznik.nastavitve import NastavitveVoznika

HITRO = Nastavitve(casovna_omejitev_s=0.3, cas_alternative_s=0.1, alternative=False)


def lok(od, do, zamik, n=40):
    """Pot od->do, ki se v sredini odmakne za `zamik` stopinj (sever/jug) – dve očitno različni poti."""
    out = []
    for i in range(n + 1):
        t = i / n
        izbocenost = zamik * (1 - abs(2 * t - 1))
        out.append([od[0] + (do[0] - od[0]) * t + izbocenost, od[1] + (do[1] - od[1]) * t])
    return out


class Ponudnik2(Ponudnik):
    """Brez izogibanja: pot SEVER. Z izogibanjem (zastoj/ovira) ali ko je `promet` vklopljen: pot JUG."""
    ime, kakovost, podpira_izogibanje = "p", "natančno", True
    promet = False

    async def izracunaj(self, od, do, tip, izogibaj=None):
        jug = bool(izogibaj) or self.promet
        g = lok(od, do, -0.01 if jug else 0.01)
        return Pot(600 if jug else 500, 5000, g, self.kakovost)


@pytest.fixture()
def okolje(tmp_path, monkeypatch):
    monkeypatch.setenv("AID_HEARTBEAT_MAPA", str(tmp_path / "hb"))
    pon = Ponudnik2()
    c = Center(usmerjanje=UsmerjevalniServis(ponudniki=[pon]), nastavitve_planerja=HITRO,
               nastavitve_voznika=NastavitveVoznika(skrivnost=b"k" * 32))
    ura = {"t": 1_800_000_000.0}
    c.heartbeati.ura = lambda: ura["t"]
    with TestClient(ustvari_app(c, ozadje=False)) as cl:
        eid = next(e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if ks and e.startswith("RV"))
        z = cl.post("/voznik/prijava", json={"enota_id": eid, "koda": "DEMO-" + eid.replace("-", ""), "naprava_id": "tel"}).json()
        H = {"Authorization": "Bearer " + z["dostop"]}
        n = cl.get("/voznik/naloga", headers=H).json()
        cl.portal.call(c.pocakaj_ozadje)
        yield {"cl": cl, "c": c, "eid": eid, "H": H, "ura": ura, "pon": pon, "tmp": tmp_path, "naloga": n}


def hb(o, hitrost=40.0, lat=None, lon=None, dt=30):
    c, eid = o["c"], o["eid"]
    o["ura"]["t"] += dt
    e = next(x for x in c.enote() if x["id"] == eid)
    r = o["cl"].post("/voznik/heartbeat", headers=o["H"], json={
        "ts": o["ura"]["t"], "hitrost_kmh": hitrost, "lokacija": {"lat": lat or e["lat"], "lon": lon or e["lon"], "natancnost": 8}})
    assert r.status_code == 200, r.text
    o["cl"].portal.call(c.pocakaj_ozadje)
    return r.json()


def dogodki(o, tip):
    return [d["podatki"] for d in o["cl"].get("/stanje", params={"od": 0}).json()["dogodki"] if d["tip"] == tip]


def test_heartbeat_vsebuje_hitrost_lokacijo_pot_in_se_shrani_v_json(okolje):
    o = okolje
    assert hb(o, 52.5)["interval_s"] == 30
    hb(o, 48.0)
    datoteke = list((o["tmp"] / "hb").glob(f"*/{o['eid']}.json"))
    assert len(datoteke) == 1
    vsebina = json.loads(datoteke[0].read_text(encoding="utf-8"))
    assert len(vsebina) == 2 and [x["hitrost_kmh"] for x in vsebina] == [52.5, 48.0]
    z = vsebina[-1]
    assert set(z["lokacija"]) >= {"lat", "lon"} and z["enota_id"] == o["eid"]
    assert z["pot"]["naloga_id"] and z["pot"]["cilj"] and z["pot"]["preostalo_m"] > 0 and z["pot"]["skupaj_m"] == 5000
    # dispečer: zgodovina in zadnji heartbeat; brez prijave ni dostopa
    assert len(o["cl"].get(f"/enote/{o['eid']}/heartbeat").json()) == 2
    assert o["cl"].get("/heartbeat").json()[0]["hitrost_kmh"] == 48.0
    with TestClient(o["cl"].app, headers={"X-Brez-Prijave": "1"}) as anon:
        assert anon.get("/heartbeat").status_code == 401 and anon.get(f"/enote/{o['eid']}/heartbeat").status_code == 401
    assert o["cl"].get(f"/enote/{o['eid']}/heartbeat", params={"datum": "../x"}).status_code == 422


def test_heartbeat_neveljaven_in_brez_zetona(okolje):
    o = okolje
    with TestClient(o["cl"].app, headers={"X-Brez-Prijave": "1"}) as anon:
        assert anon.post("/voznik/heartbeat", json={"lokacija": {"lat": 46, "lon": 14.5}}).status_code == 401
    assert o["cl"].post("/voznik/heartbeat", headers=o["H"], json={"hitrost_kmh": -5, "lokacija": {"lat": 46, "lon": 14.5}}).status_code == 422
    assert o["cl"].post("/voznik/heartbeat", headers=o["H"], json={"lokacija": {"lat": 146, "lon": 14.5}}).status_code == 422


def test_izpad_heartbeata_opozori_dispecerja(okolje):
    o = okolje
    hb(o)
    o["ura"]["t"] += 100
    assert o["c"].heartbeati.preveri_izpade() == [o["eid"]]
    assert o["c"].heartbeati.preveri_izpade() == []  # le enkrat
    assert any(x.get("vrsta") == "heartbeat" and o["eid"] in x["sporocilo"] for x in dogodki(o, "opozorilo"))


def test_zastoj_vozilo_stoji_dobi_drugo_pot(okolje):
    o = okolje
    stara = o["c"].naloge_voznikov.pot(o["eid"])
    assert stara["geometrija"][20][0] > stara["geometrija"][0][0]  # sever
    hb(o, 35.0)
    hb(o, 2.0)
    assert not dogodki(o, "preusmeritev")  # en heartbeat stanja še ni zastoj
    hb(o, 1.0)
    pr = dogodki(o, "preusmeritev")
    assert len(pr) == 1 and pr[0]["razlog"] == "zastoj" and pr[0]["enota_id"] == o["eid"]
    nova = o["c"].naloge_voznikov.pot(o["eid"])
    assert nova["preusmeritev"]["razlog"] == "zastoj" and nova["geometrija"][20][0] < nova["geometrija"][0][0]  # jug
    # vozniku je šla nova pot, centrala jo ima v reviziji
    assert o["cl"].get("/voznik/naloga", headers=o["H"]).json()["pot"]["preusmeritev"]["razlog"] == "zastoj"
    assert any(r["akcija"] == "preusmeritev" for r in o["cl"].get("/revizija", params={"objekt_id": o["eid"]}).json())
    hb(o, 0.0)
    assert len(dogodki(o, "preusmeritev")) == 1  # ne preusmerja vsakih 30 s


def test_ovira_na_poti_dobi_drugo_pot(okolje):
    o = okolje
    g = o["c"].naloge_voznikov.pot(o["eid"])["geometrija"]
    sredina = g[len(g) // 2]
    o["cl"].post("/dogodek", json={"tip": "ovira_na_cesti", "parametri": {"vrsta": "zapora", "opis": "test",
                                                                          "lokacija": {"lat": sredina[0], "lon": sredina[1]}}})
    hb(o, 50.0)
    pr = dogodki(o, "preusmeritev")
    assert pr and pr[-1]["razlog"] == "ovira" and "zapora" in pr[-1]["opis"]
    assert o["c"].naloge_voznikov.pot(o["eid"])["ovire"]  # nova pot se izogne oviri


def test_promet_hitrejsa_druga_pot(okolje):
    o = okolje
    hb(o, 50.0)
    assert not dogodki(o, "preusmeritev")  # najhitrejša pot je še naša
    o["pon"].promet = True  # ponudnik s trenutnim prometom zdaj predlaga drugo pot
    o["c"].usmerjanje._predpomnilnik.clear()
    hb(o, 45.0, dt=61)
    pr = dogodki(o, "preusmeritev")
    assert pr and pr[-1]["razlog"] == "promet"


def test_blizu_cilja_ni_preusmeritve(okolje):
    o = okolje
    k = o["c"].naloge_voznikov.trenutni(o["eid"])
    hb(o, 1.0, lat=k["lat"] + 0.0005, lon=k["lon"])
    hb(o, 0.0, lat=k["lat"] + 0.0005, lon=k["lon"])
    assert not dogodki(o, "preusmeritev")
