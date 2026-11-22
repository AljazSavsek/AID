import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from kartica import build_card, LABELS


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
        payload = json.dumps({"text": text, "kartica": build_card(text)}, ensure_ascii=False)
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


# Besedišče klicev 112, ki Whisperju pove, katere besede pričakovati.
BESEDISCE = ("Center za obveščanje. Kaj se je zgodilo? Prometna nesreča, trčil, parkiran avto, "
             "kombi, požar, poškodovana oseba, pri zavesti, nezavesten, diha, krvavi, boli. "
             "Naslov je ulica, hišna številka. Vhod je pri zapornici, nasproti avtobusnega postajališča. "
             "Koordinate, severno, vzhodno, vejica. Telefonska številka, povratni klic. "
             "Ljubljana, Maribor, Celje, Kranj, Koper, Novo mesto, Zagorje ob Savi.")
MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")


def prepisi_odsek(model, posnetek):
    odseki, _ = model.transcribe(str(posnetek), language="sl", beam_size=5,
                                 condition_on_previous_text=False,
                                 initial_prompt=BESEDISCE, vad_filter=True,
                                 temperature=0.0)
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
    print(f"Nalagam Whisper {MODEL}. Ob prvem zagonu se model prenese. Prosim, počakaj.", flush=True)
    try:
        model = WhisperModel(MODEL, device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    print("Začetek simuliranega klica.", flush=True)
    pot = await poslusaj(prepis_klica(model, predvajaj_windows), Konzola())
    prikazi_in_dopolni(pot)


def prikazi_in_dopolni(pot):
    podatki = json.loads(pot.read_text(encoding="utf-8"))
    print("\nKartica dogodka")
    for key, label in LABELS.items():
        polje = podatki["kartica"][key]
        print(f"{label}: {polje['vrednost'] if polje['vrednost'] is not None else polje['status']}")
    print("Prepis in kartica:", pot)
    for key in LABELS:
        polje = podatki["kartica"][key]
        if polje["vrednost"] is not None and polje["status"] != "preveri prepis":
            continue
        try:
            vnos = input(f"{LABELS[key]} (ročni vnos, Enter preskoči): ").strip()
        except EOFError:
            break
        if vnos:
            if key == "stevilo_poskodovanih":
                if not vnos.isdecimal():
                    print("Število ni shranjeno.")
                    continue
                vnos = int(vnos)
            polje.update(vrednost=vnos, status="ročno vneseno", dokaz="")
            tmp = pot.with_suffix(".tmp")
            tmp.write_text(json.dumps(podatki, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(pot)



if __name__ == "__main__":
    asyncio.run(zagon())
