"""Prevodi: i18n/<jezik>.json, ključ -> besedilo z {imenovanimi} parametri. Manjkajoč ključ vrne ključ."""
from __future__ import annotations

import json
from pathlib import Path

MAPA = Path(__file__).resolve().parent.parent / "i18n"
_besedila: dict[str, str] = {}


def nalozi(jezik: str = "sl") -> None:
    global _besedila
    _besedila = json.loads((MAPA / f"{jezik}.json").read_text(encoding="utf-8"))


def t(kljuc: str, **kw) -> str:
    if not _besedila:
        nalozi()
    b = _besedila.get(kljuc, kljuc)
    try:
        return b.format(**kw) if kw else b
    except (KeyError, IndexError):
        return b


def razdalja(m: float | None) -> str:
    if m is None:
        return "–"
    return t("enota.m", v=int(round(m / 10) * 10)) if m < 1000 else t("enota.km", v=f"{m / 1000:.1f}".replace(".", ","))


def trajanje(s: float | None) -> str:
    if s is None:
        return "–"
    return t("enota.s", v=int(s)) if s < 60 else t("enota.min", v=int(round(s / 60)))
