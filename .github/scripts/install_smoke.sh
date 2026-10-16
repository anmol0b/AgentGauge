#!/bin/sh
# Install the built distributions into a fresh venv the way a user would and
# check the client finds the executor binary — without activating the venv,
# so the binary is not on PATH and discovery has to find it on its own.
#
# usage: install_smoke.sh <python> <dist dir>
set -eu
python=$1
dist=$2
venv=$(mktemp -d)/venv

"$python" -m venv "$venv"
# --find-links: the executor wheel may not be on PyPI yet, so it must come
# from what this run built. Everything else resolves from PyPI as usual.
"$venv/bin/pip" install --quiet --find-links "$dist" agentbench-ai

cd "$(dirname "$venv")"  # away from any checkout, whose rust/target would win
"$venv/bin/agentbench" --help > /dev/null
"$venv/bin/python" - <<'PY'
import subprocess
from agentbench.sandbox.discovery import find_executor_binary

binary = find_executor_binary()
assert binary is not None, "the executor wheel installed but discovery cannot find it"
out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=True)
print(f"{binary}: {out.stdout.strip()}")
PY
