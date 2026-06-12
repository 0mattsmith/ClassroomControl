<#
.SYNOPSIS
    Build the Windows .exe bundles and publish them to a GitHub Release.

.DESCRIPTION
    The Windows counterpart of scripts/release.sh. Runs on the
    Windows machine (you can't cross-build .exe from macOS / Linux).

    What it does, in order:

      1. Reads the version from shared\version.py.
      2. Optionally bumps it with -Bump patch / minor / major.
      3. Builds the Windows bundles using build_windows.ps1.
      4. Zips dist\ClassControlTeacher\ and dist\ClassControlClient\
         into platform-tagged archives.
      5. Computes SHA-256 of every zip and writes SHA256SUMS.
      6. Commits the version bump, tags the release, pushes.
      7. Creates (or updates) the GitHub Release with the tag and
         uploads the zips + SHA256SUMS as assets.

    The updater inside the running app picks up the right asset by
    filename (anything containing "win"/"windows" → win32).

.PARAMETER Bump
    Optional version bump: patch / minor / major. If omitted, uses
    whatever's already in shared\version.py.

.PARAMETER Notes
    Release notes. If omitted, gh's --generate-notes is used.

.PARAMETER Draft
    Don't publish — leave the release as a draft.

.PARAMETER SkipBuild
    Skip the build step (handy if you've just built and only need to
    re-upload the existing zips).

.EXAMPLE
    .\scripts\release.ps1 -Bump patch
    Cuts a 0.2.1 release with auto-generated notes.

.EXAMPLE
    .\scripts\release.ps1 -Notes "Fixed lockdown regression on Windows"
    No version bump; reuses the current shared\version.py value.

.NOTES
    Requirements (one-time):
      * gh CLI installed and `gh auth login` already done
      * git remote 'origin' pointed at the GitHub repo
      * working tree is clean (no uncommitted changes other than the
        bump we're about to make)

    Run scripts/release.sh on the Mac side to publish the .app
    bundles to the SAME release tag — gh's upload --clobber will
    happily attach Windows zips alongside Mac zips on one release.
#>

[CmdletBinding()]
param(
    [ValidateSet("patch","minor","major")]
    [string]$Bump,
    [string]$Notes,
    [switch]$Draft,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

function Section($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Yellow }
function OK($t)      { Write-Host "  ok  " -ForegroundColor Green -NoNewline; Write-Host $t }

# ---------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "gh CLI not found. Install from https://cli.github.com/ then 'gh auth login'."
}
gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "gh not authenticated. Run:  gh auth login"
}

# ---------------------------------------------------------------------
# Bump version (optional)
# ---------------------------------------------------------------------
if ($Bump) {
    Section "Bumping version ($Bump)"
    $newVer = python -c @"
import re, sys
kind = sys.argv[1]
src = open('shared/version.py').read()
m = re.search(r'VERSION = "(\d+)\.(\d+)\.(\d+)"', src)
if not m: sys.exit('Could not parse VERSION')
major, minor, patch = map(int, m.groups())
if kind == 'major': major, minor, patch = major + 1, 0, 0
elif kind == 'minor': minor, patch = minor + 1, 0
else: patch += 1
new = f'{major}.{minor}.{patch}'
open('shared/version.py', 'w').write(
    re.sub(r'VERSION = "[^"]+"', f'VERSION = "{new}"', src)
)
print(new)
"@ $Bump
    OK "shared/version.py bumped to $newVer"
}

# Read the (possibly bumped) version
$VERSION = python -c "from shared.version import VERSION; print(VERSION)"
$TAG     = "v$VERSION"
Write-Host ""
Write-Host "Releasing ClassControl $TAG (Windows side)" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------
if (-not $SkipBuild) {
    Section "Building Windows bundles"
    & .\scripts\build_windows.ps1
    if ($LASTEXITCODE -ne 0) { throw "build_windows.ps1 failed" }
}

if (-not (Test-Path "dist\ClassControlTeacher")) {
    throw "dist\ClassControlTeacher not found. Build first, or remove -SkipBuild."
}
if (-not (Test-Path "dist\ClassControlClient")) {
    throw "dist\ClassControlClient not found."
}

# ---------------------------------------------------------------------
# Zip + SHA
# ---------------------------------------------------------------------
Section "Zipping artifacts"
$releaseDir = "release\$VERSION"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

$teacherZip = "ClassControl-Teacher-$VERSION-windows.zip"
$clientZip  = "ClassControl-Client-$VERSION-windows.zip"

Compress-Archive -Path "dist\ClassControlTeacher\*" `
    -DestinationPath (Join-Path $releaseDir $teacherZip) -Force
OK $teacherZip
Compress-Archive -Path "dist\ClassControlClient\*" `
    -DestinationPath (Join-Path $releaseDir $clientZip) -Force
OK $clientZip

# SHA256
Section "Computing SHA-256 (appended to SHA256SUMS)"
$sumsPath = Join-Path $releaseDir "SHA256SUMS"
Get-ChildItem -Path $releaseDir -Filter *.zip | ForEach-Object {
    $sha = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    "$sha  $($_.Name)" | Out-File -Encoding ascii -Append $sumsPath
    OK "$sha  $($_.Name)"
}

# ---------------------------------------------------------------------
# Commit + tag + push
# ---------------------------------------------------------------------
Section "Committing version bump + tagging $TAG"
git diff --quiet "shared/version.py" 2>$null
if ($LASTEXITCODE -ne 0) {
    git add shared/version.py
    git commit -m "Release $TAG (Windows)" 2>&1 | Out-Null
    OK "version bump committed"
}

$exists = (git rev-parse $TAG 2>$null)
if ($LASTEXITCODE -ne 0) {
    git tag -a $TAG -m "ClassControl $TAG"
    git push origin $TAG
    OK "tag $TAG pushed"
}
git push 2>&1 | Out-Null

# ---------------------------------------------------------------------
# Create release + upload assets
# ---------------------------------------------------------------------
Section "Publishing to GitHub Releases"
$assets = @(
    (Join-Path $releaseDir $teacherZip),
    (Join-Path $releaseDir $clientZip),
    $sumsPath
)

gh release view $TAG 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    OK "Release $TAG exists — uploading additional assets"
    & gh release upload $TAG $assets --clobber
} else {
    $args = @($TAG) + $assets + @("--title","ClassControl $VERSION")
    if ($Draft) { $args += "--draft" }
    if ($Notes) {
        $args += @("--notes", $Notes)
    } else {
        $args += "--generate-notes"
    }
    & gh release create @args
}

Write-Host ""
OK "Done. Windows assets published to https://github.com/<repo>/releases/tag/$TAG"
Write-Host ""
Write-Host "Run scripts/release.sh on your Mac (with the same -Bump value or" -ForegroundColor Cyan
Write-Host "no bump) to attach the macOS .app zips to the same release." -ForegroundColor Cyan
