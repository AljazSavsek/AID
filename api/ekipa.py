"""Ekipa na terenu (voznik reševalnega vozila).

- pregled: naslednji korak iz AKTIVNEGA (potrjenega) plana, pot po cestah s prometom,
  navodila za zavijanje in opozorila; podatki naloge so samo tisti, ki jih je potrdil dispečer
- lokacija vozila (GPS) -> dispečer vidi premik (WS `enota_premik`)
- status naloge (na poti, na kraju, prevzeto, dostavljeno)
- sporočilo centru -> AI poslušalec -> PREDLOG dogodka, ki ga potrdi dispečer (pravilo 2.1)
"""
from __future__ import annotations

import time

from dnevnik import model as M

from .storitev import AI, Center, NapakaStoritve

STATUSI = ("na_poti", "na_kraju", "prevzeto", "dostavljeno", "opravljeno")


def _enota(c: Center, enota_id: str) -> dict:
    e = c.db.beri("enota", enota_id)
    if not e:
        raise NapakaStoritve("enota ne obstaja", 404)
    return e


def _koraki(c: Center, e: dict) -> tuple[dict | None, list[dict]]:
    plan = c.plan_aktiven()
    if not plan:
        return None, []
    opravljeni = set(e.get("opravljeni_koraki", []))
    out = []
    for k in plan["koraki_po_enoti"].get(e["id"], []):
        if f'{k["naloga_id"]}:{k["akcija"]}' in opravljeni:
            continue
        n = c.db.beri("naloga", k["naloga_id"])
        if not n or n.get("stanje") in ("opravljena", "preklicana", "prenesena"):
            continue
        mesto = n["prevzem"] if k["akcija"] == "prevzem" else (n.get("dostava") or n["prevzem"])
        out.append({**k, "naslov": mesto.get("naslov"), "orientacija": mesto.get("orientacija"),
                    "naloga": {"id": n["id"], "tip": n["tip"], "opis": n.get("opis", ""), "prioriteta": n["prioriteta"],
                               "kolicina": n.get("kolicina"), "podatki_klica": n.get("podatki_klica") or {},
                               "status_ekipe": n.get("status_ekipe", [])[-1:]}})
    return plan, out


async def pregled(c: Center, enota_id: str) -> dict:
    e = _enota(c, enota_id)
    plan, koraki = _koraki(c, e)
    korak = koraki[0] if koraki else None
    pot = None
    if korak:
        p = await c.usmerjanje.izracunaj((e["lat"], e["lon"]), (korak["lat"], korak["lon"]), e["tip"])
        zamik = max(0, (e.get("na_voljo_od") or 0) - time.time())
        pot = {**p, "cas_s": round(p["cas_s"] + zamik), "zamik_s": round(zamik),
               "geometrija": Center._redci(p["geometrija"], 600), "prihod": time.time() + p["cas_s"] + zamik}
    dogodki = [d for d in c.db.vsi("dogodek") if d.get("stanje") in ("sprejeto", "spremenjeno")
               and (d["parametri"].get("enota_id") == enota_id or d["tip"] in ("manjsa_zmogljivost_centra",))
               and d.get("ustvarjeno", 0) > time.time() - 3 * 3600]
    return {"enota": e, "plan": {"id": plan["id"], "verzija": plan["verzija"]} if plan else None,
            "korak": korak, "naslednji": koraki[1:4], "pot": pot,
            "obvestila_centra": [{"cas": d.get("ustvarjeno"), "besedilo": d.get("opis") or d["tip"]} for d in dogodki[-3:]]}


def lokacija(c: Center, enota_id: str, lat: float, lon: float, smer: float | None = None, hitrost_kmh: float | None = None) -> dict:
    e = _enota(c, enota_id)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise NapakaStoritve("neveljavna lokacija", 422)
    e.update(lat=lat, lon=lon, smer=smer, hitrost_kmh=hitrost_kmh, lokacija_ob=time.time())
    c.db.shrani("enota", e)
    c.objava.objavi("enota_premik", {"id": enota_id, "lat": lat, "lon": lon, "smer": smer, "hitrost_kmh": hitrost_kmh})
    return e


