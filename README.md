# ClassControl

A Veyon-style classroom management system written in Python + PyQt6,
with full support for **both macOS and Windows on both ends**. The
teacher's machine (the *master*) manages a roster of student machines
(the *clients*) and can monitor screens, take remote control, broadcast
the teacher's screen, lock screens, send messages and files, block apps
and websites, lock down the internet (without losing the control
channel), remotely open apps/URLs, and shut down or restart machines.

It speaks a custom TLS-encrypted protocol with HMAC-SHA256 challenge/
response authentication using a shared key, modelled on Veyon's
master/service architecture.

---

## Cross-platform support

The four host/client combinations are all supported:

| Combination                        | Status |
| ---------------------------------- | :----: |
| macOS host ↔ macOS client         | ✅ |
| macOS host ↔ Windows client       | ✅ |
| Windows host ↔ macOS client       | ✅ |
| Windows host ↔ Windows client     | ✅ |

The platform-specific implementations live in `client/macos/` and
`client/windows/`; `client/platform.py` picks the right backend at
import time based on `sys.platform`. The teacher app uses the same
shim (via `master/screen_capture.py`) so Demo Mode works whether the
teacher is on a Mac or a PC.

---

## Feature matrix

| Capability                                    | macOS | Windows |
| --------------------------------------------- | :---: | :-----: |
| Live thumbnail grid of every student          | ✅ | ✅ |
| Remote control (mouse + keyboard + scroll)    | ✅ | ✅ |
| Demo mode (broadcast teacher screen)          | ✅ | ✅ |
| Screen lock with custom message               | ✅ | ✅ |
| Send message popup                            | ✅ | ✅ |
| Send file (master → students)                 | ✅ | ✅ |
| Request file (students → master)              | ✅ | ✅ |
| Launch app or URL on student machines         | ✅ | ✅ |
| Block apps (by bundle ID / process name)      | ✅ | ✅ |
| Block websites (`/etc/hosts` or `drivers\etc\hosts`) | ✅ | ✅ |
| **Internet lockdown** (keeps teacher through) | ✅ pf | ✅ Windows Firewall |
| Power: shutdown / restart / sleep / log out   | ✅ | ✅ |
| Audio: mute / unmute / set volume             | ✅ osascript | ✅ pycaw |
| Activity audit log                            | ✅ | ✅ |
| Persistent computer roster (groups, notes)    | ✅ | ✅ |
| TLS + shared-key authentication               | ✅ | ✅ |

---

## Project layout

```
.
├── master/                Teacher app (PyQt6, cross-platform)
│   ├── app.py
│   ├── connection.py
│   ├── roster.py
│   ├── activity_log.py
│   ├── screen_capture.py    Platform-shim re-export for demo mode
│   └── ui/
│       ├── main_window.py
│       ├── thumbnail.py
│       ├── remote_control.py
│       ├── demo_broadcaster.py
│       └── dialogs.py
├── client/                Student daemon (cross-platform)
│   ├── daemon.py            Entry point: TLS server + Qt overlay
│   ├── overlay.py
│   ├── platform.py          Picks macos/ or windows/ backend
│   ├── macos/               Quartz / AppKit / pfctl / osascript
│   └── windows/             SendInput / Win32 / netsh / pycaw
├── shared/                Used by both ends
│   ├── protocol.py          Frame format, auth, TLS contexts
│   ├── config.py            Per-user config paths
│   └── logging_setup.py
├── packaging/
│   ├── setup_master.py            py2app  (macOS teacher app)
│   ├── setup_client.py            py2app  (macOS student app)
│   ├── io.classcontrol.client.plist   macOS LaunchDaemon
│   ├── classcontrol-sudoers           macOS sudoers drop-in
│   ├── classcontrol_teacher.spec  PyInstaller (Windows teacher app)
│   └── classcontrol_client.spec   PyInstaller (Windows client daemon)
├── scripts/
│   ├── build_macos.sh             Build both .app bundles
│   ├── run_master.sh              Run teacher from source (macOS/Linux)
│   ├── run_client.sh              Run client from source (macOS/Linux)
│   ├── build_windows.ps1          Build both .exe bundles
│   ├── install_windows_client.ps1 Register the client as a SYSTEM scheduled task
│   ├── run_master.ps1             Run teacher from source (Windows)
│   └── run_client.ps1             Run client from source (Windows)
└── requirements.txt
```

