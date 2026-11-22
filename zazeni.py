"""AID – zažene vse naenkrat: strežnik, zaslon dispečerja, zaslon voznika in Flet aplikacijo voznika.

    python zazeni.py                 # vse (Flet aplikacija, če je nameščena)
    python zazeni.py --brez-flet     # samo strežnik + oba spletna zaslona
    python zazeni.py --enota RV-3    # zaslon voznika za drugo vozilo
    python zazeni.py --vozila RV-1,RV-4   # + simulator vozil (vozita po potrjeni poti, brez telefona)

Ustavi se s Ctrl+C (ustavi tudi vse podrejene procese).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

KOREN = Path(__file__).resolve().parent


def caka_strežnik(url: str, rok_s: float = 90) -> bool:
    konec = time.time() + rok_s
    while time.time() < konec:
        try:
            with urllib.request.urlopen(url + "/voznik/enote-za-prijavo", timeout=2) as r:  # javna pot (ostalo zahteva prijavo)
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def ima_zaslon() -> bool:
    return platform.system() in ("Windows", "Darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def skrivnosti() -> dict:
    """Skrivnosti za podpis žetonov (voznik, dispečer): iz okolja/.env, sicer ustvarjene enkrat in shranjene
    v podatki/.skrivnosti.json (ni v gitu) – prijave tako preživijo ponovni zagon."""
    import secrets
    pot = KOREN / "podatki" / ".skrivnosti.json"
    env_datoteka = {}
    if (KOREN / ".env").exists():
        for v in (KOREN / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in v and not v.strip().startswith("#"):
                k, x = v.split("=", 1)
                env_datoteka[k.strip()] = x.strip()
    shranjeno = {}
    try:
        shranjeno = json.loads(pot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = {}
    for k in ("AID_VOZNIK_SKRIVNOST", "AID_DISPECER_SKRIVNOST"):
        out[k] = os.environ.get(k) or env_datoteka.get(k) or shranjeno.get(k) or secrets.token_hex(32)
    if out != {k: shranjeno.get(k) for k in out}:
        pot.parent.mkdir(parents=True, exist_ok=True)
        pot.write_text(json.dumps(out), encoding="utf-8")
        try:
            os.chmod(pot, 0o600)
        except OSError:
            pass
    return out


def demo_dispecerji() -> list:
    try:
        return json.loads((KOREN / "demo" / "scenarij.json").read_text(encoding="utf-8")).get("dispecerji", [])
    except Exception:  # noqa: BLE001
        return []


def demo_kode() -> dict:
    try:
        return json.loads((KOREN / "demo" / "scenarij.json").read_text(encoding="utf-8")).get("kode_voznikov", {})
    except Exception:  # noqa: BLE001
        return {}


def stanje_prepisa() -> str:
    """Ali bo zvočni klic tekel z Whisperjem (isti Python kot strežnik: sys.executable)."""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        return (f"Prepis govora: ✖ faster-whisper ni nameščen v {sys.executable}\n"
                "    -> source .venv/bin/activate && pip install -r requirements-zvok.txt (do takrat: brez prepisa, le ročni zapis)")
    ime = os.environ.get("AID_WHISPER_MODEL", "small")
    hf = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    if any(hf.glob(f"models--*faster-whisper-{ime}")):
        return f"Prepis govora: Whisper ({ime}), model je že prenesen"
    return f"Prepis govora: Whisper ({ime}) – ob prvem zvočnem klicu se model prenese s huggingface.co (potreben internet)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--enota", default="RV-2", help="vozilo za spletni zaslon voznika")
    ap.add_argument("--brez-flet", action="store_true", help="ne zaženi Flet aplikacije voznika")
    ap.add_argument("--flet-port", type=int, default=8550, help="vrata Flet aplikacije v brskalniku (brez namiznega okna)")
    ap.add_argument("--brez-brskalnika", action="store_true", help="ne odpiraj zaslonov v brskalniku")
    ap.add_argument("--javno", action="store_true", help="poslušaj na vseh vmesnikih (telefon v istem omrežju)")
    ap.add_argument("--vozila", default="", help="simulirana vozila, npr. RV-1,RV-4 (zajem/vozilo_sim.py)")
    ap.add_argument("--pospesek", type=float, default=3.0, help="pospešek simulatorja vozil")
    ap.add_argument("--ponastavi", action="store_true",
                    help="začni znova iz demo/scenarij.json (izbriše podatki/aid.sqlite; heartbeati ostanejo)")
    a = ap.parse_args()
    if a.ponastavi:
        baza = Path(os.environ.get("AID_DB") or KOREN / "podatki" / "aid.sqlite")
        for f in (baza, baza.with_name(baza.name + "-wal"), baza.with_name(baza.name + "-shm")):
            f.unlink(missing_ok=True)
        print(f"↺ Stanje ponastavljeno – nova izmena iz demo/scenarij.json ({baza.name} izbrisana).")

    host = "0.0.0.0" if a.javno else "127.0.0.1"
    url = f"http://localhost:{a.port}"
    procesi: list[subprocess.Popen] = []

    def ustavi(*_):
        for p in reversed(procesi):
            if p.poll() is None:
                p.terminate()
        for p in procesi:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, ustavi)
    signal.signal(signal.SIGTERM, ustavi)

    print(f"▶ Zaganjam strežnik na {url} …")
    procesi.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--host", host, "--port", str(a.port)],
                                    cwd=KOREN, env={**os.environ, **skrivnosti()}))
    if not caka_strežnik(url):
        print("✖ Strežnik se ni zagnal – preverite izpis zgoraj.")
        ustavi()
        return 1

    dispecer = f"{url}/"
    voznik = f"{url}/voznik.html?enota={a.enota}"
    flet_url = None
    if not a.brez_flet and importlib.util.find_spec("flet"):
        env = {**os.environ, "AID_STREZNIK": url, "AID_VOZNIK_SIMULACIJA": "1",
               "AID_VOZNIK_PODATKI": str(KOREN / "podatki" / "aplikacija_voznik")}
        ukaz = [sys.executable, "main.py"]
        if not ima_zaslon():
            ukaz += ["--splet", "--port", str(a.flet_port)]
            flet_url = f"http://localhost:{a.flet_port}"
        print("▶ Zaganjam Flet aplikacijo voznika (simuliran GPS) …")
        procesi.append(subprocess.Popen(ukaz, cwd=KOREN / "aplikacija_voznik", env=env))
    elif not a.brez_flet:
        print("ℹ Flet ni nameščen – aplikacijo voznika namestite s: pip install -r aplikacija_voznik/requirements-razvoj.txt")

    sim = [e.strip().upper() for e in a.vozila.split(",") if e.strip()]
    if a.enota.upper() in sim:
        print(f"ℹ {a.enota} vozi simulator – za spletni zaslon voznika izberite drugo enoto (--enota).")
    if sim:
        print("▶ Zaganjam simulator vozil: " + ", ".join(sim))
        procesi.append(subprocess.Popen([sys.executable, "-m", "zajem.vozilo_sim", "--url", url, "--enote", ",".join(sim),
                                         "--pospesek", str(a.pospesek)], cwd=KOREN))

    kode = demo_kode()
    print("\n" + "═" * 64)
    print("  AID teče. Odprite:")
    print(f"  • Regijski center (dispečer):  {dispecer}")
    print(f"  • Zaslon voznika (splet):      {voznik}")
    print(f"  • Opozorilo za javnost:        {url}/javnost.html  (klik na karto = vaša lokacija)")
    if flet_url:
        print(f"  • Aplikacija voznika (Flet):   {flet_url}")
    elif len(procesi) > 1:
        print("  • Aplikacija voznika (Flet):   v ločenem oknu")
    if sim:
        print("  • Simulirana vozila:          " + ", ".join(sim) + " (ena enota = ena naprava: ne prijavljajte jih drugje)")
    if kode:
        print("  Demo kode vozil: " + ", ".join(f"{k}={v}" for k, v in kode.items()))
    disp = demo_dispecerji() if os.environ.get("AID_DISPECER_DEMO", "1") == "1" else []
    if disp:
        print("  Prijava dispečerja: " + ", ".join(f"{d['ime']} / {d['geslo']}" for d in disp))
    print("  " + stanje_prepisa())
    print("  Ustavite s Ctrl+C.")
    print("═" * 64 + "\n")
    if not a.brez_brskalnika:
        webbrowser.open(dispecer)
        time.sleep(0.8)
        webbrowser.open(voznik)
        if flet_url:
            time.sleep(4)
            webbrowser.open(flet_url)

    try:
        while True:
            for p in procesi:
                if p.poll() is not None and p is procesi[0]:
                    print("✖ Strežnik se je ustavil.")
                    ustavi()
            time.sleep(1)
    except KeyboardInterrupt:
        ustavi()
    return 0


if __name__ == "__main__":
    sys.exit(main())
