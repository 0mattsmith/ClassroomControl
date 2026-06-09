# Updating

ClassControl has a built-in self-updater. The teacher app's
**Help → Check for Updates…** menu fetches a manifest from the URL in
`shared/version.py`, compares to the running version, and — if newer —
downloads, verifies, swaps the install, and relaunches.

Two hosting flows are supported. Pick whichever fits your workflow.

---

## Option A — GitHub Releases (recommended)

This is what `version.py` defaults to. You publish a GitHub Release per
version with one `.zip` per platform; the updater talks to the GitHub
Releases API directly and figures out the rest.

### One-time setup

1. **Create a GitHub repo** (private or public, doesn't matter).
2. **Push this codebase** to it:
   ```bash
   git init
   git add -A
   git commit -m "Initial ClassControl import"
   git remote add origin https://github.com/<owner>/<repo>.git
   git branch -M main
   git push -u origin main
   ```
3. **Edit `shared/version.py`** — change `GITHUB_OWNER` and `GITHUB_REPO`:
   ```python
   GITHUB_OWNER = "your-username"
   GITHUB_REPO  = "classcontrol"
   ```
4. **Install the GitHub CLI** on whichever machines you'll cut releases from:
   ```bash
   # macOS
   brew install gh
   gh auth login
   ```

### Publishing a new version

On your dev Mac (and again on a Windows machine for the Windows asset):

```bash
./scripts/release.sh --bump patch   # 0.2.0 → 0.2.1
# or
./scripts/release.sh --bump minor   # 0.2.0 → 0.3.0
# or, no bump (you already updated version.py manually):
./scripts/release.sh
```

The script:

1. Optionally bumps `VERSION` in `shared/version.py`.
2. Builds the macOS `.app` (or Windows `.exe` — run on the Windows box for that one).
3. Zips with a platform-tagged filename like `ClassControl-Teacher-0.2.1-mac.zip`.
4. Computes SHA-256 for every asset and writes `SHA256SUMS`.
5. Commits the version bump, tags `v0.2.1`, and pushes.
6. Creates the GitHub Release and uploads the zip(s) + `SHA256SUMS`.

### What end-user machines see

Each connected machine, next time the operator clicks **Check for
Updates…**, fetches `https://api.github.com/repos/<owner>/<repo>/releases/latest`.
The updater:

- Reads `tag_name` (with the leading `v` stripped) as the version
- Picks the asset whose filename contains `mac` / `darwin` / `osx` for macOS
- Picks the asset whose filename contains `win` / `windows` for Windows
- Reads SHA-256 from the `SHA256SUMS` asset if present (skipped silently if not)
- Shows the version, release notes (from the GitHub release body), and an Install button

### Asset filename conventions the updater recognises

| Filename contains | Maps to |
|---|---|
| `mac`, `darwin`, `osx`, or ends `.app.zip` | macOS (`darwin`) |
| `win`, `windows`, or ends `.msi` | Windows (`win32`) |
| `linux` | Linux |

So `ClassControl-Teacher-0.2.1-mac.zip` and `ClassControl-Client-0.2.1-windows.zip` both work.

---

## Option B — Static manifest JSON

If you'd rather host a single JSON file (e.g., on GitHub Pages, S3, your
school's web server), point `CLASSCONTROL_UPDATE_URL` at it. The updater
auto-detects the response shape; no code changes needed.

### Manifest format

```json
{
  "version": "0.2.1",
  "released": "2026-05-30",
  "notes": "Fixed lockdown regression. Added kiosk lock on Windows.",
  "downloads": {
    "darwin": "https://example.com/ClassControl-Teacher-0.2.1-mac.zip",
    "win32":  "https://example.com/ClassControl-Teacher-0.2.1-windows.zip"
  },
  "sha256": {
    "darwin": "abc123…",
    "win32":  "def456…"
  }
}
```

For separate teacher / client versions, use the composite shape:

```json
{
  "teacher": {
    "version": "0.2.1",
    "downloads": { "darwin": "…", "win32": "…" },
    "sha256":    { "darwin": "…", "win32": "…" },
    "notes": "Teacher-side changes…"
  },
  "client": {
    "version": "0.2.0",
    "downloads": { "darwin": "…", "win32": "…" },
    "sha256":    { "darwin": "…", "win32": "…" },
    "notes": "No client changes this release"
  }
}
```

`find_update()` reads either the `teacher` sub-object or the top-level
object depending on which component is checking.

### Override URL per-machine

For staging / dev:

```bash
export CLASSCONTROL_UPDATE_URL=https://staging.example.com/manifest.json
./scripts/run_master.sh
```

---

## Trust model

- **Manifest** is fetched over HTTPS, so GitHub / your server validates
  the response. No additional signing for the manifest itself.
- **Download** is verified against the SHA-256 in the manifest (or
  `SHA256SUMS` on GitHub) before installation. If the hash doesn't
  match, the partial download is deleted and the user sees a clear
  error.
- **Helper script** runs as the same user that ran the app, so it
  cannot escalate beyond what the user already had. It waits for the
  app to exit, swaps directories, and relaunches.

---

## What gets replaced

| Build type | Path replaced |
|---|---|
| macOS `.app` bundle | The `.app` directory in place |
| Windows PyInstaller folder | The `ClassControlClient` (or `Teacher`) folder |
| Source checkout | The project root (development only — be careful) |

The previous version is renamed to `<install>.classcontrol-old` and
deleted after the swap succeeds.
