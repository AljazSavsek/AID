import asyncio
import json
from pathlib import Path
from uuid import uuid4


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


async def prepis_klica(model, predvajaj):
    posnetki = sorted(Path(__file__).with_name("zvok").glob("*.wav"))
    if not posnetki:
        raise FileNotFoundError("Manjka mapa zvok s posnetki simulacije.")
    for posnetek in posnetki:
        oznaka, vloga = posnetek.stem.split("_")
        govorec = {"dispecer": "Dispečer", "klicatelj": "Klicatelj"}[vloga]
        _, besedilo = await asyncio.gather(
            asyncio.to_thread(predvajaj, posnetek),
            asyncio.to_thread(prepisi_odsek, model, posnetek),
        )
        if besedilo:
            yield oznaka, f"{govorec}: {besedilo}"
        else:
            print(f"Odsek {oznaka}: model ni razbral govora.", flush=True)


def predvajaj_windows(posnetek):
    import winsound
    winsound.PlaySound(str(posnetek), winsound.SND_FILENAME | winsound.SND_NODEFAULT)


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
    pot = await poslusaj(prepis_klica(model, predvajaj_windows), Konzola())
    print("Konec klica. Prepis je shranjen v", pot)


if __name__ == "__main__":
    asyncio.run(zagon())
