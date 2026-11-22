"""Zadnji Whisperjev prepis posnetkov klica shrani kot vnaprej določeno besedilo za demo.

    python -m zajem.zamrzni_prepis            # iz zadnjega poslusalec/prepisi/klic_*.json
    python -m zajem.zamrzni_prepis --prazno   # predloga z vsemi posnetki za ročni vnos

Rezultat je poslusalec/zvok/prepis.json ({posnetek: besedilo}); besedilo lahko ročno popravite
(npr. napake prepisa krajevnih imen) in datoteko shranite v git – demo potem vedno pokaže to besedilo.
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
    cilj = zvok._prepis_json()
    if cilj.exists() and not a.prepisi:
        print(f"{cilj} že obstaja – za prepis dodajte --prepisi.", file=sys.stderr)
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
    podatki = {"_opomba": f"Vnaprej določeno besedilo zvočnega klica za demo (vir: {vir}). Popravite po potrebi."}
    podatki.update(besedila)
    cilj.write_text(json.dumps(podatki, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Shranjeno: {cilj} ({len(besedila)} posnetkov, vir: {vir})")
    for k, v in besedila.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
