"""Voznik M2: naloga iz aktivnega plana, dovoljeni prehodi stanj, potrditev prejema z razliko,
pot za trenutni korak, WebSocket in polling za napravo."""

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve
from voznik.nastavitve import NastavitveVoznika

HITRO = Nastavitve(casovna_omejitev_s=0.3, cas_alternative_s=0.1, alternative=False)


@pytest.fixture()
def cl():
    c = Center(nastavitve_planerja=HITRO, nastavitve_voznika=NastavitveVoznika(skrivnost=b"k" * 32))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        yield k


def koda(eid):
    return "DEMO-" + eid.replace("-", "")


def prijava(cl, eid):
    z = cl.post("/voznik/prijava", json={"enota_id": eid, "koda": koda(eid), "naprava_id": "tel"}).json()
    return {"Authorization": "Bearer " + z["dostop"]}, z


def enota_s_koraki(c, najmanj=2, tip="RV"):
    p = c.plan_aktiven()
    return next(e for e, ks in p["koraki_po_enoti"].items() if len(ks) >= najmanj and e.startswith(tip))


def naloga(cl, H):
    r = cl.get("/voznik/naloga", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def stanje(cl, H, st, n=None):
    k = next((x for x in (n or naloga(cl, H))["koraki"] if x["trenutni"]), None)
    telo = {"stanje": st, **({"naloga_id": k["naloga_id"], "akcija": k["akcija"]} if k else {})}
    return cl.post("/voznik/stanje", headers=H, json=telo)


def test_naloga_iz_aktivnega_plana(cl):
    c = cl.center
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    n = naloga(cl, H)
    plan = c.plan_aktiven()
    assert [(k["naloga_id"], k["akcija"]) for k in n["koraki"]] == [(k["naloga_id"], k["akcija"]) for k in plan["koraki_po_enoti"][eid]]
    assert set(n["koraki"][0]) >= {"naloga_id", "akcija", "nujna", "eta", "zamuda_s", "prioriteta", "lat", "lon", "naloga"}
    assert n["plan"]["id"] == plan["id"] and n["dovoljena_stanja"] == ["prejeto"]
    assert n["pot"]["cas_s"] > 0 and n["pot"]["geometrija"] and n["pot"]["kakovost"] == "ocena"  # brez ključev
    assert n["obvestila"] and n["obvestila"][0]["stanje"] == "caka"
    assert not brez_ponudnikov(cl.get("/voznik/naloga", headers=H).text)


def test_dovoljeni_prehodi_in_revizija(cl):
    c = cl.center
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    assert stanje(cl, H, "na_poti").status_code == 409       # najprej potrditev prejema
    assert stanje(cl, H, "dostavljeno").status_code == 409
    assert stanje(cl, H, "prejeto").status_code == 200
    assert stanje(cl, H, "prejeto").status_code == 200        # ponovljen zahtevek = idempotenten
    assert stanje(cl, H, "na_prizoriscu").status_code == 409  # preskok
    assert stanje(cl, H, "na_poti").status_code == 200
    assert stanje(cl, H, "ni_na_voljo").status_code == 409    # ima aktivne korake
    k = next(x for x in naloga(cl, H)["koraki"] if x["trenutni"])
    r = cl.post("/voznik/stanje", headers=H, json={"stanje": "na_prizoriscu", "naloga_id": "N-NI", "akcija": "prevzem"})
    assert r.status_code == 409 and "aktualen" in r.json()["napaka"]
    rev = [x for x in c.db.revizije(k["naloga_id"]) if x["akcija"] == "stanje_voznika"]
    assert [x["nova_vrednost"]["faza"] for x in rev] == ["prejeto", "na_poti"]
    assert all(x["uporabnik"] == f"voznik {eid}" for x in rev)


def test_neznano_stanje_422(cl):
    H, _ = prijava(cl, enota_s_koraki(cl.center))
    assert cl.post("/voznik/stanje", headers=H, json={"stanje": "izmisljeno"}).status_code == 422
    assert cl.post("/voznik/stanje", headers=H, json={"stanje": "NA POTI!"}).status_code == 422


def test_celoten_cikel_prevoza(cl):
    """prevzem -> pacient naložen (tovor) -> dostava -> dostavljeno (naloga opravljena, center +1) -> prosto."""
    c = cl.center
    plan = c.plan_aktiven()
    eid = next(e for e, ks in plan["koraki_po_enoti"].items()
               if any(k["akcija"] == "prevzem" and c.db.beri("naloga", k["naloga_id"]).get("dostava") and
                      c.db.beri("naloga", k["naloga_id"])["kolicina"]["pacienti"] for k in ks) and e.startswith("RV"))
    H, _ = prijava(cl, eid)
    koraki = len(naloga(cl, H)["koraki"])
    opravljene, nalozene = [], []
    for _ in range(4 * koraki + 2):
        n = naloga(cl, H)
        if not n["dovoljena_stanja"] or n["dovoljena_stanja"] == ["ni_na_voljo"]:
            break
        st = n["dovoljena_stanja"][0]
        k = next((x for x in n["koraki"] if x["trenutni"]), None)
        r = stanje(cl, H, st, n)
        assert r.status_code == 200, r.text
        if st == "pacient_nalozen":
            nalozene.append(k["naloga_id"])
            assert k["naloga_id"] in r.json()["enota"]["tovor"]
            assert r.json()["faza"] == "prejeto"  # naslednji korak je že prejet
        if st in ("dostavljeno", "opravljeno"):
            opravljene.append(k["naloga_id"])
    e = c.db.beri("enota", eid)
    assert e["stanje"] == "prosto" and e["tovor"] == [] and naloga(cl, H)["dovoljena_stanja"] == ["ni_na_voljo"]
    assert all(c.db.beri("naloga", x)["stanje"] == "opravljena" for x in opravljene)
    s_pacienti = [x for x in nalozene if c.db.beri("naloga", x)["kolicina"]["pacienti"] and c.db.beri("naloga", x)["dostava"].get("center_id")]
    assert s_pacienti
    centri = {cc["id"]: cc["zasedena"] for cc in c.db.vsi("center")}
    assert sum(centri.values()) == 2 + 1 + len(s_pacienti)  # scenarij: UKC 2 + PD 1
    # izmena
    assert stanje(cl, H, "ni_na_voljo").json()["enota"]["stanje"] == "ni_na_voljo"
    assert stanje(cl, H, "prosto").json()["enota"]["stanje"] == "prosto"


def test_nujna_voznja_brez_cilja_samo_opravljeno(cl):
    c = cl.center
    plan = c.plan_aktiven()
    eid, k = next((e, k) for e, ks in plan["koraki_po_enoti"].items() for k in ks[:1] if k["nujna"])
    H, _ = prijava(cl, eid)
    for st in ("prejeto", "na_poti", "na_prizoriscu"):
        assert stanje(cl, H, st).status_code == 200
    assert naloga(cl, H)["dovoljena_stanja"] == ["opravljeno"]
    assert stanje(cl, H, "pacient_nalozen").status_code == 409
    assert stanje(cl, H, "opravljeno").status_code == 200
    assert c.db.beri("naloga", k["naloga_id"])["stanje"] == "opravljena"


def test_ni_na_voljo_izkljuci_iz_plana(cl):
    c = cl.center
    plan = c.plan_aktiven()
    prosta = next(e["id"] for e in c.db.vsi("enota") if not plan["koraki_po_enoti"].get(e["id"]) and e["tip"] != "helikopter")
    H, _ = prijava(cl, prosta)
    assert stanje(cl, H, "ni_na_voljo").status_code == 200
    nov = cl.portal.call(c.predlagaj_plan, "test")
    assert prosta not in nov["koraki_po_enoti"]
    cl.post(f"/plan/{nov['id']}/potrdi")
    assert c.db.beri("enota", prosta)["stanje"] == "ni_na_voljo"  # potrditev plana ga ne vrne v »prosto«


def test_stanje_voznika_ni_povozeno_med_planiranjem(cl):
    """Planer med računanjem ne sme povoziti opravljene naloge s starim posnetkom."""
    c = cl.center
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    nid = naloga(cl, H)["koraki"][0]["naloga_id"]

    async def planiraj_in_vmes_opravi():
        import asyncio
        t = asyncio.ensure_future(c.predlagaj_plan("test"))
        await asyncio.sleep(0)
        n = c.db.beri("naloga", nid)
        n["stanje"] = "opravljena"
        c.db.shrani("naloga", n)
        await t
    cl.portal.call(planiraj_in_vmes_opravi)
    assert c.db.beri("naloga", nid)["stanje"] == "opravljena"


def test_potrditev_prejema_z_razliko(cl):
    c = cl.center
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    o = naloga(cl, H)["obvestila"][0]
    assert o["razlike"]["dodani"] and o["rok"] > o["ustvarjeno"]
    seq = c.objava.seq
    r = cl.post("/voznik/potrdi", headers=H, json={"obvestilo_id": o["id"]}).json()
    assert r["obvestila"] == [] and r["faza"] == "prejeto"
    potrditve = [d for d in c.objava.od(seq) if d["tip"] == "voznik_potrditev"]
    assert potrditve and potrditve[0]["podatki"]["pravocasno"] is True
    assert any(x["akcija"] == "potrditev_prejema" for x in c.db.revizije(o["plan_id"]))
    # tuje obvestilo
    H2, _ = prijava(cl, next(e for e in c.plan_aktiven()["koraki_po_enoti"] if e != eid))
    assert cl.post("/voznik/potrdi", headers=H2, json={"obvestilo_id": o["id"]}).status_code == 404
    # dispečer vidi status
    e = next(x for x in cl.get("/enote").json() if x["id"] == eid)
    assert e["voznik"]["prijavljen"] and e["voznik"]["obvestilo"]["stanje"] == "potrjeno" and e["voznik"]["faza"] == "prejeto"


def test_nov_plan_obvesti_samo_spremenjene(cl):
    c = cl.center
    star = c.plan_aktiven()
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    cl.post("/voznik/potrdi", headers=H, json={"obvestilo_id": naloga(cl, H)["obvestila"][0]["id"]})
    # okvara druge enote -> nov plan; potrdi
    druga = next(e for e, ks in star["koraki_po_enoti"].items() if ks and e != eid and e.startswith("RV"))
    r = cl.post("/dogodek", json={"tip": "okvara_vozila", "parametri": {"enota_id": druga}}).json()
    nov = r["plan_predlog"]
    cl.post(f"/plan/{nov['id']}/potrdi")
    spremenjene = {e for e in set(nov["koraki_po_enoti"]) | set(star["koraki_po_enoti"])
                   if [(k["naloga_id"], k["akcija"]) for k in cl.center.naloge_voznikov.preostali(e, nov)] !=
                   [(k["naloga_id"], k["akcija"]) for k in cl.center.naloge_voznikov.preostali(e, star)]}
    ob = {e: c.naloge_voznikov.zadnje_obvestilo(e) for e in spremenjene}
    assert all(o and o["plan_id"] == nov["id"] for o in ob.values())
    if eid not in spremenjene:
        assert c.naloge_voznikov.zadnje_obvestilo(eid)["plan_id"] == star["id"]
        assert naloga(cl, H)["obvestila"] == []


def test_ws_voznik_avtentikacija_in_dogodki(cl):
    c = cl.center
    eid = enota_s_koraki(c)
    H, z = prijava(cl, eid)
    with cl.websocket_connect("/ws/voznik") as ws:
        ws.send_json({"zeton": "narobe"})
        assert ws.receive_json()["tip"] == "napaka"
    with cl.websocket_connect("/ws/voznik") as ws:
        ws.send_json({"zeton": z["dostop"], "od": 0})
        pozdrav = ws.receive_json()
        assert pozdrav["tip"] == "pozdrav" and pozdrav["podatki"]["enota_id"] == eid
        prvi = ws.receive_json()  # zgodovina: obvestilo o planu
        assert prvi["tip"] in ("plan", "pot")
        stanje(cl, H, "prejeto")
        tipi = set()
        for _ in range(5):
            m = ws.receive_json()
            tipi.add(m["tip"])
            if m["tip"] == "naloga":
                break
        assert "naloga" in tipi
    # druga enota ne dobi dogodkov te enote
    o = c.naloge_voznikov.objava
    druga = next(e["id"] for e in c.db.vsi("enota") if e["id"] != eid)
    assert all(d["podatki"].get("obvestilo", {}).get("enota_id", druga) == druga for d in o.od(druga, 0))


def test_polling_dogodki_in_ponovni_zagon(cl):
    c = cl.center
    eid = enota_s_koraki(c)
    H, _ = prijava(cl, eid)
    r = cl.get("/voznik/dogodki?od=0", headers=H).json()
    assert r["dogodki"] and r["zagon"] == c.naloge_voznikov.objava.zagon
    zadnji = r["seq"]
    assert cl.get(f"/voznik/dogodki?od={zadnji}&zagon={r['zagon']}", headers=H).json()["dogodki"] == []
    # drug zagon strežnika -> vse znova
    assert cl.get(f"/voznik/dogodki?od={zadnji}&zagon=star", headers=H).json()["dogodki"]
    assert cl.get("/voznik/dogodki?od=0").status_code == 401


def test_voznik_ne_spreminja_tuje_naloge(cl):
    c = cl.center
    plan = c.plan_aktiven()
    a, b = [e for e, ks in plan["koraki_po_enoti"].items() if ks][:2]
    Ha, _ = prijava(cl, a)
    Hb, _ = prijava(cl, b)
    kb = plan["koraki_po_enoti"][b][0]
    r = cl.post("/voznik/stanje", headers=Ha, json={"stanje": "prejeto", "naloga_id": kb["naloga_id"], "akcija": kb["akcija"]})
    assert r.status_code == 409
    assert naloga(cl, Hb)["faza"] is None and c.naloge_voznikov.faza(b) is None
    ob = naloga(cl, Hb)["obvestila"][0]
    assert cl.post("/voznik/potrdi", headers=Ha, json={"obvestilo_id": ob["id"]}).status_code == 404
    assert naloga(cl, Hb)["obvestila"][0]["stanje"] == "caka"
