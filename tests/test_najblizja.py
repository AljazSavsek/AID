"""Najbližja enota po prometu in pot po cestah."""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from conftest import brez_ponudnikov
from planer.vrp import Nastavitve
from usmerjanje.ponudniki import Pot, Ponudnik, zracna_ocena
from usmerjanje.servis import UsmerjevalniServis

CILJ = (46.07138, 14.4887)  # Celovška cesta 105


class PrometniPonudnik(Ponudnik):
    """Lažen ponudnik s prometom: RV-2 (UKC) stoji v zastoju, RV-3 je zračno dlje, a hitrejši."""
    ime = "tomtom"
    kakovost = "natančno"
    zastoj = {(46.0545, 14.5222): 3.0}  # faktor zamude za izhodišče RV-2

    async def izracunaj(self, od, do, tip):
        osnova = zracna_ocena(od, do, tip)
        f = self.zastoj.get((round(od[0], 4), round(od[1], 4)), 1.0)
        sredina = [(od[0] + do[0]) / 2 + 0.002, (od[1] + do[1]) / 2]
        return Pot(osnova.cas_s * f, osnova.razdalja_m, [list(od), sredina, list(do)], self.kakovost)


@pytest.fixture()
def cl():
    c = Center(usmerjanje=UsmerjevalniServis(ponudniki=[PrometniPonudnik()]),
               nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False)) as k:
        k.center = c
        # vse cestne enote proste, da odloča samo promet
        for e in c.db.vsi("enota"):
            e["stanje"] = "prosto"
            c.db.shrani("enota", e)
        yield k


def test_najblizja_po_prometu_ne_po_razdalji(cl):
    eta = cl.get("/eta", params={"lat": CILJ[0], "lon": CILJ[1], "n": 7}).json()
    rv2 = next(x for x in eta if x["enota_id"] == "RV-2")
    assert eta[0]["enota_id"] != "RV-2" and eta[0]["cas_s"] < rv2["cas_s"]
    assert all(x["kakovost"] in ("natančno", "ocena") for x in eta)


def test_pot_enote_vrne_geometrijo_brez_ponudnika(cl):
    r = cl.get("/pot", params={"enota_id": "RV-3", "lat": CILJ[0], "lon": CILJ[1]})
    assert r.status_code == 200
    d = r.json()
    assert len(d["geometrija"]) == 3 and d["od"] == [46.084, 14.5113] and d["kakovost"] == "natančno"
    assert brez_ponudnikov(r.text) == []
    assert cl.get("/pot", params={"enota_id": "XX", "lat": 46, "lon": 14}).status_code == 404


def test_poti_za_plan(cl):
    r = cl.post("/poti", json={"odseki": [{"od": [46.0545, 14.5222], "do": list(CILJ), "tip": "vozilo"},
                                          {"od": [46.2237, 14.4576], "do": list(CILJ), "tip": "helikopter"}]})
    assert r.status_code == 200 and len(r.json()) == 2
    assert len(r.json()[0]["geometrija"]) == 3 and r.json()[1]["kakovost"] == "ocena"
    assert brez_ponudnikov(r.text) == []


def test_osvezi_najblizjo_ostane_predlog(cl):
    kid = cl.post("/klic/zacni", json={}).json()["id"]
    p = cl.post(f"/klic/{kid}/rocno", json={"polje": "lokacija", "vrednost": {"lat": CILJ[0], "lon": CILJ[1]}}).json()
    assert p["stanje"] == "spremenjeno"
    en = [x for x in cl.get(f"/klic/{kid}/predlogi").json() if x["polje"] == "predlagana_enota"]
    assert len(en) == 1 and en[0]["stanje"] == "predlagano" and en[0]["vrednost"] != "RV-2"
    assert en[0]["dodatno"]["enote"][0]["enota_id"] == en[0]["vrednost"]
    # dispečer potrdi; nato promet izgine pri RV-2 -> nova najhitrejša je le PREDLOG, potrjena ostane
    cl.post(f"/predlog/{en[0]['id']}/sprejmi")
    PrometniPonudnik.zastoj = {(46.0545, 14.5222): 0.2}
    cl.center.usmerjanje._predpomnilnik.clear()
    predlogi = cl.post(f"/klic/{kid}/najblizja").json()
    PrometniPonudnik.zastoj = {(46.0545, 14.5222): 3.0}
    en = [x for x in predlogi if x["polje"] == "predlagana_enota"]
    potrjen = [x for x in en if x["stanje"] == "sprejeto"]
    nov = [x for x in en if x["stanje"] == "predlagano"]
    assert potrjen and potrjen[0]["vrednost"] != "RV-2"
    assert nov and nov[0]["vrednost"] == "RV-2" and nov[0]["nasprotuje_potrjenemu"]
    rev = cl.get("/revizija", params={"objekt_id": kid}).json()
    assert any(r["akcija"] == "osvezi_najblizjo" for r in rev)
