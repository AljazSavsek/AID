#!/usr/bin/env bash
# Celotna aplikacija (Linux/macOS): ustvari .venv, namesti odvisnosti (tudi Whisper) in zažene zazeni.py.
# Dodatni argumenti gredo naprej, npr.: ./zazeni.sh --vozila
set -e
cd "$(dirname "$0")"
PY=$(command -v python3.12 || command -v python3.13 || command -v python3)
if [ ! -x .venv/bin/python ]; then
  echo "Ustvarjam .venv ($PY) …"
  "$PY" -m venv .venv || { echo "Namestite venv: sudo apt install python3-venv python3-full"; exit 1; }
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, ortools, anthropic, faster_whisper, av" 2>/dev/null; then
  echo "Nameščam knjižnice. Prvi zagon potrebuje internet."
  .venv/bin/python -m pip install -r requirements.txt -r requirements-zvok.txt
fi
exec .venv/bin/python zazeni.py "$@"
