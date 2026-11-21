"""Voznik M1: identiteta, dovoljenja, idempotenten in urejen sprejem položajev,
čakalna vrsta brez povezave, signal, enota_premik."""
import random
import time

import pytest
from fastapi.testclient import TestClient

from api.app import objavi_signale, ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve
from voznik.nastavitve import NastavitveVoznika
from voznik.omejitve import Omejevalnik

HITRO = Nastavitve(casovna_omejitev_s=0.3, cas_alternative_s=0.1, alternative=False)


@pytest.fixture()
def cl(monkeypatch):
    monkeypatch.setenv("AID_SKRBNIK_ZETON", "skrbnik-test")
    c = Center(nastavitve_planerja=HITRO, nastavitve_voznika=NastavitveVoznika(skrivnost=b"k" * 32))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def prijava(cl, enota="RV-2", koda="DEMO-RV2", naprava="tel-a"):
    r = cl.post("/voznik/prijava", json={"enota_id": enota, "koda": koda, "naprava_id": naprava})
    assert r.status_code == 200, r.text
    return r.json()


def glava(z):
    return {"Authorization": "Bearer " + z["dostop"]}


def fiks(seq, ts, lat=46.05, lon=14.50, **kw):
    return {"seq": seq, "ts": ts, "lat": lat, "lon": lon, "hitrost": 10.0, "smer": 90.0, "natancnost": 8.0, **kw}


def poslji(cl, z, fiksi, **kw):
    return cl.post("/voznik/polozaj", headers=glava(z), json={"fiksi": fiksi, **kw})


def enota(cl, eid):
    return next(e for e in cl.get("/enote").json() if e["id"] == eid)


# ---------- identiteta ----------
def test_prijava_in_napacna_koda(cl):
    z = prijava(cl)
    assert z["enota"]["id"] == "RV-2" and z["gps"]["posiljanje_s"] > 0
    assert "DEMO" not in cl.get("/enote").text  # kode nikoli ne gredo k odjemalcu
    r = cl.post("/voznik/prijava", json={"enota_id": "RV-2", "koda": "narobe", "naprava_id": "x"})
    assert r.status_code == 401
    assert cl.post("/voznik/prijava", json={"enota_id": "NI-TAKE", "koda": "x", "naprava_id": "x"}).status_code == 401
    akcije = [r["akcija"] for r in cl.center.db.revizije("RV-2")]
    assert "prijava_voznika" in akcije and "prijava_voznika_zavrnjena" in akcije


def test_zaklep_po_napakah(cl):
    for _ in range(5):
        assert cl.post("/voznik/prijava", json={"enota_id": "RV-3", "koda": "x", "naprava_id": "x"}).status_code == 401
    r = cl.post("/voznik/prijava", json={"enota_id": "RV-3", "koda": "DEMO-RV3", "naprava_id": "x"})
    assert r.status_code == 429


def test_brez_zetona_ponarejen_potekel(cl):
    z = prijava(cl)
    t = time.time()
    assert cl.post("/voznik/polozaj", json={"fiksi": [fiks(1, t)]}).status_code == 401
    telo, podpis = z["dostop"].split(".")
    ponarejen = {"Authorization": "Bearer " + telo[:-2] + ("AA" if telo[-2:] != "AA" else "BB") + "." + podpis}
    assert cl.post("/voznik/polozaj", headers=ponarejen, json={"fiksi": [fiks(1, t)]}).status_code == 401
    cl.center.avtentikacija.ura = lambda: time.time() + 3600  # dostopni žeton velja 15 min
    assert poslji(cl, z, [fiks(1, t)]).status_code == 401


