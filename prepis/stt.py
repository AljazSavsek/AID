"""Prepis govora z rezervo (specifikacija 7).

Primarno: streaming STT v oblaku (adapter; nastavi STT_WS_URL + STT_API_KEY).
Rezerva 1: lokalni faster-whisper z vgrajenim Silero VAD (vad_filter=True).
Zadnja rezerva: dispečer tipka ročno (POST /klic/{id}/segment z `rocni: true`).

V tem prototipu klic prihaja kot besedilo (demo) – ta modul se uporabi,
ko je na voljo zvok (WAV ali VoIP, korak 6 v vrstnem redu izdelave).
Zvok ostane na strežniku; v frontend gre samo besedilo (pravilo 2.7).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

dnevnik = logging.getLogger("prepis")


@dataclass
class DelPrepisa:
    od_s: float
    do_s: float
    besedilo: str
    koncni: bool = True


class NiNaVoljo(Exception):
    pass


class OblakSTT:
    ime = "oblak"

    def na_voljo(self) -> bool:
        return bool(os.environ.get("STT_WS_URL") and os.environ.get("STT_API_KEY"))

    def prepisi_datoteko(self, pot: str) -> Iterable[DelPrepisa]:
        # Adapter za izbranega ponudnika se doda, ko bo izbran; brez nastavitev ni na voljo.
        raise NiNaVoljo("oblačni STT ni nastavljen")


class LokalniWhisper:
    ime = "lokalni"

    def __init__(self, model: str | None = None):
        self.model_ime = model or os.environ.get("AID_WHISPER_MODEL", "small")
        self._model = None

    def na_voljo(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def prepisi_datoteko(self, pot: str) -> Iterable[DelPrepisa]:
        if not self.na_voljo():
            raise NiNaVoljo("faster-whisper ni nameščen")
        from faster_whisper import WhisperModel
        if self._model is None:
            self._model = WhisperModel(self.model_ime, device="cpu", compute_type="int8")
        segmenti, _ = self._model.transcribe(pot, language="sl", vad_filter=True)
        for s in segmenti:
            yield DelPrepisa(s.start, s.end, s.text.strip())


class Prepis:
    def __init__(self, viri=None):
        self.viri = viri or [OblakSTT(), LokalniWhisper()]

    def prepisi_datoteko(self, pot: str) -> tuple[list[DelPrepisa], bool]:
        """Vrne (deli, uspeh). Ob neuspehu je prazen seznam -> vmesnik pokaže ročni zapis."""
        for v in self.viri:
            if not v.na_voljo():
                continue
            try:
                return list(v.prepisi_datoteko(pot)), True
            except Exception as e:  # noqa: BLE001
                dnevnik.warning("STT %s napaka: %s", v.ime, e)
        return [], False
