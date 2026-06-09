#!/usr/bin/env bash
# unblock_all.sh — undo every form of network/web blocking ClassControl
# might have applied to this Mac. Safe to re-run any time.
#
# What it does, in order:
#   1. Strips the managed block section from /etc/hosts (with a backup).
#   2. Searches for suspicious entries OUTSIDE the managed section and
#      shows them so you can decide whether to remove them by hand.
#   3. Restores /etc/pf.conf as the active pf ruleset (undoes Internet
#      Lockdown) and flushes any ClassControl pf anchor + rules file.
#   4. Flushes the macOS DNS cache + reloads mDNSResponder so changes
#      take effect immediately.
#   5. Optionally clears the master's saved blocking.json (so the next
#      teacher-app launch doesn't re-push the old list).
#   6. Runs a verification DNS query against google.com to prove it now
#      resolves correctly.
#
# Usage:
#   ./scripts/unblock_all.sh                    # hosts + pf + DNS flush
#   ./scripts/unblock_all.sh --reset-master     # also empty the master's block list
#   ./scripts/unblock_all.sh --dry-run          # show what would change, change nothing

set -uo pipefail

DRY_RUN=""
RESET_MASTER=""
for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --reset-master) RESET_MASTER=1 ;;
        -h|--help)      sed -n '2,28p' "$0"; exit 0 ;;
        *)              echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

HOSTS_FILE=/etc/hosts
HOSTS_BEGIN="# >>> classcontrol-block >>>"
HOSTS_END="# <<< classcontrol-block <<<"

# Colour helpers ------------------------------------------------------
RED='\033[1;31m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { printf "${GREEN}✓${NC} %s\n" "$*"; }
no()  { printf "${YELLOW}—${NC} %s\n" "$*"; }
err() { printf "${RED}✗${NC} %s\n" "$*"; }
head() { printf "\n${YELLOW}=== %s ===${NC}\n" "$*"; }

# Re-exec self under sudo if we aren't root.
if [ "$(id -u)" -ne 0 ]; then
    printf "${YELLOW}You'll be prompted for your password — sudo is needed to modify /etc/hosts and pf.${NC}\n"
    exec sudo --preserve-env=SUDO_USER bash "$0" "$@"
fi

# Capture the calling user so we can find their config dir later.
CALLER="${SUDO_USER:-$USER}"
CALLER_HOME=$(eval echo "~$CALLER")

# ---------------------------------------------------------------------
# 1. /etc/hosts — managed block section
# ---------------------------------------------------------------------
head "/etc/hosts — managed block section"
if grep -q "$HOSTS_BEGIN" "$HOSTS_FILE" 2>/dev/null; then
    echo "Found a ClassControl-managed section. Current entries:"
    sed -n "/$HOSTS_BEGIN/,/$HOSTS_END/p" "$HOSTS_FILE" | grep -E '^[0-9]' || echo "  (none)"
    if [ -z "$DRY_RUN" ]; then
        STAMP=$(date +%Y%m%d%H%M%S)
        cp "$HOSTS_FILE" "$HOSTS_FILE.bak.$STAMP"
        sed -i.tmp "/$HOSTS_BEGIN/,/$HOSTS_END/d" "$HOSTS_FILE"
        rm -f "$HOSTS_FILE.tmp"
        ok "Removed the managed section. Backup at $HOSTS_FILE.bak.$STAMP"
    else
        no "(dry-run; not modifying)"
    fi
else
    ok "No ClassControl-managed section in $HOSTS_FILE — already clean."
fi

