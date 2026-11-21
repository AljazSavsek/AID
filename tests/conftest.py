import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AID_NOMINATIM"] = "0"          # testi ne smejo klicati interneta
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OLLAMA_URL", None)
for k in ("TOMTOM_API_KEY", "ORS_API_KEY", "OSRM_URL", "GURS_GEOKODER_URL"):
    os.environ.pop(k, None)

IMENA_PONUDNIKOV = ("tomtom", "openroute", "ors", "osrm", "google", "here", "nominatim", "mapbox")


def brez_ponudnikov(besedilo: str) -> list[str]:
    """Vrne najdena imena ponudnikov (kot cele besede) v besedilu odgovora."""
    import re
    low = besedilo.lower()
    return [i for i in IMENA_PONUDNIKOV if re.search(rf"(?<![a-zčšž]){i}(?![a-zčšž])", low)]
