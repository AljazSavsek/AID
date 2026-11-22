#!/usr/bin/env bash
set -eu
build=$(realpath "$1")
tests=$(realpath "$2")
cd "$build"
PYTHONPATH="$build" python3 -m unittest discover -s "$tests" -v
