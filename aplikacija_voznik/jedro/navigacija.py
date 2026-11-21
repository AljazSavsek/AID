"""Navigacija po POTRJENI poti (geometrija iz /voznik/naloga): preostala razdalja, ocena prihoda,
smer do naslednje točke, odmik od poti. Brez navodil ponudnika (ta niso na voljo brez ključev)
voznik vidi smer in razdaljo. Čisto računanje – brez omrežja."""
from __future__ import annotations

import math

R = 6371000.0
SMERI = ("S", "SV", "V", "JV", "J", "JZ", "Z", "SZ")


def razdalja_m(a, b) -> float:
    r = math.pi / 180
    dlat, dlon = (b[0] - a[0]) * r, (b[1] - a[1]) * r
    x = math.sin(dlat / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, x)))


def azimut(a, b) -> float:
    r = math.pi / 180
    y = math.sin((b[1] - a[1]) * r) * math.cos(b[0] * r)
    x = math.cos(a[0] * r) * math.sin(b[0] * r) - math.sin(a[0] * r) * math.cos(b[0] * r) * math.cos((b[1] - a[1]) * r)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def smer_besedilo(stopinje: float) -> str:
    return SMERI[int((stopinje + 22.5) // 45) % 8]


def _projekcija(p, a, b):
    """Najbližja točka na odseku a-b (lokalna ravnina) in delež t."""
    kx = math.cos(math.radians(p[0]))
    ax, ay, bx, by, px, py = a[1] * kx, a[0], b[1] * kx, b[0], p[1] * kx, p[0]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), t


def stanje_navigacije(polozaj, pot: dict | None, cilj=None, hitrost_ms: float | None = None, prag_odmika_m: float = 80.0) -> dict:
    """polozaj: (lat, lon); pot: {geometrija, cas_s, razdalja_m, kakovost, cestna}.
    Vrne {preostalo_m, eta_s, naslednja: (lat, lon), razdalja_do_naslednje_m, smer, odmik_m, izven_poti, kakovost}."""
    geo = [tuple(t) for t in (pot or {}).get("geometrija") or []]
    if cilj is None and geo:
        cilj = geo[-1]
    if polozaj is None or cilj is None:
        return {"preostalo_m": None, "eta_s": None, "naslednja": cilj, "smer": None, "odmik_m": None, "izven_poti": False,
                "kakovost": (pot or {}).get("kakovost")}
    if len(geo) < 2 or not (pot or {}).get("cestna", True):
        d = razdalja_m(polozaj, cilj)
        v = hitrost_ms if hitrost_ms and hitrost_ms > 2 else ((pot or {}).get("razdalja_m") or d) / max((pot or {}).get("cas_s") or 1, 1)
        return {"preostalo_m": d, "eta_s": d / v if v else None, "naslednja": cilj, "razdalja_do_naslednje_m": d,
                "smer": smer_besedilo(azimut(polozaj, cilj)), "odmik_m": None, "izven_poti": False,
                "kakovost": (pot or {}).get("kakovost")}
    # najbližji odsek
    najb = min(range(len(geo) - 1), key=lambda i: razdalja_m(polozaj, _projekcija(polozaj, geo[i], geo[i + 1])[0]))
    tocka, _ = _projekcija(polozaj, geo[najb], geo[najb + 1])
    odmik = razdalja_m(polozaj, tocka)
    preostalo = razdalja_m(tocka, geo[najb + 1]) + sum(razdalja_m(geo[i], geo[i + 1]) for i in range(najb + 1, len(geo) - 1))
    # naslednja točka: prva oglišča vsaj 30 m naprej (manj trzanja pri gostih geometrijah)
    naslednja = next((g for g in geo[najb + 1:] if razdalja_m(tocka, g) > 30), geo[-1])
    skupaj_m = pot.get("razdalja_m") or sum(razdalja_m(geo[i], geo[i + 1]) for i in range(len(geo) - 1))
    povp = skupaj_m / max(pot.get("cas_s") or 1, 1)  # povprečna hitrost potrjene poti (upošteva promet, če je na voljo)
    return {"preostalo_m": preostalo + odmik, "eta_s": (preostalo + odmik) / povp if povp else None, "naslednja": naslednja,
            "razdalja_do_naslednje_m": razdalja_m(polozaj, naslednja), "smer": smer_besedilo(azimut(polozaj, naslednja)),
            "odmik_m": odmik, "izven_poti": odmik > prag_odmika_m, "kakovost": pot.get("kakovost")}
