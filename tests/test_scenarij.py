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