def test_osvezitev_se_zamenja_in_ponovna_uporaba_preklice(cl):
    z = prijava(cl)
    z2 = cl.post("/voznik/osvezi", json={"osvezitev": z["osvezitev"]}).json()
    assert z2["osvezitev"] != z["osvezitev"]
    assert poslji(cl, z2, [fiks(1, time.time())]).status_code == 200
    # star osvežitveni žeton -> kraja -> seja preklicana
    assert cl.post("/voznik/osvezi", json={"osvezitev": z["osvezitev"]}).status_code == 401
    assert poslji(cl, z2, [fiks(2, time.time())]).status_code == 401


def test_nova_naprava_odjavi_staro(cl):
    star = prijava(cl, naprava="tel-a")
    nov = prijava(cl, naprava="tel-b")
    assert poslji(cl, star, [fiks(1, time.time())]).status_code == 401
    assert poslji(cl, nov, [fiks(1, time.time())]).status_code == 200


def test_odjava(cl):
    z = prijava(cl)
    assert cl.post("/voznik/odjava", headers=glava(z)).status_code == 200
    assert poslji(cl, z, [fiks(1, time.time())]).status_code == 401


# ---------- dovoljenja ----------
def test_voznik_ne_more_do_druge_enote(cl):
    z = prijava(cl, "RV-2", "DEMO-RV2")
    prej = enota(cl, "RV-3")
    r = poslji(cl, z, [fiks(1, time.time(), 46.1, 14.6)], enota_id="RV-3")
    assert r.status_code == 403
    po = enota(cl, "RV-3")
    assert (po["lat"], po["lon"]) == (prej["lat"], prej["lon"]) and po["polozaj"]["signal"] == "brez_gps"
    assert any(x["akcija"] == "dostop_zavrnjen" for x in cl.center.db.revizije("RV-3"))
    # tuja naprava v telesu
    assert poslji(cl, z, [fiks(1, time.time())], naprava_id="druga").status_code == 403
    # položaj gre vedno na enoto iz žetona
    assert poslji(cl, z, [fiks(2, time.time(), 46.07, 14.51)]).status_code == 200
    assert enota(cl, "RV-2")["lat"] == 46.07 and enota(cl, "RV-3")["lat"] == prej["lat"]


def test_skrbnik_izda_kodo(cl):
    z = prijava(cl)
    assert cl.post("/enote/RV-2/koda").status_code == 403
    r = cl.post("/enote/RV-2/koda", headers={"X-Skrbnik-Zeton": "skrbnik-test", "X-Uporabnik": "M. Novak"})
    koda = r.json()["koda"]
    assert poslji(cl, z, [fiks(1, time.time())]).status_code == 401  # stare seje preklicane
    assert cl.post("/voznik/prijava", json={"enota_id": "RV-2", "koda": "DEMO-RV2", "naprava_id": "x"}).status_code == 401
    prijava(cl, koda=koda)
    assert cl.get("/interno/polozaji/RV-2").status_code == 403
    assert cl.get("/interno/polozaji/RV-2", headers={"X-Skrbnik-Zeton": "skrbnik-test"}).status_code == 200
    assert any(x["akcija"] == "vpogled_zgodovina_polozajev" for x in cl.center.db.revizije("RV-2"))


def test_preverjanje_vhoda(cl):
    z = prijava(cl)
    t = time.time()
    assert poslji(cl, z, [fiks(1, t, lat=100)]).status_code == 422
    assert poslji(cl, z, [fiks(1, t, hitrost=-1)]).status_code == 422
    assert poslji(cl, z, [fiks(i, t) for i in range(201)]).status_code == 422
    assert poslji(cl, z, []).status_code == 422
    r = poslji(cl, z, [fiks(1, t + 3600)]).json()  # čas v prihodnosti
    assert r["zavrnjeni"][0]["razlog"] == "čas v prihodnosti" and r["potrjeno_do_seq"] == 1


