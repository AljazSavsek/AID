"""Diagnoza prepisa govora (Whisper). Zaženite v mapi projekta:  python diagnoza_zvok.py
Izpis pošljite razvijalcu. Ključev in podatkov pacientov ne izpisuje."""
import importlib.util
import os
import platform
import sys
import time
import traceback
from pathlib import Path

KOREN = Path(__file__).resolve().parent
sys.path.insert(0, str(KOREN))
ok = True


def korak(ime, fn):
    global ok
    print(f"\n== {ime}", flush=True)
    t = time.time()
    try:
        r = fn()
        print(f"   OK ({time.time() - t:.1f} s){': ' + str(r) if r is not None else ''}", flush=True)
        return r
    except BaseException as e:  # noqa: BLE001
        ok = False
        print(f"   ✖ {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return None


print("Python:", sys.version.split()[0], sys.executable)
print("Sistem:", platform.platform(), platform.machine())
print("venv:", "da" if sys.prefix != sys.base_prefix else "NE – najprej: source .venv/bin/activate")
print("Mapa:", KOREN)
for k in ("AID_WHISPER_MODEL", "AID_ZVOK_REZERVA", "AID_WHISPER_NITI", "HF_HOME", "HF_HUB_OFFLINE", "HTTPS_PROXY"):
    if os.environ.get(k):
        print(f"{k}={os.environ[k]}")

for paket in ("fastapi", "uvicorn", "ortools", "anthropic", "faster_whisper", "ctranslate2", "av", "huggingface_hub", "tokenizers"):
    spec = importlib.util.find_spec(paket)
    ver = ""
    if spec:
        try:
            ver = getattr(__import__(paket), "__version__", "")
        except Exception as e:  # noqa: BLE001
            ver = f"UVOZ NE USPE: {type(e).__name__}: {e}"
            ok = False
    print(f"  {paket:16} {'✔ ' + str(ver) if spec else '✖ ni nameščen'}")

from poslusalec import poslusalec as pp  # noqa: E402

posnetki = korak("Posnetki poslusalec/zvok", lambda: len(pp.posnetki_klica()))
korak("Povezava do huggingface.co", lambda: __import__("httpx").get("https://huggingface.co/api/models/Systran/faster-whisper-small", timeout=10).status_code)
ime = os.environ.get("AID_WHISPER_MODEL", "small")
model = korak(f"Nalaganje modela Whisper '{ime}' (prvič prenos ~500 MB)", lambda: pp.nalozi_model() and "naložen")
if model:
    p = pp.posnetki_klica()[1]
    korak(f"Prepis {p.name}", lambda: repr(pp.prepisi_odsek(pp.nalozi_model(), p)))
    import asyncio

    async def poln():
        from api.storitev import Center
        from zajem.zvok import predvajaj_zvocni_klic
        c = Center()
        k = c.zacni_klic("diagnoza")
        return f"{await predvajaj_zvocni_klic(c, k['id'], hitrost=100)} odsekov"
    korak("Celoten zvočni klic skozi aplikacijo", lambda: asyncio.run(poln()))

print("\n" + ("VSE DELUJE" if ok else "NAPAKA – pošljite celoten izpis zgoraj"))
