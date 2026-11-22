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


def test_omejitev_ponudnika_ne_izklopi_karte(monkeypatch):
    """Preveč zahtevkov (429 pri ponudniku): karta se ne izklopi, stara ploščica ostane, nova -> 503 + Retry-After."""
    monkeypatch.setenv("TOMTOM_MAP_KEY", "k")
    stanje = {"koda": 200}

    def h(req):
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"}) if stanje["koda"] == 200 \
            else httpx.Response(stanje["koda"])

    with TestClient(app_z()) as cl:
        p = cl.app.state.ploscice
        p._odjemalec, p._razmik = odjemalec(h), 0.0
        assert cl.get("/ploscice/karta/12/2218/1453.png").status_code == 200
        p._predpomnilnik[("karta", 12, 2218, 1453)] = (0, PNG)  # potekla
        stanje["koda"] = 429
        for _ in range(20):
            assert cl.get("/ploscice/karta/12/2218/1453.png").content == PNG  # stara ploščica, karta ne izgine
        r = cl.get("/ploscice/karta/12/2219/1453.png")
        assert r.status_code == 503 and r.headers["retry-after"] == "2"
        assert p.na_voljo("karta") and cl.get("/ploscice/stanje").json()["karta"] is True  # sloj NI izklopljen
        stanje["koda"] = 200
        p._omejen_do = 0
        assert cl.get("/ploscice/karta/12/2219/1453.png").status_code == 200


def test_ploscice_iz_predpomnilnika_ne_stejejo_v_omejitev(monkeypatch):
    monkeypatch.setenv("TOMTOM_MAP_KEY", "k")
    klici = []

    def h(req):
        klici.append(1)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    with TestClient(app_z()) as cl:
        p = cl.app.state.ploscice
        p._odjemalec, p._razmik = odjemalec(h), 0.0
        kode = [cl.get("/ploscice/karta/12/2218/1453.png").status_code for _ in range(3000)]  # nad mejo 2400/min
        assert set(kode) == {200} and len(klici) == 1
        assert cl.get("/ploscice/stanje").json()["stevec"]["predpomnilnik"] >= 2999


def test_socasni_zahtevki_isto_ploscico_prenesejo_enkrat(monkeypatch):
    import asyncio
    monkeypatch.setenv("TOMTOM_MAP_KEY", "k")
    klici = []

    async def h(req):
        klici.append(1)
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    async def tek():
        p = Ploscice(odjemalec=odjemalec(h), qps=1000)
        rez = await asyncio.gather(*(p.ploscica("karta", 12, 2218, 1453) for _ in range(10)))
        return rez
    assert asyncio.run(tek()) == [PNG] * 10 and len(klici) == 1


def test_napake_streznika_sloj_izklopijo_sele_po_vec_zaporednih(monkeypatch):
    import asyncio
    monkeypatch.setenv("TOMTOM_MAP_KEY", "k")
    p = Ploscice(odjemalec=odjemalec(lambda req: httpx.Response(502)), qps=1000)

    async def tek():
        for i in range(7):
            try:
                await p.ploscica("karta", 12, 2218, 1453 + i)
            except Exception:  # noqa: BLE001
                pass
    asyncio.run(tek())
    assert p.na_voljo("karta")  # 7 napak: še na voljo
    try:
        asyncio.run(p.ploscica("karta", 12, 2218, 1))
    except Exception:  # noqa: BLE001
        pass
    assert not p.na_voljo("karta") and p.na_voljo("promet")  # 8. zaporedna: izklop le tega sloja (30 s)
