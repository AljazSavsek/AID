"""Zadnji Whisperjev prepis posnetkov klica shrani kot vnaprej določeno besedilo za demo.

    python -m zajem.zamrzni_prepis            # iz zadnjega poslusalec/prepisi/klic_*.json
    python -m zajem.zamrzni_prepis --prazno   # predloga z vsemi posnetki za ročni vnos

Rezultat gre v demo/klici.json (polje »prepis« zvočnega pogovora: {posnetek: besedilo}), kjer so vsi demo
pogovori; besedilo lahko ročno popravite (npr. napake prepisa krajevnih imen) in datoteko shranite v git.
"""
from __future__ import annotations

import argparse
import json
import sys

from poslusalec import poslusalec as pp
from zajem import zvok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prazno", action="store_true", help="predloga s praznim besedilom za vsak posnetek")
    ap.add_argument("--prepisi", action="store_true", help="prepiši obstoječo prepis.json")
    a = ap.parse_args(argv)
    posnetki = pp.posnetki_klica()
    cilj = zvok._klici_json()
    klici = json.loads(cilj.read_text(encoding="utf-8")) if cilj.exists() else {"klici": []}
    pogovor = next((k for k in klici.get("klici", []) if k.get("posnetki")), None)
    if pogovor is None:
        print(f"V {cilj} ni zvočnega pogovora (polje »posnetki«).", file=sys.stderr)
        return 1
    if any(str(v).strip() for v in (pogovor.get("prepis") or {}).values()) and not a.prepisi:
        print(f"Zvočni pogovor v {cilj} že ima besedilo – za prepis dodajte --prepisi.", file=sys.stderr)
        return 1
    if a.prazno:
        besedila, vir = {p.name: "" for p in posnetki}, "prazna predloga"
    else:
        r = zvok._iz_shranjenega_prepisa(posnetki)
        if not r:
            print("Ni shranjenega prepisa za te posnetke (poslusalec/prepisi/klic_*.json z enakim številom in vrstnim redom "
                  "govorcev). Najprej z Whisperjem predvajajte zvočni klic v aplikaciji ali zaženite poslusalec/zazeni.sh, "
                  "ali uporabite --prazno.", file=sys.stderr)
            return 1
        besedila, vir = r
    pogovor["prepis"] = besedila
    pogovor["prepis_vir"] = vir
    cilj.write_text(json.dumps(klici, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Shranjeno: {cilj} ({len(besedila)} posnetkov, vir: {vir})")
    for k, v in besedila.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