# ---------- idempotenca in vrstni red ----------
def test_ponovljen_paket_ne_podvoji(cl):
    z = prijava(cl)
    t = time.time()
    paket = [fiks(i, t - 10 + i, 46.05 + i * 1e-4) for i in range(5)]
    r1 = poslji(cl, z, paket).json()
    r2 = poslji(cl, z, paket).json()
    assert (r1["novi"], r1["podvojeni"]) == (5, 0) and (r2["novi"], r2["podvojeni"]) == (0, 5)
    assert r1["potrjeno_do_seq"] == r2["potrjeno_do_seq"] == 4
    assert len(cl.center.polozaji.zgodovina("RV-2")) == 5


def test_star_fiks_ne_premakne_nazaj(cl):
    z = prijava(cl)
    t = time.time()
    poslji(cl, z, [fiks(10, t, 46.08, 14.52)])
    poslji(cl, z, [fiks(5, t - 30, 46.01, 14.40)])  # zamujen iz čakalne vrste
    e = enota(cl, "RV-2")
    assert (e["lat"], e["lon"]) == (46.08, 14.52)
    assert len(cl.center.polozaji.zgodovina("RV-2")) == 2


def test_ponovno_poslana_vrsta_brez_povezave(cl):
    """Naprava je bila 3 min brez povezave: fiksi z izvirnimi časi, poslani v več paketih in premešano."""
    z = prijava(cl)
    t0 = time.time() - 180
    vrsta = [fiks(i, t0 + i * 2, 46.05 + i * 1e-4, 14.50) for i in range(90)]
    paketi = [vrsta[i:i + 25] for i in range(0, 90, 25)]
    random.Random(1).shuffle(paketi)
    for p in paketi:
        assert poslji(cl, z, p).status_code == 200
    poslji(cl, z, paketi[0])  # ponovni poskus po prekinitvi
    e = enota(cl, "RV-2")
    assert e["lat"] == pytest.approx(46.05 + 89e-4) and e["polozaj"]["ts"] == pytest.approx(t0 + 178)
    zg = cl.center.polozaji.zgodovina("RV-2")
    assert len(zg) == 90 and [f["seq"] for f in zg] == list(range(90))


def test_popravek_ure_naprave(cl):
    z = prijava(cl)
    t = time.time()
    poslji(cl, z, [fiks(1, t - 3600)], poslano_ob=t - 3600)  # ura naprave zaostaja 1 h
    assert enota(cl, "RV-2")["polozaj"]["ts"] == pytest.approx(t, abs=3)


def test_nemogoc_skok_in_lazno(cl):
    z = prijava(cl)
    t = time.time()
    poslji(cl, z, [fiks(1, t - 5, 46.05, 14.50)])
    poslji(cl, z, [fiks(2, t, 46.55, 14.50)])  # 55 km v 5 s
    assert enota(cl, "RV-2")["lat"] == 46.05
    poslji(cl, z, [fiks(3, t + 1, 46.051, 14.50, lazno=True)])
    assert enota(cl, "RV-2")["lat"] == 46.05
    oznake = {f["seq"]: f["oznaka"] for f in cl.center.polozaji.zgodovina("RV-2")}
    assert oznake == {1: None, 2: "skok", 3: "lazno"}


# ---------- signal ----------
def test_signal_in_brez_signala(cl):
    c = cl.center
    z = prijava(cl)
    t = time.time()
    assert enota(cl, "RV-3")["polozaj"]["signal"] == "brez_gps"
    poslji(cl, z, [fiks(1, t)])
    e = dict(c.db.beri("enota", "RV-2"), stanje="prosto")
    p = c.polozaji.trenutni("RV-2")
    zak, brez = c.nast_voznika.pragovi_signala(False, p["interval_s"])
    assert c.polozaji.signal(e, p, t + 1)["signal"] == "v_zivo"
    assert c.polozaji.signal(e, p, t + zak + 1)["signal"] == "zakasnjeno"
    assert c.polozaji.signal(e, p, t + brez + 1)["signal"] == "ni_signala"
    # na nalogi so pragovi strožji (privzeto 30/60 s)
    assert c.polozaji.signal(dict(e, stanje="na_nalogi"), p, t + 61)["signal"] == "ni_signala"
    # nadzor objavi samo prehode
    e = c.db.beri("enota", "RV-2")
    _, brez = c.nast_voznika.pragovi_signala(e["stanje"] == "na_nalogi", p["interval_s"])
    objavi_signale(c, t + 1)
    seq = c.objava.seq
    assert objavi_signale(c, t + 2) == [] and c.objava.seq == seq
    sp = objavi_signale(c, t + brez + 1)
    assert [(s["enota_id"], s["signal"]) for s in sp] == [("RV-2", "ni_signala")]
    tipi = [d["tip"] for d in c.objava.od(seq)]
    assert "enota_signal" in tipi and "opozorilo" in tipi


