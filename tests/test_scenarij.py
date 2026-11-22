"""Demo scenarij: brez Motorista 1, po začetnem planu izmene so vsaj 3 enote proste (za nove klice)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ustvari_app
from api.storitev import Center
from planer.vrp import Nastavitve

SC = json.loads((Path(__file__).parent.parent / "demo" / "scenarij.json").read_text(encoding="utf-8"))


def test_ni_motorista_1_in_vsaka_enota_ima_kodo():
    ids = [e["id"] for e in SC["enote"]]
    assert "MOT-1" not in ids and "MOT-1" not in SC["kode_voznikov"]
    assert set(SC["kode_voznikov"]) == set(ids)


@pytest.mark.parametrize("omejitev_s", [0.3, 2.0])
def test_po_zacetnem_planu_vsaj_3_proste_enote(omejitev_s):
    c = Center(nastavitve_planerja=Nastavitve(casovna_omejitev_s=omejitev_s, alternative=False))
    with TestClient(ustvari_app(c, ozadje=False)) as cl:
        plan = c.plan_aktiven()
        assert plan and not plan["metrike"]["nepokrite"]
        proste = [e for e, ks in plan["koraki_po_enoti"].items() if not ks and e != "HELI-1"]
        assert len(proste) >= 3, plan["koraki_po_enoti"]
        assert "MOT-1" not in cl.get("/voznik/enote-za-prijavo").text


def test_stara_baza_z_mot1_se_sama_ponastavi(tmp_path):
    """Baza iz prejšnjega scenarija (z MOT-1) se ob zagonu z novim scenarijem sama ponastavi – brez ročnega brisanja."""
    from dnevnik.shramba import Shramba
    star = dict(SC, enote=SC["enote"] + [{"id": "MOT-1", "tip": "motorist", "ime": "Motorist 1", "lat": 46.0569, "lon": 14.5058,
                                          "stanje": "prosto", "kapaciteta": {"pacienti": 0, "oprema": 1}, "tovor": [], "na_voljo_od": 0}])
    (tmp_path / "star.json").write_text(json.dumps(star), encoding="utf-8")
    baza = tmp_path / "aid.sqlite"
    c1 = Center(Shramba(baza), scenarij=tmp_path / "star.json", nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    c1.db.shrani("klic", {"id": "k-star", "stanje": "zakljucen"})
    assert "MOT-1" in {e["id"] for e in c1.enote()}
    c2 = Center(Shramba(baza), nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    assert {e["id"] for e in c2.enote()} == {e["id"] for e in SC["enote"]} and "MOT-1" not in {e["id"] for e in c2.enote()}
    assert c2.db.beri("klic", "k-star") is None  # nova izmena
    assert any(r["akcija"] == "ponastavitev" for r in c2.db.revizije("demo"))  # revizijska sled ostane in to zabeleži
    c3 = Center(Shramba(baza), nastavitve_planerja=Nastavitve(casovna_omejitev_s=0.3, alternative=False))
    c3.db.shrani("klic", {"id": "k-nov", "stanje": "v_teku"})
    assert Center(Shramba(baza)).db.beri("klic", "k-nov")  # isti scenarij: stanje ostane