# ---------------------------------------------------------------------
# 2. /etc/hosts — suspicious entries OUTSIDE our managed section
# ---------------------------------------------------------------------
head "/etc/hosts — anything else pointing localhost to common sites"
PATTERN='(google|youtube|facebook|twitter|tiktok|reddit|netflix|instagram|snapchat|twitch)'
# Match lines that route a "common-site" host to a loopback / null address.
SUSPICIOUS=$(awk -v pat="$PATTERN" '
    /^[[:space:]]*#/ { next }                 # skip comments
    $1 ~ /^(127\.|0\.0\.0\.0|::1)/ && tolower($0) ~ pat { print }
' "$HOSTS_FILE" || true)
if [ -n "$SUSPICIOUS" ]; then
    err "Found entries OUTSIDE the managed section that look like blocks:"
    echo "$SUSPICIOUS" | sed 's/^/    /'
    echo
    echo "${YELLOW}These were NOT removed because they're outside the area"
    echo "ClassControl owns. If you want them gone, edit $HOSTS_FILE by"
    echo "hand:    sudo nano $HOSTS_FILE${NC}"
else
    ok "No common-site loopback entries found."
fi

# ---------------------------------------------------------------------
# 3. pf firewall — restore main ruleset
# ---------------------------------------------------------------------
head "pf firewall — restore /etc/pf.conf"
if pfctl -sr 2>/dev/null | grep -q -i "ClassControl"; then
    echo "ClassControl rules currently active in the pf main ruleset."
    if [ -z "$DRY_RUN" ]; then
        if pfctl -f /etc/pf.conf 2>/dev/null; then
            ok "Reloaded /etc/pf.conf as the active ruleset."
        else
            err "pfctl -f /etc/pf.conf failed."
        fi
    else
        no "(dry-run; would run: pfctl -f /etc/pf.conf)"
    fi
else
    ok "No ClassControl rules in the active pf ruleset."
fi

# Also flush our anchor and remove the rules file, belt-and-braces.
if [ -z "$DRY_RUN" ]; then
    pfctl -a classcontrol -F all 2>/dev/null || true
    if [ -f /etc/pf.anchors/classcontrol-active ]; then
        rm -f /etc/pf.anchors/classcontrol-active
        ok "Removed /etc/pf.anchors/classcontrol-active."
    fi
fi

# ---------------------------------------------------------------------
# 4. DNS caches
# ---------------------------------------------------------------------
head "DNS caches"
if [ -z "$DRY_RUN" ]; then
    if dscacheutil -flushcache 2>/dev/null; then ok "dscacheutil cache flushed."; fi
    if killall -HUP mDNSResponder 2>/dev/null; then
        ok "mDNSResponder reloaded."
    else
        no "mDNSResponder wasn't running (or already gone)."
    fi
else
    no "(dry-run; would flush dscacheutil + mDNSResponder)"
fi

# ---------------------------------------------------------------------
# 5. Optionally reset the master's saved blocking.json
# ---------------------------------------------------------------------
if [ -n "$RESET_MASTER" ]; then
    head "Master block list ($CALLER's blocking.json)"
    MASTER_BL="$CALLER_HOME/Library/Application Support/ClassControl/master/blocking.json"
    if [ -f "$MASTER_BL" ]; then
        if [ -z "$DRY_RUN" ]; then
            STAMP=$(date +%Y%m%d%H%M%S)
            cp "$MASTER_BL" "$MASTER_BL.bak.$STAMP"
            cat > "$MASTER_BL" <<EOF
{
  "apps_master": false,
  "urls_master": false,
  "apps": {},
  "urls": {}
}
EOF
            chown "$CALLER" "$MASTER_BL" 2>/dev/null || true
            ok "Reset to empty + master switches off. Backup at $MASTER_BL.bak.$STAMP"
        else
            no "(dry-run; would clear $MASTER_BL)"
        fi
    else
        ok "No master blocking.json — nothing to reset."
    fi
fi

# ---------------------------------------------------------------------
# 6. Verify by querying google.com
# ---------------------------------------------------------------------
head "Verification"
RESULT=$(dscacheutil -q host -a name google.com 2>&1 | sed 's/^/    /')
echo "$RESULT"
if echo "$RESULT" | grep -q '127\.0\.0\.1\|0\.0\.0\.0'; then
    err "google.com STILL resolves to a loopback / null address."
    echo
    echo "Things to check next:"
    echo "  • Your browser may have its own DNS cache — quit + relaunch it."
    echo "    Chrome: chrome://net-internals/#dns → Clear host cache."
    echo "  • Run     sudo cat $HOSTS_FILE     and look for non-classcontrol"
    echo "    entries pointing google.com somewhere weird."
    echo "  • Other tools that might be blocking: Little Snitch, hostsblock,"
    echo "    NextDNS, Pi-Hole on your router, parental controls."
else
    ok "google.com resolves to a real public IP — looks unblocked."
fi

echo
ok "Done."
