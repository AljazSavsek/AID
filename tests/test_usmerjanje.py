"""Usmerjevalni servis: rezerve, hedging, varovalka, smiselnost, helikopter, skritost ponudnikov."""
import asyncio
import json

import pytest

from conftest import brez_ponudnikov
from usmerjanje.ponudniki import NapakaPonudnika, Pot, Ponudnik, helikopter, zracna_razdalja_m
from usmerjanje.servis import UsmerjevalniServis

OD, DO = (46.0545, 14.5222), (46.0714, 14.4887)


class Lazen(Ponudnik):
    def __init__(self, ime, cas_s=300, zamik=0.0, napaka=False, kakovost="natančno"):
        self.ime, self.cas_s, self.zamik, self.napaka, self.kakovost = ime, cas_s, zamik, napaka, kakovost
        self.klici = 0

    async def izracunaj(self, od, do, tip):
        self.klici += 1
        await asyncio.sleep(self.zamik)
        if self.napaka:
            raise NapakaPonudnika("izpad")
        return Pot(self.cas_s, 3000, [list(od), list(do)], self.kakovost)


def servis(*p, **kw):
    return UsmerjevalniServis(ponudniki=list(p), **kw)


def run(k):
    return asyncio.run(k)


def test_primarni_odgovori():
    r = run(servis(Lazen("tomtom", 300), Lazen("ors", 320, kakovost="brez prometa")).izracunaj(OD, DO))
    assert r["cas_s"] == 300 and r["kakovost"] == "natančno"
    assert set(r) == {"cas_s", "razdalja_m", "geometrija", "kakovost", "navodila", "opozorila", "zamuda_prometa_s", "izogiba_oviram"}


@pytest.mark.parametrize("izklopljen", [0, 1, 2])
def test_izklop_vsakega_ponudnika_posebej(izklopljen):
    pon = [Lazen("tomtom", 300), Lazen("ors", 310, kakovost="brez prometa"), Lazen("osrm", 320, kakovost="brez prometa")]
    pon[izklopljen].napaka = True
    r = run(servis(*pon).izracunaj(OD, DO))
    assert r["cas_s"] > 0
    assert brez_ponudnikov(json.dumps(r, ensure_ascii=False)) == []


def test_vsi_izklopljeni_zracna_ocena():
    r = run(servis(Lazen("a", napaka=True), Lazen("b", napaka=True)).izracunaj(OD, DO))
    assert r["kakovost"] == "ocena" and r["cas_s"] > 0


def test_hedging_po_1_5_s():
    pocasen, hiter = Lazen("tomtom", 300, zamik=3.0), Lazen("ors", 330, zamik=0.05, kakovost="brez prometa")
    s = servis(pocasen, hiter, hedge_s=0.3, rok_s=4.0)
    t0 = asyncio.run(_cas(s))
    assert t0[1]["cas_s"] == 330 and t0[0] < 1.0
    assert hiter.klici == 1


async def _cas(s):
    import time
    t = time.perf_counter()
    r = await s.izracunaj(OD, DO)
    return time.perf_counter() - t, r


def test_skupni_rok_nato_zracna_ocena():
    s = servis(Lazen("a", zamik=5), Lazen("b", zamik=5), hedge_s=0.1, rok_s=0.4)
    t, r = asyncio.run(_cas(s))
    assert r["kakovost"] == "ocena" and t < 1.0


def test_varovalka_po_treh_napakah():
    ura = [1000.0]
    a, b = Lazen("a", napaka=True), Lazen("b", 400, kakovost="brez prometa")
    s = servis(a, b, ura=lambda: ura[0])
    for i in range(3):
        run(s.izracunaj((46 + i / 100, 14.5), DO))
    assert s.varovalke["a"].stanje in ("odprta", "pol-odprta") and a.klici == 3
    run(s.izracunaj((46.5, 14.5), DO))
    assert a.klici == 3  # izločen
    ura[0] += 61
    a.napaka = False
    run(s.izracunaj((46.6, 14.5), DO))
    assert a.klici == 4 and s.varovalke["a"].napake == 0  # poskusni klic uspel


def test_smiselnost_konzervativnejsi():
    a, b = Lazen("a", 300, zamik=0.05), Lazen("b", 600, zamik=0.08, kakovost="brez prometa")
    s = servis(a, b, hedge_s=0.0)
    r = run(s.izracunaj(OD, DO))
    assert r["cas_s"] == 600


def test_predpomnilnik():
    a = Lazen("a", 300)
    s = servis(a)
    run(s.izracunaj(OD, DO))
    run(s.izracunaj(OD, DO))
    assert a.klici == 1


def test_helikopter_ne_klice_cestnih_api():
    a = Lazen("a", 300)
    r = run(servis(a).izracunaj(OD, DO, "helikopter"))
    d = zracna_razdalja_m(OD, DO)
    assert a.klici == 0
    assert abs(r["cas_s"] - (d / (200 / 3.6) + 300)) < 2
    assert r == helikopter(OD, DO).javno()


def test_matrika():
    m = run(servis(Lazen("a", 100)).matrika([OD, DO], [DO, OD]))
    assert len(m) == 2 and len(m[0]) == 2 and m[0][0]["cas_s"] == 100


def test_zdravje_vsebuje_imena_samo_interno():
    s = servis(Lazen("tomtom"))
    assert "tomtom" in s.zdravje()


def test_omejitev_hitrosti_ne_izklopi_ponudnika():
    from usmerjanje.ponudniki import Omejitev

    class Omejen(Lazen):
        async def izracunaj(self, od, do, tip):
            self.klici += 1
            raise Omejitev("429")

    a = Omejen("tomtom")
    s = servis(a)
    for i in range(6):
        r = run(s.izracunaj((46 + i / 100, 14.5), DO))
        assert r["kakovost"] == "ocena"
    assert s.varovalke["tomtom"].stanje == "zaprta" and a.klici == 6


def test_omejevalnik_razmakne_zahteve():
    import time
    from usmerjanje.ponudniki import Omejevalnik
    om = Omejevalnik(hkrati=2, na_sekundo=10)

    async def ena():
        async with om:
            return time.perf_counter()

    async def vse():
        return await asyncio.gather(*(ena() for _ in range(5)))

    casi = sorted(run(vse()))
    assert casi[-1] - casi[0] >= 0.35  # 5 zahtev pri 10/s


def test_ni_poti_in_isti_kraj_ne_izklopita_ponudnika():
    from usmerjanje.ponudniki import NiPoti

    class Brez(Lazen):
        async def izracunaj(self, od, do, tip):
            self.klici += 1
            raise NiPoti("NO_ROUTE_FOUND")

    a = Brez("tomtom")
    s = servis(a)
    for i in range(5):
        run(s.izracunaj((45.8 + i / 100, 15.1), DO))
    assert s.varovalke["tomtom"].stanje == "zaprta"
    # enota je že na cilju: brez klica ponudnika, čas 0
    r = run(s.izracunaj(DO, DO))
    assert r["cas_s"] == 0 and a.klici == 5
