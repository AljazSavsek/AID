"""AI poslušalec z rezervo (specifikacija 5, 7).

Vrstni red: LLM v oblaku -> lokalni LLM (Ollama) -> lokalna pravila -> brez predlogov.
Neveljaven JSON = ponovi enkrat, nato preklopi na naslednjega.
Ime vira gre le v interni dnevnik; navzven vrnemo samo izhod.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

from . import pravila
from .shema import NAVODILA_POLJ, SHEMA, SISTEMSKI_PROMPT, NeveljavenIzhod, validiraj

dnevnik = logging.getLogger("poslusalec")


def sestavi_vhod(segmenti: list[dict], predlogi: list[dict], enote: list[dict], centri: list[dict]) -> str:
    prepis = "\n".join(f'[{s["id"]} {s["od_s"]:.1f}s {s["govorec"]}] {s["besedilo"]}' for s in segmenti)
    stanje = [{"polje": p["polje"], "vrednost": p["vrednost"], "stanje": p["stanje"]}
              for p in predlogi if p["stanje"] in ("predlagano", "sprejeto", "spremenjeno")]
    return (
        f"{NAVODILA_POLJ}\n\nPREPIS:\n{prepis}\n\n"
        f"TRENUTNI PREDLOGI (sprejeto/spremenjeno = potrdil dispečer, ne spreminjaj):\n"
        f"{json.dumps(stanje, ensure_ascii=False)}\n\n"
        f"ENOTE: {json.dumps([{'id': e['id'], 'tip': e['tip'], 'ime': e['ime']} for e in enote], ensure_ascii=False)}\n"
        f"CENTRI: {json.dumps([{'id': c['id'], 'ime': c['ime']} for c in centri], ensure_ascii=False)}"
    )


class OblakLLM:
    ime = "oblak"

    def __init__(self, model: str | None = None, rok_s: float = 8.0):
        self.model = model or os.environ.get("AID_LLM_MODEL", "claude-opus-5-5")
        self.rok_s = rok_s
        self._odjemalec = None

    def na_voljo(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    async def poklici(self, vhod: str) -> str:
        import anthropic
        if self._odjemalec is None:
            self._odjemalec = anthropic.AsyncAnthropic(timeout=self.rok_s, max_retries=0)
        odg = await self._odjemalec.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SISTEMSKI_PROMPT,
            output_config={"effort": os.environ.get("AID_LLM_EFFORT", "low"),
                           "format": {"type": "json_schema", "schema": SHEMA}},
            messages=[{"role": "user", "content": vhod}],
        )
        if odg.stop_reason == "refusal":
            raise NeveljavenIzhod("model je zavrnil zahtevo")
        if odg.stop_reason == "max_tokens":
            raise NeveljavenIzhod("izhod prekinjen")
        return next(b.text for b in odg.content if b.type == "text")


class OllamaLLM:
    ime = "lokalni_llm"

    def __init__(self, rok_s: float = 12.0):
        self.url = os.environ.get("OLLAMA_URL", "")
        self.model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.rok_s = rok_s

    def na_voljo(self) -> bool:
        return bool(self.url)

    async def poklici(self, vhod: str) -> str:
        async with httpx.AsyncClient(timeout=self.rok_s) as c:
            r = await c.post(f"{self.url.rstrip('/')}/api/chat", json={
                "model": self.model, "stream": False, "format": SHEMA, "options": {"temperature": 0},
                "messages": [{"role": "system", "content": SISTEMSKI_PROMPT}, {"role": "user", "content": vhod}],
            })
            r.raise_for_status()
            return r.json()["message"]["content"]


class Pravila:
    """Deterministična rezerva; namesto besedila prejme segmente neposredno."""
    ime = "pravila"

    def na_voljo(self) -> bool:
        return os.environ.get("AID_PRAVILA", "1") == "1"


class Poslusalec:
    def __init__(self, viri: list | None = None):
        self.viri = viri if viri is not None else [OblakLLM(), OllamaLLM(), Pravila()]
        self.zadnji_vir: str | None = None  # samo za interno zdravje
        self.simuliran_izpad = False         # demo: izpad vseh virov
        self.napake: dict[str, str] = {}     # zadnja napaka po viru (interno zdravje)
        self.rezerva = False                 # True, ko je nastavljen boljši vir odpovedal in dela rezerva

    async def obdelaj(self, segmenti: list[dict], predlogi: list[dict], enote: list[dict], centri: list[dict]) -> dict | None:
        """Vrne očiščen izhod ali None (brez predlogov -> ročni vnos)."""
        if not segmenti or self.simuliran_izpad:
            return None
        vhod = sestavi_vhod(segmenti, predlogi, enote, centri)
        odpovedali = []
        for vir in self.viri:
            if not vir.na_voljo():
                continue
            if isinstance(vir, Pravila):
                trenutni = {p["polje"]: p["vrednost"] for p in predlogi if p["stanje"] in ("predlagano", "sprejeto", "spremenjeno")}
                self.zadnji_vir = vir.ime
                self.rezerva = bool(odpovedali)
                return validiraj(pravila.izlusci(segmenti, trenutni), segmenti)
            for poskus in (1, 2):  # neveljaven JSON -> ponovi enkrat
                try:
                    besedilo = await vir.poklici(vhod)
                    izhod = validiraj(json.loads(besedilo), segmenti)
                    self.zadnji_vir = vir.ime
                    self.rezerva = bool(odpovedali)
                    self.napake.pop(vir.ime, None)
                    return izhod
                except (json.JSONDecodeError, NeveljavenIzhod) as e:
                    dnevnik.warning("%s: neveljaven izhod (poskus %d): %s", vir.ime, poskus, e)
                    self.napake[vir.ime] = f"neveljaven izhod: {e}"[:300]
                except (asyncio.TimeoutError, httpx.HTTPError) as e:
                    dnevnik.warning("%s: ni dosegljiv: %s", vir.ime, e)
                    self.napake[vir.ime] = f"ni dosegljiv: {e}"[:300]
                    break
                except Exception as e:  # noqa: BLE001 – napake SDK (napačna različica, ključ, model …) niso tihe
                    dnevnik.error("%s: napaka: %r", vir.ime, e)
                    self.napake[vir.ime] = f"napaka: {e!r}"[:300]
                    break
            odpovedali.append(vir.ime)
        self.zadnji_vir = None
        self.rezerva = bool(odpovedali)
        dnevnik.error("AI poslušalec ni na voljo – brez predlogov (ročni vnos)")
        return None
