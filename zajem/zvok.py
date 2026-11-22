"""Zvočni klic v aplikaciji: posnetki (poslusalec/zvok) -> prepis govora z Whisperjem
(poslusalec/poslusalec.py, faster-whisper) -> segmenti klica -> AI poslušalec (poslusalec/ai.py) -> predlogi.

Vir besedila (AID_ZVOK_VIR):
  - »demo« (privzeto, če je besedilo na voljo): zvok se predvaja kot pri pravem klicu, besedilo pa je vnaprej
    določeno – iz poslusalec/zvok/prepis.json ali iz zadnjega Whisperjevega prepisa istih posnetkov
    (poslusalec/prepisi/klic_*.json). Zanesljiv demo brez modela; videz v vmesniku je enak.
  - »whisper«: besedilo samo iz Whisperja. Če Whisper ni na voljo, se to jasno pokaže, dispečer pa klic
    zapisuje ročno (specifikacija 7, zadnja rezerva).
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
PAVZA_S = float(os.environ.get("AID_ZVOK_PAVZA_S", "0.6"))  # premor med posnetki (odgovor sogovornika)

# stanje modela (en model na proces): ni_zagnan | nalagam | pripravljen | napaka
MODEL: dict = {"stanje": "ni_zagnan", "razlog": "", "cas_napake": 0.0, "model": None, "naloga": None, "preneseno_mb": 0}
objava = None  # Objava centra (nastavi app ob zagonu) – za sporočilo »prepis_model« v vmesnik


class PrepisNiNaVoljo(Exception):
    pass


# ---------------- demo: vnaprej določeno besedilo ----------------
def _prepis_json() -> Path:
    return Path(os.environ.get("AID_ZVOK_PREPIS") or pp.mapa_posnetkov() / "prepis.json")


def _iz_shranjenega_prepisa(posnetki: list[Path]) -> tuple[dict, str] | None:
    """Zadnji prepis istih posnetkov, ki ga je shranil poslušalec (»Dispečer: … Klicatelj: …«), razdeljen po
    posnetkih; uporabi se le, če se število in vrstni red govorcev ujemata."""
    import json
    import re
    mapa = Path(os.environ.get("AID_PREPISI_MAPA") or pp.MAPA / "prepisi")
    vloge = [pp.oznaka_in_vloga(p)[1] for p in posnetki]
    if None in vloge:
        return None
    for d in sorted(mapa.glob("klic_*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            besedilo = json.loads(d.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        deli = re.findall(r"(Dispečer|Klicatelj):\s*(.*?)(?=\s*(?:Dispečer|Klicatelj):|\Z)", besedilo, re.S)
        if len(deli) == len(posnetki) and all(pp.GOVORCI[v] == g for v, (g, _) in zip(vloge, deli)):
            return {p.name: b.strip() for p, (_, b) in zip(posnetki, deli)}, d.name
    return None


def _klici_json() -> Path:
    from api.storitev import KOREN
    return Path(os.environ.get("AID_DEMO_KLICI") or KOREN / "demo" / "klici.json")


def besedilo_demo() -> tuple[dict, str] | None:
    """({posnetek: besedilo}, vir) za demo ali None. Prednost: »prepis« zvočnega pogovora v demo/klici.json,
    nato poslusalec/zvok/prepis.json, nato zadnji Whisperjev prepis istih posnetkov."""
    import json
    try:
        posnetki = pp.posnetki_klica()
    except FileNotFoundError:
        return None
    try:
        for k in json.loads(_klici_json().read_text(encoding="utf-8")).get("klici", []):
            pr = k.get("prepis") or {}
            if k.get("posnetki") and pr and all(p.name in pr and str(pr[p.name]).strip() for p in posnetki):
                return {p.name: str(pr[p.name]).strip() for p in posnetki}, _klici_json().name
    except (OSError, ValueError, AttributeError):
        pass
    f = _prepis_json()
    if f.exists():
        try:
            podatki = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            dnevnik.error("%s ni veljaven JSON: %s", f, e)
            podatki = {}
        besedila = {k: str(v).strip() for k, v in podatki.items() if not k.startswith("_")}
        manjka = [p.name for p in posnetki if p.name not in besedila]
        if not manjka:
            return besedila, f.name
        dnevnik.warning("%s nima besedila za %s – uporabim shranjen prepis ali Whisper", f.name, ", ".join(manjka))
    return _iz_shranjenega_prepisa(posnetki)


def nacin() -> str:
    """'demo' ali 'whisper' (AID_ZVOK_VIR: demo | whisper | samodejno; privzeto samodejno = demo, če je besedilo)."""
    v = os.environ.get("AID_ZVOK_VIR", "samodejno").lower()
    if v == "whisper":
        return "whisper"
    return "demo" if besedilo_demo() else "whisper"


class DemoBesedilo:
    """Vmesnik kot faster-whisper (transcribe), besedilo pa je vnaprej določeno za vsak posnetek."""

    def __init__(self, besedila: dict):
        self.besedila = besedila

    def transcribe(self, pot, **_):
        class S:
            def __init__(self, t, konec):
                self.start, self.end, self.text = 0.0, konec, t
        return [S(self.besedila.get(Path(pot).name, ""), pp.trajanje_s(pot))], None


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
    if nacin() == "demo":
        return {"stanje": "demo", "model": "demo", "preneseno_mb": 0, "razlog": ""}
    st = MODEL["stanje"] if whisper_na_voljo() else "ni_namescen"
    return {"stanje": st, "model": ime_modela(), "preneseno_mb": MODEL.get("preneseno_mb", 0), "razlog": MODEL["razlog"] if st == "napaka" else
            (razlog_ni_whisperja() if st == "ni_namescen" else "")}


def _objavi_stanje():
    if objava:
        objava.objavi("prepis_model", stanje_modela())


def _mapa_modela() -> Path:
    hf = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    return hf / f"models--Systran--faster-whisper-{ime_modela()}"


def _velikost_mb(mapa: Path) -> int:
    try:
        return int(sum(f.stat().st_size for f in mapa.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6)
    except OSError:
        return 0


async def _spremljaj_prenos():
    """Med nalaganjem vsaki 2 s objavi, koliko MB modela je že prenesenega (prvi zagon: ~480 MB)."""
    prej = -1
    while MODEL["stanje"] == "nalagam":
        mb = _velikost_mb(_mapa_modela())
        if mb != prej:
            MODEL["preneseno_mb"] = mb
            _objavi_stanje()
            if mb > prej + 50 or prej < 0:
                print(f"[prepis] Whisper {ime_modela()}: preneseno {mb} MB …", flush=True)
            prej = mb
        await asyncio.sleep(2)


async def _nalozi():
    MODEL.update(stanje="nalagam", razlog="", preneseno_mb=0)
    _objavi_stanje()
    print(f"[prepis] Nalagam Whisper '{ime_modela()}' (prvi zagon prenese ~480 MB s huggingface.co) …", flush=True)
    spremljanje = asyncio.ensure_future(_spremljaj_prenos())
    try:
        MODEL["model"] = await asyncio.to_thread(pp.nalozi_model)
        print(f"[prepis] Whisper '{ime_modela()}' pripravljen.", flush=True)
        MODEL.update(stanje="pripravljen")
        dnevnik.info("Whisper '%s' pripravljen.", ime_modela())
    except Exception as e:  # noqa: BLE001 – brez interneta ob prvem zagonu, poškodovan model …
        MODEL.update(stanje="napaka", cas_napake=time.time(), razlog=f"model se ni naložil ({type(e).__name__}: {str(e)[:200]})")
        dnevnik.error("Whisper ni na voljo: %s", MODEL["razlog"])
    finally:
        spremljanje.cancel()
        _objavi_stanje()


def prednalozi() -> None:
    """Ob zagonu strežnika začne nalagati Whisper v ozadju (če je nameščen)."""
    if nacin() == "demo":
        print(f"[prepis] Demo: besedilo klica je vnaprej določeno ({besedilo_demo()[1]}); Whisper se ne nalaga.", flush=True)
        return
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
    """Predvaja posnetke klica in jih kot segmente doda v klic (besedilo: demo ali Whisper). Vrne število segmentov."""
    vir = "whisper"
    if model is None and nacin() == "demo":
        model, vir = DemoBesedilo(besedilo_demo()[0]), "demo"
    elif model is None:
        if MODEL["stanje"] != "pripravljen":
            center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "nalagam",
                                                   "sporocilo": f"Nalagam Whisper ({ime_modela()}) …"})
        model = await pripravi_model()
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "v_zivo", "vir": vir,
                                           "sporocilo": "Prepis govora v živo." if vir == "demo"
                                           else f"Prepis govora v živo (Whisper {ime_modela()})."})
    mapa = Path(os.environ.get("AID_PREPISI_MAPA") or pp.MAPA / "prepisi")
    zapis = pp.Prepis(mapa) if vir == "whisper" else None  # prepis Whisperja se shrani kot pri samostojnem poslušalcu
    predvajaj = pp.predvajaj_lokalno if os.environ.get("AID_PREDVAJAJ_ZVOK") == "1" else None  # še na strežniku
    posnetki = pp.posnetki_klica()
    hitrost = max(0.25, hitrost)
    cas, n = 0.0, 0
    zanka = asyncio.get_running_loop()
    zacetek = zanka.time()
    try:
        for i, posnetek in enumerate(posnetki, 1):
            if prekini and prekini():
                break
            oznaka, vloga = pp.oznaka_in_vloga(posnetek)
            trajanje = await asyncio.to_thread(pp.trajanje_s, posnetek)
            # brskalnik dispečerja predvaja posnetek (kot da posluša klic), Whisper ga hkrati prepisuje
            center.objava.objavi("zvok_odsek", {"klic_id": klic_id, "posnetek": posnetek.name, "st": i, "skupaj": len(posnetki),
                                                "vloga": vloga or "klic", "trajanje_s": round(trajanje, 2), "hitrost": hitrost, "vir": vir})
            opravila = [asyncio.to_thread(pp.prepisi_casovno, model, posnetek)]
            if predvajaj:
                opravila.append(asyncio.to_thread(predvajaj, posnetek))
            deli = (await asyncio.gather(*opravila))[0]
            if vloga:  # NN_vloga.*: en odsek enega govorca
                deli = [(0.0, trajanje, " ".join(t for _, _, t in deli))]
            if not any(t for _, _, t in deli):
                print(f"[prepis] {oznaka}: (Whisper ni razbral govora)", flush=True)
            for od, do, besedilo in deli:
                do = min(max(do, od), trajanje) if do else trajanje
                if not predvajaj:  # odsek je »izrečen« šele ob svojem koncu (realni čas, pospešen s hitrostjo)
                    await asyncio.sleep(max(0.0, zacetek + (cas + do) / hitrost - zanka.time()))
                if prekini and prekini():
                    break
                if not besedilo:
                    continue
                v = vloga or "klicatelj"  # cel klic brez vlog v imenu: vse kot klicatelj (dispečer lahko popravi)
                print(f"[prepis] {oznaka} {pp.GOVORCI[v]}: {besedilo}", flush=True)
                center.dodaj_segment(klic_id, v, besedilo, cas + od, cas + do)
                n += 1
                if zapis:
                    await zapis.segment(f"{oznaka}-{n}", f"{pp.GOVORCI[v]}: {besedilo}")
            cas += trajanje
            if i < len(posnetki) and vloga:  # kratek premor med govorcema, kot v pravem pogovoru
                cas += PAVZA_S
            if not predvajaj:  # naslednji posnetek šele, ko se ta izteče (+ premor)
                await asyncio.sleep(max(0.0, zacetek + cas / hitrost - zanka.time()))
    finally:
        if zapis:
            await zapis.close()
    center.objava.objavi("prepis_stanje", {"klic_id": klic_id, "stanje": "konec", "vir": vir,
                                           "sporocilo": f"Prepis končan ({n} odsekov)."})
    return n