def test_enota_premik_samo_ws_in_posnetek_za_polling(cl):
    c = cl.center
    z = prijava(cl)
    seq = c.objava.seq
    with cl.websocket_connect(f"/ws?od={seq}") as ws:
        poslji(cl, z, [fiks(1, time.time(), 46.061, 14.511)])
        m = ws.receive_json()
        while m["tip"] != "enota_premik":
            m = ws.receive_json()
    assert m["podatki"]["enota_id"] == "RV-2" and m["podatki"]["signal"] == "v_zivo" and "seq" not in m
    assert all(d["tip"] != "enota_premik" for d in c.objava.od(0))
    st = cl.get("/stanje?od=999999").json()
    rv2 = next(p for p in st["polozaji"] if p["enota_id"] == "RV-2")
    assert (rv2["lat"], rv2["signal"]) == (46.061, "v_zivo")
    besedilo = cl.get("/stanje?od=0").text + str(m)
    assert not brez_ponudnikov(besedilo)


def test_planer_ne_povozi_zivega_polozaja(cl):
    c = cl.center
    z = prijava(cl)
    poslji(cl, z, [fiks(1, time.time(), 46.0612, 14.5123)])
    cl.portal.call(c.predlagaj_plan, "test")
    e = enota(cl, "RV-2")
    assert (e["lat"], e["lon"]) == (46.0612, 14.5123)
    assert "polozaj" not in c.db.beri("enota", "RV-2")


def test_opomba_planu_za_star_polozaj(cl):
    c = cl.center
    z = prijava(cl)
    poslji(cl, z, [fiks(1, time.time() - 30)])
    c.polozaji.ura = lambda: time.time() + 3600
    p = cl.portal.call(c.predlagaj_plan, "test")
    assert any(o.startswith("RV-2: položaj star") for o in p["opombe"])


# ---------- hramba, omejitve ----------
def test_hramba_pocisti_stare(cl):
    c = cl.center
    z = prijava(cl)
    t = time.time()
    poslji(cl, z, [fiks(1, t - 10), fiks(2, t)])
    c.polozaji.ura = lambda: t + c.nast_voznika.hranjenje_dni * 86400 + 5
    assert c.polozaji.pocisti() == 2 and c.polozaji.zgodovina("RV-2") == []


def test_omejevanje():
    ura = [0.0]
    o = Omejevalnik(3, ura=lambda: ura[0])
    assert [o.dovoli("a") for _ in range(4)] == [True, True, True, False]
    assert o.dovoli("b")
    ura[0] += 20  # 1 žeton / 20 s
    assert o.dovoli("a") and not o.dovoli("a")


def test_omejevanje_polozajev(cl):
    z = prijava(cl)
    kode = [poslji(cl, z, [fiks(i, time.time())]).status_code for i in range(61)]
    assert kode[:60] == [200] * 60 and kode[60] == 429


def test_frekvence_glede_na_baterijo():
    n = NastavitveVoznika()
    assert n.frekvence(True, {"nivo": 5})["nacin"] == "aktivno"
    assert n.frekvence(False, {"nivo": 5, "polni": False})["nacin"] == "varcno"
    assert n.frekvence(False, {"nivo": 5, "polni": True})["nacin"] == "prosto"