---

## Quick start (from source)

### Common to both platforms

```bash
git clone <your-repo> ClassControl
cd ClassControl
python3 -m venv .venv
# macOS / Linux:   source .venv/bin/activate
# Windows:         .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Only the lines for your OS in `requirements.txt` actually install
(thanks to PEP 508 markers), so it's safe to run on either platform.

### 1. Generate the shared key (on the teacher machine)

```bash
# macOS / Linux
./scripts/run_master.sh --print-key
# Windows
./scripts/run_master.ps1 --print-key
```

It prints a 64-character hex string. Copy it.

### 2. Distribute that key to every student machine

The client also creates a local key the first time it starts; replace
it with the teacher's:

**macOS / Linux students**

```bash
./scripts/run_client.sh --print-key             # creates the file
echo "<teacher hex key>" > \
    "$HOME/Library/Application Support/ClassControl/client/auth.key"
chmod 600 "$HOME/Library/Application Support/ClassControl/client/auth.key"
```

**Windows students**

```powershell
.\scripts\run_client.ps1 --print-key
"<teacher hex key>" | Out-File -Encoding ascii -NoNewline `
    "$env:APPDATA\ClassControl\client\auth.key"
```

### 3. Run the student daemon

**macOS**

```bash
./scripts/run_client.sh
```

macOS will prompt for permissions on first launch — grant all three or
features silently no-op:

* **Screen Recording** (System Settings → Privacy & Security)
* **Accessibility** (for synthetic mouse/keyboard)
* **Automation** (for power/audio AppleScripts)

**Windows**

```powershell
# Run from an elevated PowerShell window so firewall/hosts/shutdown work
./scripts/run_client.ps1
```

Windows Defender Firewall may show a one-time prompt to allow the
daemon's TCP listener on port 11400 — allow it on private + domain
networks.

### 4. Run the teacher app (same on both OSes)

```bash
# macOS
./scripts/run_master.sh
# Windows
./scripts/run_master.ps1
```

Use **Add computer…** to enter each student's hostname/IP and port
(default 11400). Connections come up automatically.

---

## Packaging native bundles

### macOS — .app

```bash
./scripts/build_macos.sh
# Outputs:
#   dist/ClassControl Teacher.app
#   dist/ClassControl Client.app
```

Drop **ClassControl Client.app** into `/Applications` on each student
Mac. For auto-start install the LaunchDaemon:

```bash
sudo cp packaging/io.classcontrol.client.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/io.classcontrol.client.plist
sudo chmod 644 /Library/LaunchDaemons/io.classcontrol.client.plist
sudo launchctl load -w /Library/LaunchDaemons/io.classcontrol.client.plist
```

### Windows — .exe

```powershell
./scripts/build_windows.ps1
# Outputs:
#   dist/ClassControlTeacher/ClassControlTeacher.exe
#   dist/ClassControlClient/ClassControlClient.exe
```

The client is built with `uac_admin=True`, so double-clicking it
triggers a UAC prompt and runs elevated (required for firewall lockdown,
hosts edits, and shutdown). For unattended boot-time auto-start install
it as a SYSTEM-level scheduled task on each student PC:

```powershell
# Copy the built directory somewhere stable, then from an ELEVATED prompt:
./scripts/install_windows_client.ps1 -InstallPath "C:\Program Files\ClassControl\ClassControlClient"
```