def status(c: Center, enota_id: str, naloga_id: str, akcija: str, uporabnik: str) -> dict:
    if akcija not in STATUSI:
        raise NapakaStoritve("neznan status", 422)
    e = _enota(c, enota_id)
    n = c.db.beri("naloga", naloga_id)
    if not n:
        raise NapakaStoritve("naloga ne obstaja", 404)
    stara = n.get("stanje")
    n.setdefault("status_ekipe", []).append({"akcija": akcija, "enota_id": enota_id, "cas": time.time()})
    opravljeni = e.setdefault("opravljeni_koraki", [])
    if akcija == "prevzeto":
        opravljeni.append(f"{naloga_id}:prevzem")
        if n.get("dostava") and n["tip"] != "nujna_voznja":
            e.setdefault("tovor", []).append({"naloga_id": naloga_id, **(n.get("kolicina") or {})})
        else:
            n["stanje"] = "opravljena"
    elif akcija in ("dostavljeno", "opravljeno"):
        opravljeni.extend([f"{naloga_id}:prevzem", f"{naloga_id}:dostava"])
        e["tovor"] = [t for t in e.get("tovor", []) if t["naloga_id"] != naloga_id]
        n["stanje"] = "opravljena"
    c.db.shrani("naloga", n)
    c.db.shrani("enota", e)
    c.db.revizija(f"{uporabnik} ({enota_id})", "Naloga", naloga_id, f"status_{akcija}", stara, n["stanje"])
    c.objava.objavi("status_ekipe", {"enota_id": enota_id, "naloga_id": naloga_id, "akcija": akcija})
    c.objava.objavi("enote", c.db.vsi("enota"))
    return n


async def sporocilo(c: Center, enota_id: str, besedilo: str, uporabnik: str) -> dict:
    """Besedilo ekipe -> AI -> predlog dogodka. Dispečer ga potrdi ali zavrne."""
    e = _enota(c, enota_id)
    besedilo = (besedilo or "").strip()
    if not besedilo:
        raise NapakaStoritve("prazno sporočilo")
    sid = M.nov_id("m")
    seg = {"id": sid, "govorec": "klicatelj", "od_s": 0.0, "do_s": 0.0, "besedilo": besedilo}
    c.db.revizija(f"{uporabnik} ({enota_id})", "Sporocilo", sid, "sporocilo_ekipe", None, besedilo)
    st = c.stanje()
    izhod = await c.poslusalec.obdelaj([seg], [], st["enote"], st["centri"])
    predlogi = []
    for d in (izhod or {}).get("zaznani_dogodki", []):
        par = dict(d["parametri"])
        # sporočilo je poslala ta enota: če AI vozila ne navede, ga predlagamo (dispečer potrdi)
        if d["tip"] == "okvara_vozila":
            par.setdefault("enota_id", enota_id)
            par.setdefault("lokacija", {"lat": e["lat"], "lon": e["lon"]})
        dog = M.dogodek(d["tip"], par, "ekipa", "predlagano", d["dokazi"], d["zaupanje"])
        dog.update(sporocilo=besedilo, enota_posiljatelj=enota_id)
        c.db.shrani("dogodek", dog)
        c.db.revizija(AI, "Dogodek", dog["id"], "predlog_ai", None, {"tip": dog["tip"], "parametri": par})
        c.objava.objavi("dogodek_predlog", dog)
        predlogi.append(dog)
    sp = {"id": sid, "enota_id": enota_id, "besedilo": besedilo, "cas": time.time(), "predlogi": [p["id"] for p in predlogi]}
    c.db.shrani("sporocilo", sp)
    c.objava.objavi("sporocilo_ekipe", sp)
    return {"sporocilo": sp, "predlogi_dogodkov": predlogi, "ai_na_voljo": izhod is not None}


async def sprejmi_dogodek(c: Center, dogodek_id: str, uporabnik: str, parametri: dict | None = None) -> dict:
    d = c.db.beri("dogodek", dogodek_id)
    if not d:
        raise NapakaStoritve("dogodek ne obstaja", 404)
    if d["stanje"] != "predlagano":
        raise NapakaStoritve(f"dogodek je že {d['stanje']}", 409)
    if parametri:
        c.db.revizija(uporabnik, "Dogodek", d["id"], "spremeni", d["parametri"], parametri)
        d["parametri"] = parametri
        d["stanje"] = "spremenjeno"
    d["potrdil"] = uporabnik
    plan = await c.uveljavi_dogodek(d, uporabnik)
    return {"dogodek": c.db.beri("dogodek", d["id"]), "plan_predlog": plan}


def zavrni_dogodek(c: Center, dogodek_id: str, uporabnik: str, razlog: str | None = None) -> dict:
    d = c.db.beri("dogodek", dogodek_id)
    if not d:
        raise NapakaStoritve("dogodek ne obstaja", 404)
    if d["stanje"] != "predlagano":
        raise NapakaStoritve(f"dogodek je že {d['stanje']}", 409)
    d.update(stanje="zavrnjeno", razlog=razlog)
    c.db.shrani("dogodek", d)
    c.db.revizija(uporabnik, "Dogodek", d["id"], "zavrni", "predlagano", {"razlog": razlog})
    c.objava.objavi("dogodek", d)
    return d
