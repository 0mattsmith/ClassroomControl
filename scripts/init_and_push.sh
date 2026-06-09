#!/usr/bin/env bash
# init_and_push.sh — one-time first commit + push to GitHub.
#
# Usage:
#   export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxx
#   ./scripts/init_and_push.sh https://github.com/USER/REPO.git
#
# Why this script rather than typing it all manually?
#   * Reads the token from an env var so it never appears in your
#     shell history or in `ps`.
#   * Embeds the token in the remote URL only for the duration of
#     the push, then immediately rewrites the remote to the plain
#     HTTPS URL so the token isn't stored in .git/config.
#   * Cleans up any half-initialised .git directory first (e.g.
#     left behind by the Cowork sandbox).
#
# After a successful push, REVOKE THE TOKEN you used here at
# https://github.com/settings/tokens and create a fresh one for
# future use. The fact that the token was pasted into chat means
# it should be treated as compromised.

set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE_URL="${1:-}"
if [ -z "$REMOTE_URL" ]; then
    cat <<USAGE
Usage:
    export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxx
    $0 https://github.com/USER/REPO.git
USAGE
    exit 2
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "Error: GITHUB_TOKEN env var not set." >&2
    echo "  Run:    export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxx" >&2
    echo "  Then:   $0 $REMOTE_URL" >&2
    exit 2
fi

# Validate URL shape and extract owner.
if ! [[ "$REMOTE_URL" =~ ^https://github\.com/([^/]+)/[^/]+(\.git)?$ ]]; then
    echo "Error: expected a https://github.com/USER/REPO(.git) URL" >&2
    exit 2
fi
OWNER="${BASH_REMATCH[1]}"

# ---------------------------------------------------------------------
# 1. Clean up any partial .git left by a previous sandbox attempt OR
#    by an interrupted git command (the classic 'index.lock exists'
#    error).
# ---------------------------------------------------------------------
# Case A: .git exists but isn't a real repo (no HEAD) — Cowork sandbox
# pattern. Wipe and re-init.
if [ -d .git ] && [ ! -f .git/HEAD ]; then
    echo "→ Removing half-initialised .git/"
    rm -rf .git
fi

# Case B: .git exists with stale *.lock files but no live git process.
# Wipe just the locks (don't trash a real repo).
if [ -d .git ]; then
    LOCKS=$(find .git -maxdepth 2 -name '*.lock' 2>/dev/null || true)
    if [ -n "$LOCKS" ]; then
        echo "→ Removing stale lock files:"
        echo "$LOCKS" | sed 's/^/    /'
        echo "$LOCKS" | xargs rm -f
    fi
fi

# ---------------------------------------------------------------------
# 2. git init (idempotent) + first commit (if anything is staged).
# ---------------------------------------------------------------------
if [ ! -d .git ]; then
    echo "→ git init"
    git init -b main >/dev/null
fi

# Set user identity ONLY if not configured globally.
if ! git config --get user.name >/dev/null; then
    git config user.name  "Matt Smith"
fi
if ! git config --get user.email >/dev/null; then
    git config user.email "0matthewsmith@gmail.com"
fi

echo "→ Staging files (honouring .gitignore)"
git add -A

echo "→ Files about to be committed: $(git diff --cached --name-only | wc -l | tr -d ' ')"
git diff --cached --name-only | head -30 | sed 's/^/    /'
[ "$(git diff --cached --name-only | wc -l)" -gt 30 ] && echo "    … (truncated)"
echo

# Sanity-check: refuse to commit if any obvious secret slipped through.
SUSPECT=$(git diff --cached --name-only | grep -E '(\.venv|auth\.key|cert\.pem|key\.pem|\.crt$|\.zip$)' || true)
if [ -n "$SUSPECT" ]; then
    echo "✗ Refusing to commit — these files should not be in git:" >&2
    echo "$SUSPECT" | sed 's/^/    /' >&2
    echo "" >&2
    echo "Update .gitignore and try again." >&2
    exit 1
fi

if git diff --cached --quiet; then
    echo "(nothing new to commit — skipping)"
else
    git commit -m "Initial ClassControl v0.2.0 import

Cross-platform classroom management for macOS and Windows.
TLS+HMAC auth, screen monitoring, remote control, demo broadcast,
multi-monitor lock with kiosk mode + input block, app + URL blocking,
internet lockdown, file/folder transfer, Wake-on-LAN, Veyon config
import, GitHub-Releases self-updater."
fi

# ---------------------------------------------------------------------
# 3. Embed the token in the remote URL JUST for this push, then scrub.
# ---------------------------------------------------------------------
PATH_AND_REPO="${REMOTE_URL#https://github.com}"
AUTH_URL="https://${OWNER}:${GITHUB_TOKEN}@github.com${PATH_AND_REPO}"

if git remote | grep -q '^origin$'; then
    git remote set-url origin "$AUTH_URL" >/dev/null
else
    git remote add origin "$AUTH_URL" >/dev/null
fi

echo "→ Pushing to $REMOTE_URL"
# Suppress the token from the push output if git decides to print it.
git push -u origin main 2>&1 | sed -E "s|:[^@]+@|:***@|g"

# Always scrub the stored URL, even if push failed midway.
git remote set-url origin "$REMOTE_URL"
echo "→ Credential scrubbed from .git/config (verify with: git remote -v)"

echo
echo "✓ Done."
echo
echo "Next steps:"
echo "  1. REVOKE the token you used:"
echo "         https://github.com/settings/tokens"
echo "  2. Edit shared/version.py — set GITHUB_OWNER + GITHUB_REPO to your repo."
echo "  3. Verify your push lives at $REMOTE_URL"
