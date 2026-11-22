"""Jedro aplikacije voznika proti pravemu API (v procesu): trajna vrsta, ponovno pošiljanje po
izpadu povezave, osvežitev žetona, odjava z druge naprave, sinhronizacija naloge, navigacija."""
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aplikacija_voznik"))

from api.app import ustvari_app  # noqa: E402
from api.storitev import Center  # noqa: E402
from jedro.gps import SimuliranVir, fiks  # noqa: E402
from jedro.navigacija import razdalja_m, stanje_navigacije  # noqa: E402
from jedro.odjemalec import NiPovezave, Odjavljen, Odjemalec, Seja  # noqa: E402
from jedro.posiljalec import Posiljalec  # noqa: E402
from jedro.sinhronizacija import Sinhronizacija  # noqa: E402
from jedro.vrsta import VrstaFiksov  # noqa: E402
from planer.vrp import Nastavitve  # noqa: E402
from voznik.nastavitve import NastavitveVoznika  # noqa: E402


class Prekinljiv(httpx.AsyncBaseTransport):
    """ASGI transport, ki ga lahko »odklopimo« (simulacija izpada omrežja)."""

    def __init__(self, app):
        self.notranji = httpx.ASGITransport(app=app)
        self.odklopljen = False

    async def handle_async_request(self, request):
        if self.odklopljen:
            raise httpx.ConnectError("ni omrežja", request=request)
        return await self.notranji.handle_async_request(request)


@pytest.fixture()
def okolje(tmp_path):
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False),
               nastavitve_voznika=NastavitveVoznika(skrivnost=b"k" * 32))
    app = ustvari_app(c, ozadje=False)
    with TestClient(app) as cl:
        tr = Prekinljiv(app)
        seja = Seja(tmp_path / "seja.json")
        o = Odjemalec(seja, "http://test", transport=tr)
        yield cl, c, o, tr, tmp_path


def pozeni(cl, fn, *a):
    return cl.portal.call(fn, *a)


def eid_s_koraki(c):
    return next(e for e, ks in c.plan_aktiven()["koraki_po_enoti"].items() if len(ks) >= 2 and e.startswith("RV"))


def test_vrsta_trajna_seq_in_omejitev(tmp_path):
    v = VrstaFiksov(tmp_path / "v.sqlite", najvec=5)
    nid = v.naprava_id
    for i in range(3):
        v.dodaj(fiks(46, 14, time.time() + i))
    v2 = VrstaFiksov(tmp_path / "v.sqlite", najvec=5)  # ponovni zagon aplikacije
    assert v2.naprava_id == nid and len(v2) == 3 and v2.dodaj(fiks(46, 14)) == 4
    for _ in range(4):
        v2.dodaj(fiks(46, 14))
    assert len(v2) == 5 and v2.paket(1)[0]["seq"] == 4 and v2.zavrzeni == 3  # najstarejši zavrženi
    v2.potrdi_do(6)
    assert [f["seq"] for f in v2.paket()] == [7, 8]


def test_izpad_povezave_in_ponovno_posiljanje(okolje):
    cl, c, o, tr, tmp = okolje
    eid = eid_s_koraki(c)
    vrsta = VrstaFiksov(tmp / "v.sqlite")
    pozeni(cl, o.prijava, eid, "DEMO-" + eid.replace("-", ""), vrsta.naprava_id)
    pos = Posiljalec(o, vrsta, velikost_paketa=25)
    t0 = time.time() - 120
    tr.odklopljen = True
    for i in range(60):
        pos.nov_fiks(fiks(46.05 + i * 1e-4, 14.5, t0 + i * 2, 12.0, 0.0, 6.0))
    with pytest.raises(NiPovezave):
        pozeni(cl, pos.poslji_vse)
    assert len(vrsta) == 60  # nič izgubljenega
    tr.odklopljen = False
    assert pozeni(cl, pos.poslji_vse) is True and len(vrsta) == 0
    zg = c.polozaji.zgodovina(eid)
    assert [f["seq"] for f in zg] == list(range(1, 61))
    assert c.polozaji.trenutni(eid)["lat"] == pytest.approx(46.05 + 59e-4)
    assert pos.gps["nacin"] in ("aktivno", "prosto")  # frekvence od strežnika


def test_neveljaven_paket_ne_zamasi_vrste(okolje):
    cl, c, o, tr, tmp = okolje
    vrsta = VrstaFiksov(tmp / "v.sqlite")
    pozeni(cl, o.prijava, "RV-3", "DEMO-RV3", vrsta.naprava_id)
    pos = Posiljalec(o, vrsta, velikost_paketa=1)
    vrsta.dodaj({"ts": time.time(), "lat": 200.0, "lon": 14.5})  # pokvarjen fiks
    pos.nov_fiks(fiks(46.07, 14.5))
    assert pozeni(cl, pos.poslji_vse) is True and len(vrsta) == 0
    assert c.polozaji.trenutni("RV-3")["lat"] == 46.07


def test_osvezitev_zetona_in_odjava_z_druge_naprave(okolje):
    cl, c, o, tr, tmp = okolje
    pozeni(cl, o.prijava, "RV-3", "DEMO-RV3", "tel-a")
    star = o.seja.podatki["osvezitev"]
    o.seja.podatki["dostop_potece"] = time.time() - 1  # potekel -> samodejna osvežitev
    pozeni(cl, o.naloga)
    assert o.seja.podatki["osvezitev"] != star
    # druga naprava se prijavi -> ta je odjavljena, seja počiščena (koda se nikoli ne shrani)
    cl.post("/voznik/prijava", json={"enota_id": "RV-3", "koda": "DEMO-RV3", "naprava_id": "tel-b"})
    with pytest.raises(Odjavljen):
        pozeni(cl, o.naloga)
    assert not o.seja.prijavljen and "DEMO" not in (tmp / "seja.json").read_text()


