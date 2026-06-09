#!/usr/bin/env bash
# dev_loopback.sh — one-command end-to-end test for ClassControl on macOS.
#
# Usage:
#   ./scripts/dev_loopback.sh           # client runs as you
#   ./scripts/dev_loopback.sh --sudo    # client runs as root (prompts for
#                                       # your password once); needed for
#                                       # Internet lockdown, URL blocking,
#                                       # and shutdown/restart to actually
#                                       # take effect during testing
#
# What it does, in order:
#   1. Creates .venv and installs requirements if missing.
#   2. Generates the client auth key if it doesn't exist yet.
#   3. Mirrors that key onto the master so they trust each other.
#   4. Adds Loopback (127.0.0.1:11400) to the master's roster if absent.
#   5. Kills anything already listening on port 11400 (e.g. a leftover
#      daemon from a previous run).
#   6. Starts the client daemon in the background.
#       - With --sudo it's launched via `sudo -E` so SUDO_USER is set,
#         and the client's config helper steers config paths back to
#         /Users/<you>/Library/...  (see shared/config.py).
#   7. Waits up to 3s for it to bind to TCP 11400.
#   8. Launches the teacher app in the foreground.
#   9. When you quit the teacher (⌘Q or the red dot), the client is
#      torn down automatically.
#
# Live client log (separate terminal):
#   tail -f "$HOME/Library/Application Support/ClassControl/client/client.log"

set -euo pipefail
cd "$(dirname "$0")/.."

USE_SUDO=0
for arg in "$@"; do
    case "$arg" in
        --sudo|--root) USE_SUDO=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

APP_DIR="$HOME/Library/Application Support/ClassControl"
CLIENT_KEY="$APP_DIR/client/auth.key"
MASTER_KEY="$APP_DIR/master/auth.key"
ROSTER="$APP_DIR/master/roster.json"
CLIENT_LOG="$APP_DIR/client/client.log"
PORT=11400

# ----------------------------------------------------------------------
# 1. venv + dependencies
# ----------------------------------------------------------------------
if [ ! -d .venv ]; then
    echo "→ Creating .venv and installing dependencies (one-time setup)..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip >/dev/null
    .venv/bin/pip install -r requirements.txt
fi
PY=".venv/bin/python"

# ----------------------------------------------------------------------
# 2. Make sure the client auth key exists
# ----------------------------------------------------------------------
if [ ! -f "$CLIENT_KEY" ]; then
    echo "→ Generating client auth key..."
    "$PY" -m client.daemon --print-key >/dev/null
fi

# ----------------------------------------------------------------------
# 3. Mirror it onto the master
# ----------------------------------------------------------------------
mkdir -p "$(dirname "$MASTER_KEY")"
cp "$CLIENT_KEY" "$MASTER_KEY"
chmod 600 "$MASTER_KEY"
echo "→ Auth key synced (client ↔ master)."

# ----------------------------------------------------------------------
# 4. Auto-add Loopback to the roster (idempotent)
# ----------------------------------------------------------------------
"$PY" - <<PYEOF
import json, uuid
from pathlib import Path
p = Path("$ROSTER")
p.parent.mkdir(parents=True, exist_ok=True)
data = {"computers": []}
if p.exists():
    try:
        data = json.loads(p.read_text())
    except Exception:
        pass
host, port = "127.0.0.1", $PORT
if not any(c.get("host") == host and c.get("port") == port
           for c in data.get("computers", [])):
    data.setdefault("computers", []).append({
        "id": uuid.uuid4().hex, "name": "Loopback",
        "host": host, "port": port,
        "group": "default", "mac": "",
        "notes": "added by dev_loopback.sh",
    })
    p.write_text(json.dumps(data, indent=2))
    print("→ Added Loopback ($host:$port) to the roster.")
else:
    print("→ Loopback already in the roster.")
PYEOF

# ----------------------------------------------------------------------
# 5. Free port 11400 if something's already on it
# ----------------------------------------------------------------------
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "→ Port $PORT is busy — terminating the existing holder..."
    PIDS=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN | awk 'NR>1 {print $2}')
    # Try without sudo first; fall back to sudo (covers a leftover
    # daemon from a previous --sudo run).
    echo "$PIDS" | xargs kill -TERM 2>/dev/null || true
    sleep 0.5
    if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
        echo "$PIDS" | xargs sudo kill -TERM 2>/dev/null || true
    fi
    sleep 1
fi

# ----------------------------------------------------------------------
# 6. Start the client daemon in the background
# ----------------------------------------------------------------------
if [ "$USE_SUDO" -eq 1 ]; then
    echo "→ Starting client daemon as ROOT (sudo). You may be prompted"
    echo "  for your password. Logs: $CLIENT_LOG"
    # `sudo -E` preserves SUDO_USER so shared/config.py can keep using
    # /Users/<you>/Library/... for auth.key / cert / blocking.json etc.
    sudo -E "$PY" -m client.daemon &
else
    echo "→ Starting client daemon (logs: $CLIENT_LOG)..."
    echo "  (Tip: re-run with --sudo to enable Internet lockdown,"
    echo "   URL blocking, and shutdown/restart.)"
    "$PY" -m client.daemon &
fi
CLIENT_PID=$!

# Always tear the client down when this script exits, however that happens.
cleanup() {
    if kill -0 "$CLIENT_PID" 2>/dev/null; then
        echo
        echo "→ Stopping client (pid $CLIENT_PID)..."
        # When the client was launched under sudo we need sudo to kill it.
        if [ "$USE_SUDO" -eq 1 ]; then
            sudo kill -TERM "$CLIENT_PID" 2>/dev/null || true
        else
            kill -TERM "$CLIENT_PID" 2>/dev/null || true
        fi
        wait "$CLIENT_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ----------------------------------------------------------------------
# 7. Wait up to 3s for the client to bind to TCP 11400
# ----------------------------------------------------------------------
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
        echo "→ Client is listening on TCP $PORT."
        break
    fi
    sleep 0.3
done

if ! lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "⚠  Client failed to bind within 3 seconds."
    echo "   Check $CLIENT_LOG for errors."
    echo "   Continuing — the master will surface a connect error."
fi

# ----------------------------------------------------------------------
# 8. Launch the teacher app in the foreground
# ----------------------------------------------------------------------
echo
echo "════════════════════════════════════════════════════════════════"
echo "  Teacher launching. Close it (⌘Q) to shut everything down."
echo "  Live client log in another terminal:"
echo "    tail -f \"$CLIENT_LOG\""
echo "════════════════════════════════════════════════════════════════"
echo
"$PY" -m master.app