This registers a task named **ClassControlClient** that runs as
**NT AUTHORITY\SYSTEM** with highest privileges, starts at boot, and
auto-restarts on failure.

---

## Privileged actions

Three features require elevated privileges:

| Feature              | macOS                          | Windows                              |
| -------------------- | ------------------------------ | ------------------------------------ |
| Internet lockdown    | `pfctl` (root)                 | `netsh advfirewall` (Administrator)  |
| URL blocking (hosts) | `/etc/hosts` (root)            | `drivers\etc\hosts` (Administrator)  |
| Shutdown / restart   | `shutdown -h/-r` (root)        | `shutdown.exe /s/r` (any user)*      |

\* Shutdown on Windows works for any local user, but `/f` forces app
close which some IT policies restrict.

The macOS LaunchDaemon and the Windows scheduled-task installer both
configure the client to run with the necessary privileges. If you'd
rather keep the client running as the logged-in user, use the macOS
sudoers drop-in in `packaging/classcontrol-sudoers` (narrowly scoped to
the exact binaries needed).

---

## Security model

* TLS 1.2+ on every link. Client certs are self-signed (trust-on-first-use);
  identity is proven by HMAC-SHA256 challenge/response against a 32-byte
  shared key.
* The shared key lives at:
  * macOS:   `~/Library/Application Support/ClassControl/<role>/auth.key`
  * Windows: `%APPDATA%\ClassControl\<role>\auth.key`

  Mode `0600` on macOS; on Windows it relies on per-user `%APPDATA%`
  ACLs. Treat it like a password.
* Internet lockdown allows only the listed teacher IP(s) through. If the
  list is wrong you'll lose the control channel — verify with `ifconfig`
  (macOS) or `ipconfig` (Windows) before turning it on.
* The client only ever serves **one** master connection at a time. New
  connections from a second master are rejected with `busy`.
* Every teacher action is appended to the audit log at:
  * macOS:   `~/Library/Application Support/ClassControl/master/activity.log`
  * Windows: `%APPDATA%\ClassControl\master\activity.log`

---

## Known limitations / honest caveats

* This is a first-pass implementation. The wire protocol works but
  hasn't been battle-tested at scale.
* macOS requires per-app TCC permissions for screen capture, input
  injection, and AppleScript. Re-building the .app changes the bundle
  hash so the user will be re-prompted.
* On Windows, the screen-capture call uses GDI/BitBlt. Some games and a
  few hardware-accelerated overlays will appear as black rectangles —
  this is the same limitation Veyon has.
* Demo mode broadcasts JPEG frames. With many students the teacher's
  uplink can saturate — lower the fps in
  `master/ui/demo_broadcaster.py` if you're seeing dropped frames.
* Per-user persistence (e.g., a client surviving a user-switch on macOS)
  needs a LaunchAgent rather than the supplied LaunchDaemon.

---

## Troubleshooting

* **"authentication failed (wrong shared key?)"** — copy the teacher's
  `auth.key` byte-for-byte to each client.
* **Black thumbnails / "(no signal)" on macOS** — Screen Recording
  permission missing. System Settings → Privacy & Security → Screen
  Recording → enable the app.
* **Black thumbnails on Windows for a specific app** — that app is
  rendering via a path GDI can't capture (some games, secure browser
  surfaces). Capturing the desktop as a whole still works.
* **Remote control does nothing on macOS** — Accessibility permission
  missing.
* **Remote control does nothing on Windows** — the master is targeting
  a session running on a different desktop (e.g., a UAC secure desktop
  is up). Dismiss the UAC prompt and retry.
* **Lockdown blocks everything including the teacher** — the master IP
  you supplied was wrong. Release lockdown via the toolbar checkbox
  (sent over the still-allowed local-network path), or on the student
  machine run:
  * macOS:   `sudo pfctl -a classcontrol -F all`
  * Windows: `netsh advfirewall firewall delete rule group=ClassControl-Lockdown`
