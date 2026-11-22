#!/usr/bin/env bash
set -eu
cd "$1"
python3 -m compileall -q .
python3 -m unittest discover -v
