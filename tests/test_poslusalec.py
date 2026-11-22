"""AI poslušalec: neveljaven JSON, rezerva, dokazi, nič izmišljenega."""
import asyncio
import json
from pathlib import Path

from poslusalec.ai import Poslusalec, Pravila
from poslusalec.shema import NeveljavenIzhod, validiraj

SEGMENTI = [dict(s, id=f"s{i + 1}") for i, s in enumerate(
    json.loads((Path(__file__).parent / "podatki" / "klic1.json").read_text(encoding="utf-8"))["segmenti"])]


class LazenLLM:
    def __init__(self, ime, odgovori):
        self.ime, self.odgovori, self.klici = ime, list(odgovori), 0

    def na_voljo(self):
        return True

    async def poklici(self, vhod):
        self.klici += 1
        o = self.odgovori.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


VELJAVEN = json.dumps({"posodobitve": [{"polje": "naslov", "vrednost": "Celovška cesta 150, Ljubljana", "zaupanje": 0.8,
                                        "dokazi": [{"segment_id": "s4", "citat": "Celovška cesta, mislim da 150"}]}],
                       "vprasanja_za_klicatelja": ["Kateri vhod?"], "zaznani_dogodki": []})


def obdelaj(p, n=4):
    return asyncio.run(p.obdelaj(SEGMENTI[:n], [], [], []))


def test_neveljaven_json_ponovi_enkrat():
    llm = LazenLLM("oblak", ["{ni json", VELJAVEN])
    izhod = obdelaj(Poslusalec([llm]))
    assert llm.klici == 2 and izhod["posodobitve"][0]["polje"] == "naslov"


def test_dvakrat_neveljaven_preklop_na_rezervo():
    prvi = LazenLLM("oblak", ["{x", "[]"])
    drugi = LazenLLM("lokalni", [VELJAVEN])
    p = Poslusalec([prvi, drugi])
    izhod = obdelaj(p)
    assert prvi.klici == 2 and drugi.klici == 1 and izhod is not None
    assert p.zadnji_vir == "lokalni"


def test_izpad_vseh_brez_predlogov():
    p = Poslusalec([LazenLLM("oblak", [TimeoutError(), TimeoutError()])])
    assert obdelaj(p) is None


def test_pravila_kot_zadnja_rezerva():
    p = Poslusalec([LazenLLM("oblak", [ConnectionError("ni povezave")]), Pravila()])
    izhod = obdelaj(p, 8)
    polja = {u["polje"]: u["vrednost"] for u in izhod["posodobitve"]}
    assert polja["naslov"] == "Celovška cesta 105, Ljubljana"
    assert polja["vrsta_dogodka"] == "padec"


def test_izmisljen_citat_se_zavrze():
    izhod = validiraj({"posodobitve": [{"polje": "naslov", "vrednost": "Dunajska 5", "zaupanje": 0.9,
                                        "dokazi": [{"segment_id": "s4", "citat": "Dunajska 5"}]}],
                       "vprasanja_za_klicatelja": [], "zaznani_dogodki": []}, SEGMENTI)
    assert izhod["posodobitve"] == []


def test_brez_dokaza_ni_predloga():
    izhod = validiraj({"posodobitve": [{"polje": "prioriteta", "vrednost": 1, "zaupanje": 0.9, "dokazi": []}],
                       "vprasanja_za_klicatelja": [], "zaznani_dogodki": []}, SEGMENTI)
    assert izhod["posodobitve"] == []


def test_napacna_struktura():
    import pytest
    with pytest.raises(NeveljavenIzhod):
        validiraj({"posodobitve": "x"}, SEGMENTI)


def test_pravila_ne_ugibajo_manjkajocega():
    izhod = obdelaj(Poslusalec([Pravila()]), 2)
    polja = {u["polje"] for u in izhod["posodobitve"]}
    assert "naslov" not in polja and "kontakt" not in polja
    assert any("Kje" in q for q in izhod["vprasanja_za_klicatelja"])


def test_dogodek_okvare_brez_enote_vprasa():
    seg = [{"id": "x1", "govorec": "klicatelj", "od_s": 0, "do_s": 2, "besedilo": "Vozilo je obstalo. Oprema je še v njem."}]
    izhod = asyncio.run(Poslusalec([Pravila()]).obdelaj(seg, [], [], []))
    d = izhod["zaznani_dogodki"][0]
    assert d["tip"] == "okvara_vozila" and d["parametri"]["tovor_ostane"] is True and "enota_id" not in d["parametri"]
    assert "Katero vozilo?" in izhod["vprasanja_za_klicatelja"]


def test_naslov_v_mestniku_iz_prepisa_govora():
    import asyncio
    from poslusalec.ai import Poslusalec, Pravila
    seg = [{"id": "s1", "govorec": "klicatelj", "od_s": 0, "do_s": 3,
            "besedilo": "Halo, na Tržaški cesti 85 v Ljubljani je ženska nezavestna, ne diha."}]
    izhod = asyncio.run(Poslusalec([Pravila()]).obdelaj(seg, [], [], []))
    polja = {p["polje"]: p for p in izhod["posodobitve"]}
    assert polja["naslov"]["vrednost"] == "Tržaška cesta 85, Ljubljana"
    assert polja["naslov"]["dokazi"][0]["citat"] in seg[0]["besedilo"]
