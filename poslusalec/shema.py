"""Shema izhoda AI poslušalca (specifikacija 5) in preverjanje.

Preverjanje je strogo: AI si ničesar ne sme izmisliti. Posodobitev brez
dobesednega citata iz prepisa se zavrže.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from dnevnik.model import POLJA_LLM, TIPI_DOGODKOV, VRSTE_DOGODKA

dnevnik = logging.getLogger("poslusalec")

SISTEMSKI_PROMPT = (
    "Si pomočnik dispečerja nujne medicinske pomoči v Sloveniji. Poslušaš prepis klica v slovenščini, "
    "ki lahko vsebuje napake prepoznave govora in narečje. Iz prepisa izlušči samo podatke, ki so v njem "
    "izrečeni. Ničesar ne domnevaj in ne dopolnjuj. Za vsak podatek navedi dobesedni citat iz prepisa in "
    "zaupanje med 0 in 1. Če podatek manjka, ga ne vračaj. Predlagaj kratka vprašanja, ki bi jih dispečer "
    "moral postaviti klicatelju, da dobi manjkajoče ključne podatke (lokacija, dihanje, zavest, število "
    "poškodovanih). Ne spreminjaj podatkov, ki jih je dispečer potrdil. Vrni izključno JSON po podani shemi."
)

_DOKAZ = {
    "type": "object",
    "properties": {"segment_id": {"type": "string"}, "citat": {"type": "string"}},
    "required": ["segment_id", "citat"],
    "additionalProperties": False,
}
_NULL_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULL_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

SHEMA = {
    "type": "object",
    "properties": {
        "posodobitve": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "polje": {"type": "string", "enum": list(POLJA_LLM)},
                    "vrednost": {"anyOf": [{"type": "string"}, {"type": "integer"},
                                           {"type": "array", "items": {"type": "string"}}]},
                    "zaupanje": {"type": "number"},
                    "dokazi": {"type": "array", "items": _DOKAZ},
                },
                "required": ["polje", "vrednost", "zaupanje", "dokazi"],
                "additionalProperties": False,
            },
        },
        "vprasanja_za_klicatelja": {"type": "array", "items": {"type": "string"}},
        "zaznani_dogodki": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tip": {"type": "string", "enum": list(TIPI_DOGODKOV)},
                    "enota_id": _NULL_STR,
                    "tovor_ostane": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                    "zamik_min": _NULL_INT,
                    "center_id": _NULL_STR,
                    "nova_kapaciteta": _NULL_INT,
                    "naloga_id": _NULL_STR,
                    "nova_prioriteta": _NULL_INT,
                    "zaupanje": {"type": "number"},
                    "dokazi": {"type": "array", "items": _DOKAZ},
                },
                "required": ["tip", "enota_id", "tovor_ostane", "zamik_min", "center_id", "nova_kapaciteta",
                             "naloga_id", "nova_prioriteta", "zaupanje", "dokazi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["posodobitve", "vprasanja_za_klicatelja", "zaznani_dogodki"],
    "additionalProperties": False,
}

NAVODILA_POLJ = (
    "Polja: vrsta_dogodka (eno od: " + ", ".join(VRSTE_DOGODKA) + "), opis (kratek opis), naslov "
    "(ulica, hišna številka, kraj, kot je izrečen), orientacija (opis okolice), stevilo_poskodovanih "
    "(celo število), stanje_pacienta, prioriteta (1–5, 1 = življenjsko ogrožen; samo z dokazom), "
    "potrebna_oprema (seznam), kontakt (telefonska številka). Če je kraj dvoumen, vrni več posodobitev "
    "naslova z nižjim zaupanjem. Dogodki plana (okvara_vozila, megla, nujna_naloga, "
    "manjsa_zmogljivost_centra, sprememba_prioritete) samo, če so izrečeni; neznani parametri so null."
)


class NeveljavenIzhod(ValueError):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s)).lower()
    s = re.sub(r"[„“”\"'«»!?.,;:()\-–—]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def citat_v_segmentu(citat: str, besedilo: str) -> bool:
    c = _norm(citat)
    return bool(c) and c in _norm(besedilo)


def _preveri_dokaze(dokazi, segmenti: dict[str, dict]) -> list[dict]:
    veljavni = []
    for d in dokazi or []:
        if not isinstance(d, dict):
            continue
        seg = segmenti.get(str(d.get("segment_id")))
        if seg and citat_v_segmentu(d.get("citat", ""), seg["besedilo"]):
            veljavni.append({"segment_id": seg["id"], "citat": str(d["citat"]).strip()})
        else:
            dnevnik.info("zavržen dokaz brez ujemanja v prepisu: %r", d)
    return veljavni


def _preveri_vrednost(polje: str, v):
    if polje == "vrsta_dogodka":
        return v if v in VRSTE_DOGODKA else None
    if polje in ("prioriteta", "stevilo_poskodovanih"):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        if polje == "prioriteta" and not 1 <= n <= 5:
            return None
        return n if n >= 0 else None
    if polje == "potrebna_oprema":
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",")]
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) and v else None
    if isinstance(v, (str, int)) and str(v).strip():
        return str(v).strip()
    return None


def validiraj(izhod, segmenti: list[dict]) -> dict:
    """Vrne očiščen izhod ali sproži NeveljavenIzhod (sprožilec za ponovitev / rezervo)."""
    if not isinstance(izhod, dict):
        raise NeveljavenIzhod("izhod ni objekt")
    for k in ("posodobitve", "vprasanja_za_klicatelja", "zaznani_dogodki"):
        if k not in izhod or not isinstance(izhod[k], list):
            raise NeveljavenIzhod(f"manjka ali ni seznam: {k}")
    po_id = {s["id"]: s for s in segmenti}
    cisto = {"posodobitve": [], "vprasanja_za_klicatelja": [], "zaznani_dogodki": []}
    for u in izhod["posodobitve"]:
        if not isinstance(u, dict) or u.get("polje") not in POLJA_LLM:
            continue
        try:
            z = float(u.get("zaupanje"))
        except (TypeError, ValueError):
            continue
        if not 0 <= z <= 1:
            continue
        v = _preveri_vrednost(u["polje"], u.get("vrednost"))
        dokazi = _preveri_dokaze(u.get("dokazi"), po_id)
        if v is None or not dokazi:
            dnevnik.info("zavržena posodobitev %s (vrednost ali dokaz neveljaven)", u.get("polje"))
            continue
        cisto["posodobitve"].append({"polje": u["polje"], "vrednost": v, "zaupanje": round(z, 2), "dokazi": dokazi,
                                     **({"alternativa": True} if u.get("alternativa") else {})})
    cisto["vprasanja_za_klicatelja"] = [str(q).strip() for q in izhod["vprasanja_za_klicatelja"]
                                        if isinstance(q, str) and q.strip()][:5]
    for d in izhod["zaznani_dogodki"]:
        if not isinstance(d, dict) or d.get("tip") not in TIPI_DOGODKOV:
            continue
        dokazi = _preveri_dokaze(d.get("dokazi"), po_id)
        if not dokazi:
            continue
        parametri = {k: d.get(k) for k in ("enota_id", "tovor_ostane", "zamik_min", "center_id",
                                           "nova_kapaciteta", "naloga_id", "nova_prioriteta")
                     if d.get(k) is not None}
        try:
            z = min(1.0, max(0.0, float(d.get("zaupanje", 0.5))))
        except (TypeError, ValueError):
            z = 0.5
        cisto["zaznani_dogodki"].append({"tip": d["tip"], "parametri": parametri, "zaupanje": round(z, 2), "dokazi": dokazi})
    return cisto
