"""Dogodki plana (specifikacija 9) – sprememba modela.

Funkcije spremenijo `stanje` ({enote, naloge, centri}) in vrnejo opis za revizijo.
Kličejo se šele, ko dispečer dogodek potrdi.
"""
from __future__ import annotations

import time

from dnevnik.model import VRSTE_OVIR
from dnevnik.model import naloga as nova_naloga


class NeveljavenDogodek(ValueError):
    pass


def _enota(stanje, eid):
    for e in stanje["enote"]:
        if e["id"] == eid:
            return e
    raise NeveljavenDogodek(f"neznana enota: {eid!r}")


def _naloga(stanje, nid):
    for n in stanje["naloge"]:
        if n["id"] == nid:
            return n
    raise NeveljavenDogodek(f"neznana naloga: {nid!r}")


def uveljavi(stanje: dict, dogodek: dict, zdaj: float | None = None) -> str:
    zdaj = time.time() if zdaj is None else zdaj
    tip, p = dogodek["tip"], dogodek["parametri"]

    if tip == "okvara_vozila":
        if not p.get("enota_id"):
            raise NeveljavenDogodek("manjka enota_id – katero vozilo?")
        e = _enota(stanje, p["enota_id"])
        lok = p.get("lokacija") or {"lat": e["lat"], "lon": e["lon"]}
        e["stanje"] = "okvara"
        nove = []
        # tovor postane nova naloga prevzema na lokaciji okvare (dostavi ga lahko drugo vozilo)
        if p.get("tovor_ostane", True):
            for t in e.get("tovor", []):
                stara = _naloga(stanje, t["naloga_id"])
                stara["stanje"] = "prenesena"
                n = nova_naloga(stara["tip"], {"lat": lok["lat"], "lon": lok["lon"], "naslov": f"Lokacija okvare {e['id']}"},
                                stara["prioriteta"], dostava=stara.get("dostava"), okno_od=zdaj, okno_do=stara.get("okno_do"),
                                pacienti=t.get("pacienti", 0), oprema=t.get("oprema", 0),
                                opis=f"Prevzem tovora iz {e['id']} ({stara.get('opis', '')})")
                n["izvor"] = stara["id"]
                stanje["naloge"].append(n)
                nove.append(n["id"])
        e["tovor"] = []
        return f"{e['id']} izločena (okvara)." + (f" Nova naloga prevzema: {', '.join(nove)}." if nove else "")

    if tip == "megla":
        e = _enota(stanje, p.get("enota_id") or "HELI-1")
        if e["tip"] != "helikopter":
            raise NeveljavenDogodek("megla velja za helikopter")
        zamik = int(p.get("zamik_min") or 60)
        e["na_voljo_od"] = max(e.get("na_voljo_od") or 0, zdaj) + zamik * 60
        return f"{e['id']} na voljo čez {zamik} min (megla)."

    if tip == "nujna_naloga":
        n = p.get("naloga")
        if not n or "prevzem" not in n:
            raise NeveljavenDogodek("manjka naloga")
        n.setdefault("okno_od", zdaj)
        n.setdefault("okno_do", zdaj + 15 * 60)  # visoka prioriteta in ozko okno
        n.setdefault("stanje", "odprta")
        stanje["naloge"].append(n)
        return f"Dodana nujna naloga {n['id']} (P{n['prioriteta']})."

    if tip == "manjsa_zmogljivost_centra":
        c = next((c for c in stanje["centri"] if c["id"] == p.get("center_id")), None)
        if not c or p.get("nova_kapaciteta") is None:
            raise NeveljavenDogodek("manjka center_id ali nova_kapaciteta")
        stara = c["sprejemna_mesta"]
        c["sprejemna_mesta"] = int(p["nova_kapaciteta"])
        return f"{c['ime']}: sprejemna mesta {stara} → {c['sprejemna_mesta']}."

    if tip == "sprememba_prioritete":
        n = _naloga(stanje, p.get("naloga_id"))
        nova = int(p.get("nova_prioriteta") or 0)
        if not 1 <= nova <= 5:
            raise NeveljavenDogodek("prioriteta mora biti 1–5")
        stara = n["prioriteta"]
        n["prioriteta"] = nova
        if nova < stara:  # višja nujnost -> ožje okno
            n["okno_do"] = min(n.get("okno_do") or zdaj + 3600, zdaj + {1: 15, 2: 30, 3: 60}.get(nova, 120) * 60)
        return f"{n['id']}: prioriteta {stara} → {nova}."

    if tip == "ovira_na_cesti":
        # informacija za dispečerja (in usmerjanje v fazi 2); model plana se ne spremeni
        lok = p.get("lokacija") or {}
        if p.get("vrsta") not in VRSTE_OVIR or lok.get("lat") is None or lok.get("lon") is None:
            raise NeveljavenDogodek("ovira potrebuje vrsto in lokacijo")
        return f"Ovira ({p['vrsta']}) pri {lok['lat']:.5f}, {lok['lon']:.5f}" + (f": {p['opis']}" if p.get("opis") else "") + "."

    if tip == "prevoz_z_nujne_voznje":
        # ekipa je na nujni vožnji naložila pacienta; dispečer izbere center
        e = _enota(stanje, p.get("enota_id"))
        n = _naloga(stanje, p.get("naloga_id"))
        c = next((c for c in stanje["centri"] if c["id"] == p.get("center_id")), None)
        if not c:
            raise NeveljavenDogodek("izberite center (bolnišnico)")
        if n.get("stanje") != "odprta":
            raise NeveljavenDogodek(f"naloga {n['id']} ni več odprta")
        if int(e.get("kapaciteta", {}).get("pacienti", 0)) < 1:
            raise NeveljavenDogodek(f"{e['id']} ne more prevažati pacienta")
        if any(t["naloga_id"] == n["id"] for t in e.get("tovor", [])):
            raise NeveljavenDogodek("pacient je že v vozilu")
        pacienti = max(1, int(p.get("pacienti") or 1))
        n["dostava"] = {"lat": c["lat"], "lon": c["lon"], "naslov": c["ime"], "center_id": c["id"]}
        n["kolicina"] = {**n.get("kolicina", {}), "pacienti": pacienti}
        e.setdefault("tovor", []).append({"naloga_id": n["id"], "pacienti": pacienti, "oprema": 0})
        return f"{n['id']}: pacient v {e['id']}, prevoz v {c['ime']}."

    if tip == "enota_ni_na_voljo":
        e = _enota(stanje, p.get("enota_id"))
        if e.get("tovor"):
            raise NeveljavenDogodek(f"{e['id']} ima tovor – najprej dostava")
        e["stanje"] = "ni_na_voljo"
        return f"{e['id']} ni na voljo" + (f" ({p['razlog']})" if p.get("razlog") else "") + "."

    raise NeveljavenDogodek(f"neznan tip: {tip}")
