#!/usr/bin/env bash
# Build both .app bundles for ClassControl on macOS.
set -euo pipefail

# Usage:
#     ./scripts/build_macos.sh
#
# Produces:
#     dist/ClassControl Teacher.app
#     dist/ClassControl Client.app

# ADD THIS LINE RIGHT HERE:
export CLT_PATH="/Library/Developer/CommandLineTools"

# Force Xcode paths for C extension compiling
export SDKROOT=$(xcrun --show-sdk-path)
export PATH="/usr/bin:/usr/local/bin:$PATH"

cd "$(dirname "$0")/.."

PY=.venv/bin/python

# (Re)create venv if missing OR if its python is gone (a partial create
# from a previous interrupted run is a common cause of "pip not found").
if [ ! -x "$PY" ]; then
    echo "Creating virtual environment in .venv/"
    rm -rf .venv
    /usr/local/bin/python3 -m venv .venv
fi

# Always invoke pip via `python -m pip` so we never depend on activate
# putting pip on PATH (which silently fails on some Python builds —
# that's the bug the previous version of this script tripped on).
#"$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
#"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt

rm -rf build dist
echo "Building teacher app…"
"$PY" packaging/setup_master.py py2app

# py2app overwrites dist/, so move and rebuild for the client
mv dist dist-teacher
echo "Building client app…"
"$PY" packaging/setup_client.py py2app
mv dist dist-client
mkdir -p dist
mv "dist-teacher/ClassControl Teacher.app" dist/
mv "dist-client/ClassControl Client.app" dist/
rm -rf dist-teacher dist-client build

echo
echo "Done. Built:"
ls -la dist
