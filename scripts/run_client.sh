#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    rm -rf .venv
    python3 -m venv .venv
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
fi
exec "$PY" -m client.daemon "$@"
