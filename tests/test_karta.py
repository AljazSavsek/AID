"""Posrednik ploščic: ključ ostane na strežniku, brez imena ponudnika, rezerva."""
import httpx
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from karta.ploscice import Ploscice
from planer.vrp import Nastavitve

PNG = b"\x89PNG\r\n\x1a\nlazna"


def odjemalec(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def app_z(ploscice=None):
    app = ustvari_app(Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False)), ozadje=False)
    return app


def test_brez_kljuca_ni_na_voljo():
    with TestClient(app_z()) as cl:
        assert cl.get("/ploscice/stanje").json()["karta"] is False
        assert cl.get("/ploscice/karta/12/2218/1453.png").status_code == 404


def test_s_kljucem_vrne_sliko_brez_ponudnika(monkeypatch):
    monkeypatch.setenv("TOMTOM_MAP_KEY", "skrivni-kljuc")
    klici = []

    def h(req):
        klici.append(str(req.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png", "x-provider": "TomTom"})

    with TestClient(app_z()) as cl:
        cl.app.state.ploscice._odjemalec = odjemalec(h)
        st = cl.get("/ploscice/stanje")
        assert st.json()["karta"] is True and brez_ponudnikov(st.text) == []
        r = cl.get("/ploscice/karta/12/2218/1453.png")
        assert r.status_code == 200 and r.content == PNG
        assert "x-provider" not in r.headers and "skrivni" not in str(r.headers)
        assert brez_ponudnikov(" ".join(f"{k}: {v}" for k, v in r.headers.items())) == []
        cl.get("/ploscice/karta/12/2218/1453.png")
        assert len(klici) == 1  # predpomnilnik
        assert "key=skrivni-kljuc" in klici[0]


def test_napaka_vrne_staro_ploscico(monkeypatch):
    import asyncio
    monkeypatch.setenv("TOMTOM_MAP_KEY", "k")
    stanje = {"ok": True}

    def h(req):
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"}) if stanje["ok"] else httpx.Response(503)

    p = Ploscice(odjemalec=odjemalec(h))
    assert asyncio.run(p.ploscica("promet", 10, 1, 1)) == PNG
    p._predpomnilnik[("promet", 10, 1, 1)] = (0, PNG)  # potekla
    stanje["ok"] = False
    assert asyncio.run(p.ploscica("promet", 10, 1, 1)) == PNG
