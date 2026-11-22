"""Zvočni klic v aplikaciji: posnetki -> prepis govora (poslusalec/poslusalec.py, faster-whisper)
-> segmenti klica -> AI poslušalec (poslusalec/ai.py) -> predlogi za dispečerja.

Redundanca (specifikacija 7, vrstica »Prepis«):
  Whisper (lokalno, faster-whisper) -> zadnji shranjen resnični prepis istih posnetkov -> dispečer tipka ročno.
Zvok ostane na strežniku; v vmesnik gre samo besedilo (pravilo 2.7). Predvajanje na strežniku
le z AID_PREDVAJAJ_ZVOK=1 (npr. demo na enem računalniku).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from pathlib import Path

from poslusalec import poslusalec as pp

dnevnik = logging.getLogger("prepis")


class PrepisNiNaVoljo(Exception):
    pass


def whisper_na_voljo() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


_neuspeh = {"cas": 0.0, "razlog": ""}


async def pripravi_model():
    """(model, opis vira). Whisper se naloži enkrat (prvič lahko traja – prenos modela).
    Po neuspehu (npr. brez interneta) 5 min ne poskuša znova, da klici ne čakajo."""
    import time
    if os.environ.get("AID_ZVOK_REZERVA") != "1" and whisper_na_voljo() and time.time() - _neuspeh["cas"] > 300:
        try:
            return await asyncio.to_thread(pp.nalozi_model), "whisper"
        except Exception as e:  # noqa: BLE001 – brez interneta ob prvem zagonu, poškodovan model …
            _neuspeh["cas"] = time.time()
            _neuspeh["razlog"] = f"model se ni naložil ({type(e).__name__}: {str(e)[:160]})"
            dnevnik.warning("Whisper ni na voljo: %s", e)
    if os.environ.get("AID_ZVOK_REZERVA") == "1":
        _neuspeh["razlog"] = "AID_ZVOK_REZERVA=1"
    elif not whisper_na_voljo():
        _neuspeh["razlog"] = f"faster-whisper ni nameščen v Pythonu {sys.executable} (aktivirajte .venv in pip install -r requirements-zvok.txt)"
    dnevnik.warning("Prepis govora: rezerva – %s", _neuspeh["razlog"])
    try:
        return pp.ShranjenPrepis(), "shranjen"
    except FileNotFoundError as e:
        raise PrepisNiNaVoljo("Prepis govora ni na voljo: namestite faster-whisper "
                              "(pip install -r requirements-zvok.txt) ali tipkajte ročni zapis.") from e


async def predvajaj_zvocni_klic(center, klic_id: str, hitrost: float = 1.0, prekini=None, model=None) -> int:
    """Doda segmente iz posnetkov v klic. Vrne število segmentov."""
    if model is None:
        center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "nalagam", "sporocilo": "Nalagam model za prepis govora …"})
        model, vir = await pripravi_model()
    else:
        vir = "whisper"
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "v_zivo", "vir": vir,
                                           "sporocilo": "Prepis govora v živo." if vir == "whisper"
                                           else "Whisper ni na voljo – uporabljen zadnji shranjen prepis teh posnetkov. Razlog: "
                                           + (_neuspeh["razlog"] or "neznan")})
    mapa = Path(os.environ.get("AID_PREPISI_MAPA") or pp.MAPA / "prepisi")
    zapis = pp.Prepis(mapa) if vir == "whisper" else None  # kot samostojni poslušalec
    predvajaj = pp.predvajaj_lokalno if os.environ.get("AID_PREDVAJAJ_ZVOK") == "1" else None
    cas, n = 0.0, 0
    zanka = asyncio.get_running_loop()
    zacetek = zanka.time()
    async for o in pp.odseki_klica(model, predvajaj):
        if not predvajaj:  # realen čas klica (pospešen s hitrostjo): odsek je »izrečen« šele ob svojem koncu
            await asyncio.sleep(max(0.0, zacetek + (cas + o["trajanje_s"]) / max(0.25, hitrost) - zanka.time()))
        if prekini and prekini():
            break
        od, cas = cas, cas + o["trajanje_s"]
        if not o["besedilo"]:
            dnevnik.info("odsek %s: model ni razbral govora", o["oznaka"])
            continue
        center.dodaj_segment(klic_id, o["vloga"], o["besedilo"], od, cas)
        n += 1
        if zapis:
            await zapis.segment(o["oznaka"], f"{o['govorec']}: {o['besedilo']}")
    if zapis:
        await zapis.close()
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "konec", "vir": vir, "sporocilo": f"Prepis končan ({n} odsekov)."})
    return n
