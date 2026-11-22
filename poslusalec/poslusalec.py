"""Prepis govora klica (faster-whisper) – samostojno (ZAZENI.bat) ali kot del aplikacije AID.

Samostojno: predvaja posnetke iz mape zvok/ in sproti izpisuje ter shranjuje prepis (prepisi/).
V aplikaciji: `odseki_klica` daje odseke (govorec, besedilo, trajanje); strežnik jih doda v klic
(zajem/zvok.py), kjer iz njih AI poslušalec (poslusalec/ai.py) pripravi predloge za dispečerja.
Zvok ostane na strežniku; v vmesnik gre samo besedilo (specifikacija 2.7).
"""
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
from uuid import uuid4

MAPA = Path(__file__).resolve().parent
GOVORCI = {"dispecer": "Dispečer", "klicatelj": "Klicatelj"}


class Prepis:
    def __init__(self, folder):
        folder.mkdir(exist_ok=True)
        self.path = folder / f"klic_{uuid4().hex}.json"
        self.parts = {}
        self.version = 0
        self.changed = asyncio.Condition()
        self.closed = False
        self.payload = None

    async def save(self, text):
        payload = json.dumps({"text": text}, ensure_ascii=False)
        async with self.changed:
            if self.payload == payload:
                return
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
            self.payload = payload
            self.version += 1
            self.changed.notify_all()

    async def close(self):
        async with self.changed:
            self.closed = True
            self.changed.notify_all()

    async def stream(self, socket):
        version = 0
        while True:
            async with self.changed:
                await self.changed.wait_for(lambda: self.version != version or self.closed)
                if version == self.version:
                    break
                version, payload = self.version, self.payload
            await socket.send_str(payload)

    async def segment(self, oznaka, besedilo):
        """Model poda celotno trenutno besedilo odseka. Popravek ima isto oznako."""
        if self.closed:
            raise RuntimeError("Klic je zaključen.")
        self.parts[oznaka] = besedilo.strip()
        await self.save(" ".join(delcek for delcek in self.parts.values() if delcek))


async def poslusaj(dogodki, ui, mapa=None):
    """Za en klic sprejema pare (oznaka, besedilo) in odprto povezavo UI."""
    klic = Prepis(Path(mapa) if mapa is not None else Path(__file__).with_name("prepisi"))
    await klic.save("")
    prenos = asyncio.create_task(klic.stream(ui))
    try:
        async for oznaka, besedilo in dogodki:
            await klic.segment(oznaka, besedilo)
    finally:
        await klic.close()
        await prenos
    return klic.path


def prepisi_odsek(model, posnetek):
    odseki, _ = model.transcribe(str(posnetek), language="sl", beam_size=3,
                                 condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in odseki if s.text.strip())


def posnetki_klica(mapa=None):
    posnetki = sorted(Path(mapa or MAPA / "zvok").glob("*.wav"))
    if not posnetki:
        raise FileNotFoundError("Manjka mapa zvok s posnetki simulacije.")
    return posnetki


def trajanje_s(posnetek):
    with wave.open(str(posnetek)) as w:
        return w.getnframes() / float(w.getframerate())


async def odseki_klica(model, predvajaj=None, mapa=None):
    """Za vsak posnetek (NN_vloga.wav) hkrati predvaja in prepiše; vrne slovar z vlogo govorca.
    `model` je WhisperModel ali karkoli z metodo transcribe (npr. rezerva iz shranjenega prepisa)."""
    for posnetek in posnetki_klica(mapa):
        oznaka, vloga = posnetek.stem.split("_", 1)
        if vloga not in GOVORCI:
            continue
        opravila = [asyncio.to_thread(prepisi_odsek, model, posnetek)]
        if predvajaj:
            opravila.append(asyncio.to_thread(predvajaj, posnetek))
        besedilo = (await asyncio.gather(*opravila))[0]
        yield {"oznaka": oznaka, "vloga": vloga, "govorec": GOVORCI[vloga], "besedilo": besedilo,
               "trajanje_s": trajanje_s(posnetek), "posnetek": posnetek.name}


async def prepis_klica(model, predvajaj):
    async for o in odseki_klica(model, predvajaj):
        if o["besedilo"]:
            yield o["oznaka"], f"{o['govorec']}: {o['besedilo']}"
        else:
            print(f"Odsek {o['oznaka']}: model ni razbral govora.", flush=True)


def predvajaj_windows(posnetek):
    import winsound
    winsound.PlaySound(str(posnetek), winsound.SND_FILENAME | winsound.SND_NODEFAULT)


def predvajaj_lokalno(posnetek):
    """Predvajanje na tem računalniku (Windows, macOS, Linux); brez predvajalnika počaka dolžino posnetka."""
    sistem = platform.system()
    if sistem == "Windows":
        return predvajaj_windows(posnetek)
    for ukaz in (["afplay"] if sistem == "Darwin" else []) + [["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]:
        ukaz = ukaz if isinstance(ukaz, list) else [ukaz]
        if shutil.which(ukaz[0]):
            subprocess.run(ukaz + [str(posnetek)], check=False)
            return
    time.sleep(trajanje_s(posnetek))


_modeli = {}


def nalozi_model(ime=None):
    """WhisperModel (enkrat na proces). Ime: AID_WHISPER_MODEL (privzeto small)."""
    ime = ime or os.environ.get("AID_WHISPER_MODEL", "small")
    if ime not in _modeli:
        from faster_whisper import WhisperModel
        _modeli[ime] = WhisperModel(ime, device="cpu", compute_type="int8",
                                    cpu_threads=int(os.environ.get("AID_WHISPER_NITI", 4)))
    return _modeli[ime]


class Konzola:
    def __init__(self):
        self.prejsnji = ""

    async def send_str(self, payload):
        prepis = json.loads(payload)["text"]
        nov_del = prepis.removeprefix(self.prejsnji).strip()
        if nov_del:
            print(nov_del, flush=True)
        self.prejsnji = prepis


async def zagon():
    from faster_whisper import WhisperModel
    print("Nalagam Whisper small. Ob prvem zagonu se model prenese. Prosim, počakaj.", flush=True)
    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
    print("Začetek simuliranega klica.", flush=True)
    pot = await poslusaj(prepis_klica(model, predvajaj_lokalno), Konzola())
    print("Konec klica. Prepis je shranjen v", pot)


if __name__ == "__main__":
    asyncio.run(zagon())
