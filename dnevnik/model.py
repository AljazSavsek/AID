"""Podatkovni model (specifikacija, razdelek 4).

Objekti so navadni slovarji (JSON), da jih lahko neposredno shranimo v SQLite
in pošljemo v frontend. Tu so tovarne in dovoljene vrednosti.
"""
from __future__ import annotations

import itertools
import time
import uuid

STANJA_PREDLOGA = ("predlagano", "sprejeto", "spremenjeno", "zavrnjeno", "zastarelo")
POTRJENA = ("sprejeto", "spremenjeno")

POLJA_AI = (
    "vrsta_dogodka", "opis", "naslov", "orientacija", "lokacija", "stevilo_poskodovanih",
    "stanje_pacienta", "prioriteta", "potrebna_oprema", "predlagana_enota", "kontakt",
    "vprasanja_za_klicatelja", "zaznani_dogodek_plana",
)
# polja, ki jih predlaga LLM neposredno iz prepisa (lokacija pride iz geokodiranja,
# predlagana_enota iz usmerjanja + planerja)
POLJA_LLM = (
    "vrsta_dogodka", "opis", "naslov", "orientacija", "stevilo_poskodovanih",
    "stanje_pacienta", "prioriteta", "potrebna_oprema", "kontakt",
)
VRSTE_DOGODKA = ("prometna_nesreca", "padec", "bolecina_v_prsih", "nezavest", "porod", "drugo")
TIPI_DOGODKOV = ("okvara_vozila", "megla", "nujna_naloga", "manjsa_zmogljivost_centra", "sprememba_prioritete",
                 # predlogi ekip (voznik/ekipa.py), dispečer jih potrdi
                 "ovira_na_cesti", "prevoz_z_nujne_voznje", "enota_ni_na_voljo")
VRSTE_OVIR = ("zapora", "nesreca", "dela_na_cesti", "poplava", "zastoj", "drugo")
TIPI_NALOG = ("prevoz_pacienta", "prevoz_opreme", "nujna_voznja")
TIPI_ENOT = ("vozilo", "motorist", "helikopter")
STANJA_ENOTE = ("prosto", "na_nalogi", "okvara", "ni_na_voljo")
NERAZPOLOZLJIVA = ("okvara", "ni_na_voljo")  # planer jih ne uporablja

_stevec = itertools.count(1)


def nov_id(predpona: str) -> str:
    return f"{predpona}-{uuid.uuid4().hex[:8]}"


def zdaj() -> float:
    return time.time()


def klic(stevilka: str | None = None) -> dict:
    return {"id": nov_id("k"), "zacetek": zdaj(), "konec": None, "stanje": "v_teku",
            "stevilka": stevilka, "segmenti": []}


def segment(klic_id: str, govorec: str, od_s: float, do_s: float, besedilo: str,
            koncni: bool = True, rocni: bool = False, seg_id: str | None = None) -> dict:
    assert govorec in ("klicatelj", "dispecer")
    return {"id": seg_id or f"s{next(_stevec)}", "klic_id": klic_id, "govorec": govorec,
            "od_s": float(od_s), "do_s": float(do_s), "besedilo": besedilo,
            "koncni": koncni, "rocni": rocni}


def predlog(klic_id: str | None, polje: str, vrednost, zaupanje: float | None, dokazi: list,
            verzija: int = 1, predlagal: str = "ai", nasprotuje_potrjenemu: bool = False,
            dodatno: dict | None = None) -> dict:
    assert polje in POLJA_AI, polje
    return {
        "id": nov_id("p"), "klic_id": klic_id, "polje": polje, "vrednost": vrednost,
        "zaupanje": zaupanje, "dokazi": list(dokazi), "stanje": "predlagano", "verzija": verzija,
        "predlagal": predlagal, "potrdil": None, "potrjeno_ob": None,
        "prvotna_ai_vrednost": vrednost if predlagal == "ai" else None,
        "nasprotuje_potrjenemu": nasprotuje_potrjenemu, "razlog": None,
        "ustvarjeno": zdaj(), "dodatno": dodatno or {},
    }


def dogodek(tip: str, parametri: dict, vir: str, stanje: str = "predlagano",
            dokazi: list | None = None, zaupanje: float | None = None) -> dict:
    assert tip in TIPI_DOGODKOV, tip
    assert vir in ("klic", "ekipa", "sistem", "dispecer")
    return {"id": nov_id("d"), "tip": tip, "parametri": dict(parametri), "vir": vir,
            "stanje": stanje, "dokazi": dokazi or [], "zaupanje": zaupanje, "ustvarjeno": zdaj()}


def naloga(tip: str, prevzem: dict, prioriteta: int, dostava: dict | None = None,
           okno_od: float | None = None, okno_do: float | None = None,
           pacienti: int = 0, oprema: int = 0, opis: str = "", naloga_id: str | None = None) -> dict:
    assert tip in TIPI_NALOG
    assert 1 <= int(prioriteta) <= 5
    return {"id": naloga_id or nov_id("N"), "tip": tip, "prevzem": prevzem, "dostava": dostava,
            "okno_od": okno_od, "okno_do": okno_do, "prioriteta": int(prioriteta),
            "kolicina": {"pacienti": pacienti, "oprema": oprema}, "opis": opis,
            "stanje": "odprta", "ustvarjeno": zdaj()}
