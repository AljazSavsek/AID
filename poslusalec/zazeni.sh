#!/usr/bin/env bash
# Samostojni poslušalec (Linux/macOS) – enako kot ZAZENI.bat: .venv, faster-whisper, prepis posnetkov iz zvok/.
set -e
cd "$(dirname "$0")"
PY=$(command -v python3.12 || command -v python3)
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || { echo "Namestite venv: sudo apt install python3-venv python3-full"; exit 1; }
fi
if ! .venv/bin/python -c "from faster_whisper import WhisperModel; import av; assert av.__version__ == '16.1.0'" 2>/dev/null; then
  echo "Nameščam knjižnice. Prvi zagon potrebuje internet."
  .venv/bin/python -m pip install faster-whisper==1.2.1 av==16.1.0
fi
exec .venv/bin/python poslusalec.py
