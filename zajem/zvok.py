"""Zvočni klic v aplikaciji: posnetki (poslusalec/zvok) -> prepis govora z Whisperjem
(poslusalec/poslusalec.py, faster-whisper) -> segmenti klica -> AI poslušalec (poslusalec/ai.py) -> predlogi.

Besedilo klica prihaja SAMO iz Whisperja – ni vnaprej določenega ali shranjenega besedila.
Če Whisper ni na voljo, se to jasno pokaže, dispečer pa klic zapisuje ročno (specifikacija 7, zadnja rezerva).
Model se naloži ob zagonu strežnika (prednalozi), da prvi klic ne čaka.
Zvok ostane na strežniku; v vmesnik gre samo besedilo (pravilo 2.7). Predvajanje na strežniku
le z AID_PREDVAJAJ_ZVOK=1 (npr. demo na enem računalniku).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import time
from pathlib import Path

from poslusalec import poslusalec as pp

dnevnik = logging.getLogger("prepis")
PONOVNI_POSKUS_S = 60

# stanje modela (en model na proces): ni_zagnan | nalagam | pripravljen | napaka
MODEL: dict = {"stanje": "ni_zagnan", "razlog": "", "cas_napake": 0.0, "model": None, "naloga": None}
objava = None  # Objava centra (nastavi app ob zagonu) – za sporočilo »prepis_model« v vmesnik


class PrepisNiNaVoljo(Exception):
    pass


def whisper_na_voljo() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def razlog_ni_whisperja() -> str:
    return (f"Prepis govora ni na voljo: faster-whisper ni nameščen v Pythonu {sys.executable}. "
            "Zaženite ./zazeni.sh ali v .venv: pip install -r requirements-zvok.txt")


def lahko_poskusi_znova() -> bool:
    return time.time() - MODEL["cas_napake"] > PONOVNI_POSKUS_S


def ime_modela() -> str:
    return os.environ.get("AID_WHISPER_MODEL", "small")


def stanje_modela() -> dict:
    st = MODEL["stanje"] if whisper_na_voljo() else "ni_namescen"
    return {"stanje": st, "model": ime_modela(), "razlog": MODEL["razlog"] if st == "napaka" else
            (razlog_ni_whisperja() if st == "ni_namescen" else "")}


def _objavi_stanje():
    if objava:
        objava.objavi("prepis_model", stanje_modela())


async def _nalozi():
    MODEL.update(stanje="nalagam", razlog="")
    _objavi_stanje()
    dnevnik.info("Nalagam Whisper '%s' (prvič se model prenese) …", ime_modela())
    try:
        MODEL["model"] = await asyncio.to_thread(pp.nalozi_model)
        MODEL.update(stanje="pripravljen")
        dnevnik.info("Whisper '%s' pripravljen.", ime_modela())
    except Exception as e:  # noqa: BLE001 – brez interneta ob prvem zagonu, poškodovan model …
        MODEL.update(stanje="napaka", cas_napake=time.time(), razlog=f"model se ni naložil ({type(e).__name__}: {str(e)[:200]})")
        dnevnik.error("Whisper ni na voljo: %s", MODEL["razlog"])
    finally:
        _objavi_stanje()


def prednalozi() -> None:
    """Ob zagonu strežnika začne nalagati Whisper v ozadju (če je nameščen)."""
    if whisper_na_voljo() and MODEL["stanje"] in ("ni_zagnan", "napaka") and os.environ.get("AID_WHISPER_PREDNALOZI", "1") == "1":
        MODEL["naloga"] = asyncio.ensure_future(_nalozi())


async def pripravi_model():
    """Naložen WhisperModel. Hkratni klici počakajo isto nalaganje. Brez Whisperja: PrepisNiNaVoljo."""
    if not whisper_na_voljo():
        raise PrepisNiNaVoljo(razlog_ni_whisperja())
    if MODEL["stanje"] == "pripravljen":
        return MODEL["model"]
    if MODEL["stanje"] == "nalagam" and MODEL["naloga"]:
        await asyncio.shield(MODEL["naloga"])
    elif MODEL["stanje"] == "ni_zagnan" or (MODEL["stanje"] == "napaka" and lahko_poskusi_znova()):
        MODEL["naloga"] = asyncio.ensure_future(_nalozi())
        await asyncio.shield(MODEL["naloga"])
    if MODEL["stanje"] != "pripravljen":
        raise PrepisNiNaVoljo("Prepis govora ni na voljo: " + MODEL["razlog"])
    return MODEL["model"]


async def predvajaj_zvocni_klic(center, klic_id: str, hitrost: float = 1.0, prekini=None, model=None) -> int:
    """Prepiše posnetke klica z Whisperjem in jih kot segmente doda v klic. Vrne število segmentov."""
    if model is None:
        if MODEL["stanje"] != "pripravljen":
            center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "nalagam",
                                                   "sporocilo": f"Nalagam Whisper ({ime_modela()}) …"})
        model = await pripravi_model()
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "v_zivo", "vir": "whisper",
                                           "sporocilo": f"Prepis govora v živo (Whisper {ime_modela()})."})
    mapa = Path(os.environ.get("AID_PREPISI_MAPA") or pp.MAPA / "prepisi")
    zapis = pp.Prepis(mapa)  # prepis se shrani kot pri samostojnem poslušalcu
    predvajaj = pp.predvajaj_lokalno if os.environ.get("AID_PREDVAJAJ_ZVOK") == "1" else None
    cas, n = 0.0, 0
    zanka = asyncio.get_running_loop()
    zacetek = zanka.time()
    try:
        async for o in pp.odseki_klica(model, predvajaj):
            if not predvajaj:  # realen čas klica (pospešen s hitrostjo): odsek je »izrečen« šele ob svojem koncu
                await asyncio.sleep(max(0.0, zacetek + (cas + o["trajanje_s"]) / max(0.25, hitrost) - zanka.time()))
            if prekini and prekini():
                break
            od, cas = cas, cas + o["trajanje_s"]
            if not o["besedilo"]:
                dnevnik.info("odsek %s: Whisper ni razbral govora", o["oznaka"])
                continue
            center.dodaj_segment(klic_id, o["vloga"], o["besedilo"], od, cas)
            n += 1
            await zapis.segment(o["oznaka"], f"{o['govorec']}: {o['besedilo']}")
    finally:
        await zapis.close()
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "konec", "vir": "whisper",
                                           "sporocilo": f"Prepis končan ({n} odsekov)."})
    return n
