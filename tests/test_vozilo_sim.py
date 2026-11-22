"""Simulator vozila (M4): celoten krog brez strojne opreme in izpad omrežja med vožnjo."""

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve
from voznik.nastavitve import NastavitveVoznika
from zajem.vozilo_sim import SimVozilo

HITRO = Nastavitve(casovna_omejitev_s=0.3, cas_alternative_s=0.1, alternative=False)


@pytest.fixture()
def okolje():
    nv = NastavitveVoznika(skrivnost=b"k" * 32, aktivno_fiks_s=0.05, aktivno_posiljanje_s=0.1, prosto_fiks_s=0.05,
                           prosto_posiljanje_s=0.1, omejitev_polozaj_min=100000,
                           max_hitrost_kmh={"vozilo": 1e6, "motorist": 1e6, "helikopter": 1e6})
    c = Center(nastavitve_planerja=HITRO, nastavitve_voznika=nv)
    app = ustvari_app(c, ozadje=False)
    with TestClient(app) as cl:
        yield cl, c, httpx.ASGITransport(app=app)


def vozilo(tr, eid, **kw):
    # zelo hitro vozilo za test (meja skokov je v tem testu izklopljena)
    return SimVozilo(eid, "DEMO-" + eid.replace("-", ""), "http://test", transport=tr, hitrost_kmh=1e5, max_kmh=1e6,
                     pospesek=1, postanek_s=0.1, zamik_potrditve_s=0.0, korak_s=0.03, **kw)


def test_celoten_krog_brez_naprave(okolje):
    cl, c, tr = okolje
    eid = "RV-2"
    koraki = [(k["naloga_id"], k["akcija"]) for k in c.plan_aktiven()["koraki_po_enoti"][eid]]
    v = vozilo(tr, eid)
    cl.portal.call(v.tek, 30, True)
    assert any("vse naloge opravljene" in x for x in v.dnevnik), v.dnevnik
    assert c.db.beri("enota", eid)["stanje"] == "prosto"
    assert all(c.db.beri("naloga", n)["stanje"] == "opravljena" for n, _ in koraki)
    faze = [r["nova_vrednost"]["faza"] for n in {n for n, _ in koraki} for r in c.db.revizije(n) if r["akcija"] == "stanje_voznika"]
    assert faze.count("na_prizoriscu") == len(koraki) and "pacient_nalozen" in faze and "dostavljeno" in faze
    assert c.naloge_voznikov.zadnje_obvestilo(eid)["stanje"] == "potrjeno"
    # heartbeat: simulator ga pošlje ob začetku in nato vsakih 30 s; centrala ga ima in shrani v JSON
    assert v.heartbeat.poslanih >= 1 and c.heartbeati.zadnji[eid]["lokacija"]["lat"]
    assert c.heartbeati.zgodovina(eid)
    # položaji: zgodovina urejena, enota stoji na cilju zadnje dostave
    zg = c.polozaji.zgodovina(eid)
    assert len(zg) >= 10 and [f["seq"] for f in zg] == sorted(f["seq"] for f in zg)
    zadnja = c.db.beri("naloga", koraki[-1][0])["dostava"]
    e = next(x for x in c.enote() if x["id"] == eid)
    assert abs(e["lat"] - zadnja["lat"]) < 1e-3 and abs(e["lon"] - zadnja["lon"]) < 1e-3


def test_izpad_omrezja_med_voznjo(okolje):
    cl, c, tr = okolje
    eid = "RV-2"
    v = vozilo(tr, eid, izpad=(0.4, 0.6))
    v.hitrost_ms = 30.0  # realna hitrost: noben fiks ni zavrnjen
    cl.portal.call(v.tek, 2.5)
    # vsi fiksi, ustvarjeni tudi med izpadom, so prispeli – brez podvajanja
    zg = c.polozaji.zgodovina(eid)
    assert len(zg) == v.fiksov and len({f["seq"] for f in zg}) == v.fiksov
    ts = [f["ts"] for f in zg]
    razmiki = [b - a for a, b in zip(ts, ts[1:])]
    assert max(razmiki) < 0.5  # brez luknje: fiksi iz vrste imajo izvirne čase
    assert len(v.vrsta) == 0


def test_neodziven_voznik_eskalira(okolje):
    cl, c, tr = okolje
    v = vozilo(tr, "RV-2", potrdi=False)
    cl.portal.call(v.tek, 0.5)
    o = c.naloge_voznikov.zadnje_obvestilo("RV-2")
    assert o["stanje"] == "caka"
    # ob potrditvi plana RV-2 še ni imel naprave (brez lažnega alarma); po prijavi brez potrditve -> eskalacija po roku
    c.naloge_voznikov.preveri_eskalacije(o["rok"] + 1)
    assert {e["enota_id"]: e["razlog"] for e in c.naloge_voznikov.eskalacije()}.get("RV-2") == "ni_potrditve"