def test_sinhronizacija_polling_in_cikel(okolje):
    cl, c, o, tr, tmp = okolje
    eid = eid_s_koraki(c)
    pozeni(cl, o.prijava, eid, "DEMO-" + eid.replace("-", ""), "tel")
    prejete = []
    s = Sinhronizacija(o, lambda n: prejete.append(n))
    n = pozeni(cl, s.osvezi_nalogo)
    assert n["obvestila"] and s.zagon == c.naloge_voznikov.objava.zagon
    pozeni(cl, o.potrdi, n["obvestila"][0]["id"])
    pozeni(cl, s._polling_korak)  # dogodek »naloga« -> nova naloga
    assert prejete[-1]["obvestila"] == [] and prejete[-1]["faza"] == "prejeto"
    k = next(x for x in prejete[-1]["koraki"] if x["trenutni"])
    r = pozeni(cl, o.stanje, "na_poti", k)
    assert r["faza"] == "na_poti" and r["dovoljena_stanja"] == ["na_prizoriscu"]


def test_navigacija_po_poti():
    pot = {"geometrija": [[46.0, 14.0], [46.0, 14.01], [46.01, 14.01]], "cas_s": 300, "razdalja_m": 1880, "kakovost": "ocena"}
    s = stanje_navigacije((46.0, 14.005), pot)
    assert s["preostalo_m"] == pytest.approx(razdalja_m((46.0, 14.005), (46.0, 14.01)) + razdalja_m((46.0, 14.01), (46.01, 14.01)), rel=0.02)
    assert s["smer"] == "V" and not s["izven_poti"] and s["eta_s"] > 0
    assert stanje_navigacije((46.005, 14.003), pot)["izven_poti"]
    heli = stanje_navigacije((46.0, 14.0), {"geometrija": [[46.0, 14.0], [46.1, 14.0]], "cestna": False, "cas_s": 600, "razdalja_m": 11000})
    assert heli["smer"] == "S" and heli["preostalo_m"] == pytest.approx(11119, rel=0.01)


def test_simuliran_vir_vozi_po_poti():
    v = SimuliranVir((46.0, 14.0), hitrost_ms=100)
    v.nastavi_pot([[46.0, 14.01], [46.01, 14.01]])
    f = v.korak(5)
    assert f["lon"] > 14.0 and f["smer"] == pytest.approx(90, abs=1) and f["hitrost"] == 100
    for _ in range(10):
        f = v.korak(5)
    assert (f["lat"], f["lon"]) == (46.01, 14.01) and f["hitrost"] == 0.0


def test_analiza_vrzeli():
    from analiza_vrzeli import analiziraj
    t = 1000.0
    fiksi = [{"ts": t + i * 2} for i in range(100)] + [{"ts": t + 400 + i * 2} for i in range(100)]
    r = analiziraj(fiksi, 15)
    assert r["fiksi"] == 200 and r["mediana_s"] == 2 and len(r["vrzeli"]) == 1 and r["vrzeli"][0][1] == pytest.approx(202)
    assert 0.6 < r["pokritost"] < 0.7


def test_odjemalec_sporocila_in_predlogi(okolje):
    cl, c, o, tr, tmp = okolje
    pozeni(cl, o.prijava, "RV-3", "DEMO-RV3", "tel")
    assert pozeni(cl, o.sporocilo, "zamuda")["besedilo"] == "Zamuda"
    assert pozeni(cl, o.sporocilo, None, "Gneča")["besedilo"] == "Gneča"
    d = pozeni(cl, o.ovira, "zapora", "drevo")
    assert d["stanje"] == "predlagano" and d["parametri"]["porocal"] == "RV-3"
    m = c.ekipa.od_dispecerja("RV-3", "M. Novak", "pocakaj", None)
    n = pozeni(cl, o.naloga)
    assert n["sporocila"][0]["id"] == m["id"] and n["predlogi"][-1]["tip"] == "ovira_na_cesti"
    pozeni(cl, o.prebrano, m["id"])
    assert c.ekipa.sporocila("RV-3")[0]["prebrano"]


def test_paket_heartbeata():
    from jedro.heartbeat import paket
    n = {"enota": {"id": "RV-1", "lat": 46.05, "lon": 14.5},
         "koraki": [{"naloga_id": "N-1", "akcija": "prevzem", "lat": 46.06, "lon": 14.5, "trenutni": True}],
         "pot": {"geometrija": [[46.05, 14.5], [46.055, 14.5], [46.06, 14.5]], "cas_s": 120, "razdalja_m": 1113, "izracunano": 5.0}}
    p = paket({"ts": 100.0, "lat": 46.052, "lon": 14.5, "hitrost": 10.0, "smer": 0.0, "natancnost": 6}, n, ura=110.0)
    assert p["hitrost_kmh"] == 36.0 and p["lokacija"] == {"lat": 46.052, "lon": 14.5, "natancnost": 6, "smer": 0.0}
    assert p["pot"]["naloga_id"] == "N-1" and p["pot"]["izracunano"] == 5.0 and 850 < p["pot"]["preostalo_m"] < 950
    assert paket({"ts": 0.0, "lat": 46.05, "lon": 14.5, "hitrost": 10.0}, n, ura=1000.0)["hitrost_kmh"] == 0.0  # star fiks: stoji
    assert paket(None, n, ura=1.0)["lokacija"] == {"lat": 46.05, "lon": 14.5}  # brez GPS: zadnji znani položaj
    assert paket(None, None) is None
