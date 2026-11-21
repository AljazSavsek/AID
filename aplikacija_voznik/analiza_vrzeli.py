"""Analiza preizkusa na napravi: vrzeli v zgodovini položajev enote (GPS v ozadju).

    python aplikacija_voznik/analiza_vrzeli.py --streznik http://IP:8000 --enota RV-2 --minut 40 \\
        --zeton $AID_SKRBNIK_ZETON [--prag 15]

Izpiše število fiksov, mediano intervala, vse vrzeli nad pragom in delež pokritega časa.
"""
from __future__ import annotations

import argparse
import statistics
import time

import httpx


def analiziraj(fiksi: list[dict], prag_s: float) -> dict:
    ts = sorted(f["ts"] for f in fiksi)
    if len(ts) < 2:
        return {"fiksi": len(ts), "vrzeli": [], "pokritost": 0.0}
    razmiki = [b - a for a, b in zip(ts, ts[1:])]
    vrzeli = [(a, b - a) for a, b in zip(ts, ts[1:]) if b - a > prag_s]
    skupaj = ts[-1] - ts[0]
    return {"fiksi": len(ts), "od": ts[0], "do": ts[-1], "mediana_s": statistics.median(razmiki),
            "najvecja_s": max(razmiki), "vrzeli": vrzeli,
            "pokritost": 1 - sum(max(0, d - prag_s) for _, d in vrzeli) / skupaj if skupaj else 0.0,
            "oznaceni": sum(1 for f in fiksi if f.get("oznaka"))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--streznik", default="http://localhost:8000")
    ap.add_argument("--enota", required=True)
    ap.add_argument("--minut", type=float, default=40)
    ap.add_argument("--zeton", required=True, help="AID_SKRBNIK_ZETON")
    ap.add_argument("--prag", type=float, default=15, help="vrzel = razmik nad tem (s)")
    a = ap.parse_args()
    r = httpx.get(f"{a.streznik}/interno/polozaji/{a.enota}", params={"od": time.time() - a.minut * 60},
                  headers={"X-Skrbnik-Zeton": a.zeton, "X-Uporabnik": "preizkus naprave"}, timeout=20)
    r.raise_for_status()
    izid = analiziraj(r.json(), a.prag)
    print(f"fiksov: {izid['fiksi']}, mediana razmika: {izid.get('mediana_s', 0):.1f} s, "
          f"največji: {izid.get('najvecja_s', 0):.0f} s, pokritost: {izid['pokritost'] * 100:.1f} %, označenih: {izid.get('oznaceni', 0)}")
    for zac, d in izid["vrzeli"]:
        print(f"  vrzel {time.strftime('%H:%M:%S', time.localtime(zac))}  {d:.0f} s")
    print("USPEH" if izid["fiksi"] and izid["pokritost"] >= 0.98 and not any(d > 60 for _, d in izid["vrzeli"]) else "NEUSPEH")
