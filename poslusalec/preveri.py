"""Preizkus AI poslušalca z resničnim ključem (ne teče v testih – stane nekaj centov).

    python -m poslusalec.preveri

Pošlje demo klic po segmentih in izpiše vir, čas odziva in predloge. Cilj specifikacije: ≤ 3 s v oblaku.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOREN))


async def main() -> int:
    import importlib
    importlib.import_module("api.app")  # naloži .env
    from poslusalec.ai import OblakLLM, Poslusalec

    if not OblakLLM().na_voljo():
        print("ANTHROPIC_API_KEY ni nastavljen (v .env ali okolju) – ni kaj preizkusiti.")
        return 1
    p = Poslusalec([OblakLLM()])
    klic = json.loads((KOREN / "tests" / "podatki" / "klic1.json").read_text(encoding="utf-8"))
    segmenti = [dict(s, id=f"s{i + 1}") for i, s in enumerate(klic["segmenti"])]
    casi = []
    for n in (2, 4, 6, 8, 12):
        t = time.perf_counter()
        izhod = await p.obdelaj(segmenti[:n], [], [], [])
        casi.append(time.perf_counter() - t)
        if izhod is None:
            print(f"✖ po {n} segmentih: napaka – {p.napake}")
            return 1
        polja = ", ".join(f"{u['polje']}={u['vrednost']!r} ({u['zaupanje']})" for u in izhod["posodobitve"])
        print(f"✔ {n:2d} segmentov · {casi[-1]:.1f} s · {polja}")
    print(f"\nPovprečni čas: {sum(casi) / len(casi):.1f} s (cilj ≤ 3 s). Najdaljši: {max(casi):.1f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
