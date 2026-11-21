"""Varnost: prijava dispečerja, podatki pacientov, kvota zunanjih ponudnikov, javni pogled."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov, prijavi
from planer.vrp import Nastavitve

BREZ = {"X-Brez-Prijave": "1"}


@pytest.fixture()
def app():
    return ustvari_app(Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False)), ozadje=False)


@pytest.fixture()
def anon(app):
    with TestClient(app, headers=BREZ) as k:
        yield k


@pytest.mark.parametrize("metoda,pot", [
    ("get", "/naloge"), ("get", "/enote"), ("get", "/stanje"), ("get", "/revizija"), ("get", "/plan/aktiven"),
    ("get", "/cakajoci"), ("get", "/ekipe/predlogi"), ("get", "/ekipe/sporocila"), ("get", "/eta?lat=46.05&lon=14.5"),
    ("post", "/klic/zacni"), ("post", "/dogodek"), ("post", "/plan/x/potrdi"), ("post", "/demo/izpad"),
    ("post", "/poti"), ("get", "/pot?enota_id=RV-1&lat=46.05&lon=14.5"), ("get", "/ploscice/stanje"),
    ("get", "/ploscice/karta/12/2218/1453.png"), ("get", "/interno/zdravje"),
])
def test_brez_prijave_zavrnjeno(anon, metoda, pot):
    r = getattr(anon, metoda)(pot, **({"json": {}} if metoda == "post" else {}))
    assert r.status_code in (401, 403), (pot, r.status_code)


def test_ws_dispecerja_brez_prijave_zaprt(anon):
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_javne_strani_in_prijava_voznika_delujejo(anon):
    for pot in ("/", "/voznik.html", "/javnost.html", "/js/povezava.js", "/img/aid-logo.svg", "/voznik/enote-za-prijavo"):
        assert anon.get(pot).status_code == 200, pot
    assert set(anon.get("/voznik/enote-za-prijavo").json()[0]) == {"id", "ime"}  # brez položajev in nalog
    r = anon.post("/voznik/prijava", json={"enota_id": "RV-1", "koda": "DEMO-RV1", "naprava_id": "t1"})
    assert r.status_code == 200


def test_prijava_napacno_geslo_in_zaklep(anon):
    for _ in range(5):
        assert anon.post("/dispecer/prijava", json={"ime": "M. Novak", "geslo": "narobe"}).status_code == 401
    assert anon.post("/dispecer/prijava", json={"ime": "M. Novak", "geslo": "test-geslo"}).status_code == 429
    assert anon.post("/dispecer/prijava", json={"ime": "Ne Obstaja", "geslo": "x"}).status_code == 401


def test_piskotek_httponly_in_odjava_preklice_sejo(anon):
    r = prijavi(anon)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc
    zeton = anon.cookies.get("aid_dispecer")
    assert anon.get("/naloge").status_code == 200
    anon.post("/dispecer/odjava")
    anon.cookies.set("aid_dispecer", zeton)  # ukraden/star piškotek po odjavi ne velja več
    assert anon.get("/naloge").status_code == 401


def test_ponarejen_piskotek(anon):
    anon.cookies.set("aid_dispecer", "eyJ1IjoiQWRtaW4iLCJzaWQiOiJ4IiwiZXhwIjo5OTk5OTk5OTk5fQ.ponarejen")
    assert anon.get("/naloge").status_code == 401


def test_revizija_pod_imenom_iz_seje(app):
    with TestClient(app) as cl:
        kid = cl.post("/klic/zacni", json={}, headers={"X-Uporabnik": "Ponarejen"}).json()["id"]
        assert cl.get("/revizija", params={"objekt_id": kid}).json()[0]["uporabnik"] == "M. Novak"
        assert any(r["akcija"] == "prijava" and r["uporabnik"] == "M. Novak" for r in cl.get("/revizija").json())


def test_ploscice_z_zetonom_voznika_in_meje_obmocja(anon, monkeypatch):
    monkeypatch.delenv("TOMTOM_MAP_KEY", raising=False)
    z = anon.post("/voznik/prijava", json={"enota_id": "RV-2", "koda": "DEMO-RV2", "naprava_id": "t2"}).json()["dostop"]
    h = {"Authorization": "Bearer " + z}
    assert anon.get("/ploscice/stanje", headers=h).status_code == 200
    assert anon.get("/ploscice/karta/12/2218/1453.png", headers=h).status_code == 404  # brez ključa: ni ploščice (a ni 401)
    prijavi(anon)
    assert anon.get("/ploscice/karta/12/0/0.png").status_code == 404     # izven Slovenije
    assert anon.get("/ploscice/karta/3/4/2.png").status_code == 404      # premajhna povečava
    assert anon.get("/ploscice/karta/22/1/1.png").status_code == 404


def test_poti_izven_obmocja_in_omejitev(app):
    with TestClient(app) as cl:
        r = cl.post("/poti", json={"odseki": [{"od": [48.2, 16.37], "do": [46.05, 14.5], "tip": "vozilo"}]})
        assert r.status_code == 422
        kode = [cl.post("/poti", json={"odseki": [{"od": [46.05, 14.5], "do": [46.06, 14.51]}]}).status_code for _ in range(35)]
        assert 429 in kode and kode[0] == 200


def test_dokumentacija_api_izklopljena(anon):
    assert anon.get("/interno/docs").status_code in (401, 404)
    assert anon.get("/interno/openapi.json").status_code in (401, 404)


def test_skrbnik_z_zetonom(app, monkeypatch):
    monkeypatch.setenv("AID_SKRBNIK_ZETON", "s3")
    with TestClient(app, headers=BREZ) as anon:
        assert anon.get("/interno/zdravje", headers={"X-Skrbnik-Zeton": "s3"}).status_code == 200
        assert anon.post("/enote/RV-1/koda", headers={"X-Skrbnik-Zeton": "s3"}).status_code == 200


def test_javnost_le_nujne_voznje_brez_podatkov(app):
    """Opozorilo za javnost: le vozilo na nujni vožnji (faza na_poti), brez id enote in podatkov o nalogi."""
    with TestClient(app) as cl:
        plan = cl.get("/plan/aktiven").json()
        eid, koraki = next((e, ks) for e, ks in plan["koraki_po_enoti"].items() if ks and e.startswith("RV"))
        h = {"Authorization": "Bearer " + cl.post("/voznik/prijava", json={"enota_id": eid, "koda": "DEMO-" + eid.replace("-", ""),
                                                                            "naprava_id": "j1"}).json()["dostop"]}
        n = cl.get("/voznik/naloga", headers=h).json()
        lat, lon = n["enota"]["lat"], n["enota"]["lon"]
        cl.post("/voznik/polozaj", headers=h, json={"fiksi": [{"seq": 1, "ts": time.time(), "lat": lat, "lon": lon, "smer": 90, "natancnost": 5}]})
        anon_r = cl.get("/javnost/blizina", params={"lat": lat + 0.003, "lon": lon})
        prej = anon_r.json()["vozila"]
        nujna = koraki[0].get("nujna") or (koraki[0].get("prioriteta") or 5) <= 2
        for s in ("prejeto", "na_poti"):
            cl.post("/voznik/stanje", headers=h, json={"stanje": s, "naloga_id": koraki[0]["naloga_id"], "akcija": koraki[0]["akcija"]})
        r = cl.get("/javnost/blizina", params={"lat": lat + 0.003, "lon": lon}, headers=BREZ)
        assert r.status_code == 200
        v = r.json()["vozila"]
        assert prej == []  # pred »na poti« ni opozorila
        if nujna:
            assert len(v) == 1 and set(v[0]) == {"lat", "lon", "smer", "razdalja_m", "priblizuje_se"}
            assert eid not in r.text and koraki[0]["naloga_id"] not in r.text
        else:
            assert v == []  # vožnja nizke prioritete ne sproži javnega opozorila
        assert brez_ponudnikov(r.text) == []
        assert cl.get("/javnost/blizina", params={"lat": 48.2, "lon": 16.37}).status_code == 422


def test_ai_rezerva_je_vidna():
    from poslusalec.poslusalec import Poslusalec, Pravila

    class Pokvarjen:
        ime = "oblak"

        def na_voljo(self):
            return True

        async def poklici(self, vhod):
            raise TypeError("unexpected keyword argument 'fallbacks'")  # npr. prestara različica SDK

    p = Poslusalec([Pokvarjen(), Pravila()])
    seg = [{"id": "s1", "govorec": "klicatelj", "od_s": 0, "do_s": 1, "besedilo": "Moj oče je padel z lestve."}]
    izhod = asyncio.run(p.obdelaj(seg, [], [], []))
    assert izhod["posodobitve"] and p.rezerva is True and "fallbacks" in p.napake["oblak"]
