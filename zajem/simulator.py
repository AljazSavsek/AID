"""Simulator klica (specifikacija 12): po vrsti pošilja segmente iz demo/klic1.json
z realnimi zamiki. Deluje kot zunanji odjemalec API-ja (namesto VoIP + STT).

    python -m zajem.simulator --url http://localhost:8000 --hitrost 1

Podpira tudi WAV: `--wav posnetek.wav` (prepis z rezervo iz modula prepis/).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

KOREN = Path(__file__).resolve().parent.parent


def nalozi_klic(pot: str | Path = KOREN / "demo" / "klic1.json") -> dict:
    return json.loads(Path(pot).read_text(encoding="utf-8"))


async def predvajaj(segmenti: list[dict], poslji, hitrost: float = 1.0, prekini=None) -> None:
    """Skupna zanka: uporablja jo CLI in strežnik (/demo/klic)."""
    prej = 0.0
    for seg in segmenti:
        zamik = max(0.2, seg["od_s"] - prej) / hitrost + (seg["do_s"] - seg["od_s"]) / hitrost
        await asyncio.sleep(zamik)
        if prekini and prekini():
            return
        await poslji(seg)
        prej = seg["do_s"]


async def _main(url: str, hitrost: float, pot: str, wav: str | None, ime: str, geslo: str) -> None:
    async with httpx.AsyncClient(base_url=url, timeout=10) as c:
        r = await c.post("/dispecer/prijava", json={"ime": ime, "geslo": geslo})  # piškotek seje ostane v odjemalcu
        if r.status_code != 200:
            raise SystemExit(f"Prijava dispečerja ni uspela ({r.status_code}): podajte --ime in --geslo.")
        klic = (await c.post("/klic/zacni", json={"stevilka": "+386 41 555 238"})).json()
        print("klic", klic["id"])
        if wav:
            from prepis.stt import Prepis
            deli, ok = Prepis().prepisi_datoteko(wav)
            if not ok:
                print("prepis ni na voljo – dispečer naj tipka ročno")
                return
            segmenti = [{"govorec": "klicatelj", "od_s": d.od_s, "do_s": d.do_s, "besedilo": d.besedilo} for d in deli]
        else:
            segmenti = nalozi_klic(pot)["segmenti"]

        async def poslji(seg):
            await c.post(f"/klic/{klic['id']}/segment", json=seg)
            print(f"[{seg['od_s']:5.1f}] {seg['govorec']}: {seg['besedilo']}")

        await predvajaj(segmenti, poslji, hitrost)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--hitrost", type=float, default=1.0)
    ap.add_argument("--klic", default=str(KOREN / "demo" / "klic1.json"))
    ap.add_argument("--wav")
    ap.add_argument("--ime", default="M. Novak", help="dispečer (demo račun iz scenarija)")
    ap.add_argument("--geslo", default="DEMO-DISP")
    a = ap.parse_args()
    asyncio.run(_main(a.url, a.hitrost, a.klic, a.wav, a.ime, a.geslo))
