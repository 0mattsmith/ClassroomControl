#!/usr/bin/env bash
# release.sh — cut a new ClassControl release and publish it to GitHub.
#
# What it does, in order:
#   1. Reads the version from shared/version.py.
#   2. Builds the .app (macOS) or .exe (Windows) for whichever platform
#      you're running on. (Run once per platform you support.)
#   3. Zips the build artifact with a platform-tagged filename.
#   4. Computes SHA-256 and updates a local SHA256SUMS file.
#   5. Tags the current commit and pushes the tag.
#   6. Creates a GitHub Release with the tag and uploads the .zip
#      + SHA256SUMS as release assets. The updater auto-detects the
#      right asset for each platform by filename.
#
# Requirements:
#   * gh CLI installed and `gh auth login` already done
#   * git remote 'origin' pointed at the GitHub repo
#   * the working tree is clean (no uncommitted changes)
#
# Usage:
#   ./scripts/release.sh                # uses VERSION from version.py
#   ./scripts/release.sh --bump patch   # bump 0.2.0 → 0.2.1 first
#   ./scripts/release.sh --bump minor   # bump 0.2.0 → 0.3.0 first
#   ./scripts/release.sh --notes "Fixed lockdown bug"
#   ./scripts/release.sh --draft         # don't publish, leave as draft

set -euo pipefail
cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------
BUMP=""
NOTES=""
DRAFT=""
SKIP_BUILD=""
while [ $# -gt 0 ]; do
    case "$1" in
        --bump)       BUMP="$2"; shift 2 ;;
        --notes)      NOTES="$2"; shift 2 ;;
        --draft)      DRAFT="--draft"; shift ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------
# Optionally bump VERSION
# ---------------------------------------------------------------------
if [ -n "$BUMP" ]; then
    python3 - <<PYBUMP "$BUMP"
import re, sys
kind = sys.argv[1]
path = "shared/version.py"
src = open(path).read()
m = re.search(r'VERSION = "(\d+)\.(\d+)\.(\d+)"', src)
if not m:
    print("Could not parse current VERSION", file=sys.stderr); sys.exit(2)
major, minor, patch = map(int, m.groups())
if   kind == "major": major, minor, patch = major + 1, 0, 0
elif kind == "minor": minor, patch = minor + 1, 0
elif kind == "patch": patch += 1
else: print(f"unknown bump kind: {kind}", file=sys.stderr); sys.exit(2)
new = f'{major}.{minor}.{patch}'
src2 = re.sub(r'VERSION = "[^"]+"', f'VERSION = "{new}"', src)
open(path, "w").write(src2)
print(new)
PYBUMP
fi

VERSION=$(python3 -c 'from shared.version import VERSION; print(VERSION)')
TAG="v$VERSION"

echo "→ Releasing ClassControl $TAG"

# ---------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
    echo "✗ gh CLI not found. Install with:  brew install gh"
    echo "  Then:                          gh auth login"
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "✗ gh not authenticated. Run:  gh auth login"
    exit 1
fi

# ---------------------------------------------------------------------
# Build for current platform
# ---------------------------------------------------------------------
if [ -z "$SKIP_BUILD" ]; then
    case "$(uname -s)" in
        Darwin)
            echo "→ Building macOS .app bundles..."
            ./scripts/build_macos.sh
            BUILD_DIR=dist
            ASSET_TAG=mac
            ;;
        Linux|MINGW*|MSYS*)
            echo "Cross-build to Windows from this host isn't supported."
            echo "Run release.sh on a Windows machine for the Windows build."
            exit 1
            ;;
        *)
            echo "Unsupported host: $(uname -s)"
            exit 1
            ;;
    esac
fi

# ---------------------------------------------------------------------
# Zip + SHA
# ---------------------------------------------------------------------
mkdir -p release/$VERSION
case "$(uname -s)" in
    Darwin)
        cd dist
        for app in "ClassControl Teacher.app" "ClassControl Client.app"; do
            if [ -d "$app" ]; then
                short=$(echo "$app" | tr ' ' '-' | sed 's/.app$//')
                zip_name="ClassControl-${short}-${VERSION}-mac.zip"
                echo "→ Zipping $app → $zip_name"
                ditto -c -k --sequesterRsrc --keepParent "$app" "../release/$VERSION/$zip_name"
            fi
        done
        cd ..
        ;;
esac

# Compute SHA256 of every asset
cd release/$VERSION
{ shasum -a 256 *.zip 2>/dev/null || sha256sum *.zip; } > SHA256SUMS
cd ../..

# ---------------------------------------------------------------------
# Git tag + push
# ---------------------------------------------------------------------
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "→ Committing version bump and release artefacts"
    git add shared/version.py
    git commit -m "Release $TAG" || true
fi
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
    git tag -a "$TAG" -m "ClassControl $TAG"
    git push origin "$TAG"
    git push
fi

# ---------------------------------------------------------------------
# Create GitHub Release + upload assets
# ---------------------------------------------------------------------
NOTES_ARG=""
if [ -n "$NOTES" ]; then
    NOTES_ARG="--notes \"$NOTES\""
else
    NOTES_ARG="--generate-notes"
fi

ASSETS=(release/$VERSION/*.zip release/$VERSION/SHA256SUMS)

set +e
gh release view "$TAG" >/dev/null 2>&1
EXISTS=$?
set -e

if [ $EXISTS -eq 0 ]; then
    echo "→ Release $TAG already exists — uploading additional assets"
    gh release upload "$TAG" "${ASSETS[@]}" --clobber
else
    echo "→ Creating GitHub release $TAG"
    eval gh release create "$TAG" "${ASSETS[@]}" $DRAFT --title "ClassControl $VERSION" $NOTES_ARG
fi

echo
echo "✓ Done. Connected machines will see this version next time they"
echo "  open Help → Check for Updates."
