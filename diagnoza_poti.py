"""Diagnoza poti po cestah. Zaženite v mapi projekta:  python diagnoza_poti.py
Pove, ali je ključ za poti nastavljen in kaj ponudnik poti odgovori na en testni izračun (UKC -> Šiška).
Ključa ne izpiše (le zadnje 4 znake)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent


def nalozi_env() -> Path | None:
    f = KOREN / ".env"
    if not f.exists():
        return None
    for v in f.read_text(encoding="utf-8").splitlines():
        v = v.strip()
        if v and not v.startswith("#") and "=" in v:
            k, x = v.split("=", 1)
            os.environ.setdefault(k.strip(), x.strip().strip('"').strip("'"))
    return f


def preveri(casovna_omejitev: float = 8.0) -> tuple[bool, str]:
    """(deluje, opis). En zahtevek k ponudniku poti."""
    import httpx
    kljuc = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not kljuc:
        return False, "TOMTOM_API_KEY ni nastavljen (dodajte ga v .env v mapi projekta) – vozila bodo vozila po zračni razdalji"
    url = "https://api.tomtom.com/routing/1/calculateRoute/46.05452,14.52218:46.0740,14.4850/json"
    try:
        r = httpx.get(url, params={"key": kljuc, "traffic": "true"}, timeout=casovna_omejitev)
    except httpx.HTTPError as e:
        return False, f"ni povezave do ponudnika poti ({type(e).__name__}) – preverite internet, požarni zid ali proxy"
    konec = f"ključ …{kljuc[-4:]}"
    if r.status_code == 200:
        try:
            s = r.json()["routes"][0]["summary"]
            return True, f"poti po cestah delujejo ({s['lengthInMeters'] / 1000:.1f} km, {round(s['travelTimeInSeconds'] / 60)} min; {konec})"
        except (ValueError, KeyError, IndexError):
            return False, f"nepričakovan odgovor ({konec})"
    telo = " ".join(r.text.split())[:160]
    if r.status_code in (429,) or "over qps" in telo.lower():
        return False, f"HTTP {r.status_code}: preveč zahtevkov na sekundo – počakajte nekaj sekund ({konec}; {telo})"
    if "over rate" in telo.lower() or "quota" in telo.lower():
        return False, f"HTTP {r.status_code}: porabljena dnevna kvota ključa – nov ključ ali počakajte do jutri ({konec}; {telo})"
    if r.status_code in (401, 403):
        return False, f"HTTP {r.status_code}: ključ ni veljaven ali nima vklopljenega Routing API ({konec}; {telo})"
    return False, f"HTTP {r.status_code} ({konec}; {telo})"


if __name__ == "__main__":
    f = nalozi_env()
    print(f"Python: {sys.executable}")
    print(f".env: {f if f else 'NI DATOTEKE .env v ' + str(KOREN)}")
    ok, opis = preveri()
    print(("✔ " if ok else "✖ ") + opis)
    raise SystemExit(0 if ok else 1)
