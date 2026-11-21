"""Sporočila ekip na terenu -> AI poslušalec -> PREDLOG dogodka, ki ga potrdi dispečer (pravilo 2.1).

Prijava, GPS, naloge in stanja voznika so v modulu `voznik/` (API /voznik/*).
"""
from __future__ import annotations

import time

from dnevnik import model as M

from .storitev import AI, Center, NapakaStoritve

def _enota(c: Center, enota_id: str) -> dict:
    e = next((x for x in c.enote() if x["id"] == enota_id), None) if hasattr(c, "enote") else c.db.beri("enota", enota_id)
    if not e:
        raise NapakaStoritve("enota ne obstaja", 404)
    return e


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
