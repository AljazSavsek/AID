"""Zagon demonstracije iz referenčnega besedila namesto zvočnih posnetkov."""
import asyncio
from pathlib import Path
from poslusalec import Konzola, poslusaj, prikazi_in_dopolni


async def replike():
    for i, line in enumerate(Path(__file__).with_name("scenarij.txt").read_text(encoding="utf-8").splitlines()):
        yield i, line
        await asyncio.sleep(0.15)


async def main():
    print("Prikaz iz REFERENČNEGA BESEDILA simulacije")
    prikazi_in_dopolni(await poslusaj(replike(), Konzola()))


if __name__ == "__main__":
    asyncio.run(main())
