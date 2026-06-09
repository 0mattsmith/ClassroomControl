<#
.SYNOPSIS
    One-command ClassControl client setup for a Windows student machine.

.DESCRIPTION
    Installs the auth key under %APPDATA%\ClassControl\client\auth.key, adds
    the Windows Defender Firewall inbound rule for TCP 11400, and optionally
    runs the client now or installs it as a SYSTEM-level scheduled task
    that starts on every boot.

    Run from an *Administrator* PowerShell prompt (right-click Start →
    "Windows Terminal (Admin)" or "PowerShell (Admin)"). Without admin
    rights, the firewall + scheduled-task steps will fail.

.PARAMETER AuthKey
    The 64-char hex shared key printed by `./scripts/run_master.sh --print-key`
    on the teacher's Mac. Mutually exclusive with -AuthKeyFile.

.PARAMETER AuthKeyFile
    Path to a text file containing the hex key (no surrounding whitespace
    matters — we trim). Mutually exclusive with -AuthKey.

.PARAMETER Port
    Daemon listen port. Defaults to 11400 (Veyon's default).

.PARAMETER Run
    After setup, immediately launch ClassControlClient.exe so you can
    verify the connection without going through the scheduled task.

.PARAMETER InstallTask
    Also register the boot-time scheduled task that runs the client as
    SYSTEM. The .exe must already be at the path you pass with
    -InstallPath (default: C:\Program Files\ClassControl\ClassControlClient).

.PARAMETER InstallPath
    Directory containing ClassControlClient.exe. Used by both -Run and
    -InstallTask. Default: C:\Program Files\ClassControl\ClassControlClient.

.PARAMETER NoFirewall
    Skip the Windows Firewall rule (use only if your environment already
    has TCP 11400 open via Group Policy).

.EXAMPLE
    .\scripts\setup_windows_client.ps1
    No flag = interactive prompt. Paste the hex (newlines / quotes are
    ignored), press Enter. Safest option — sidesteps every command-line
    quoting / wrapping issue.

.EXAMPLE
    .\scripts\setup_windows_client.ps1 -FromClipboard -Run
    Copy the hex on your Mac (terminal selection → Cmd-C), come to the
    Windows box, run this. It reads directly from the system clipboard.

.EXAMPLE
    .\scripts\setup_windows_client.ps1 -AuthKeyFile .\cc.key -Run
    Read the key from a file you AirDropped / SCP'd off the Mac, then
    start the client immediately.

.EXAMPLE
    .\scripts\setup_windows_client.ps1 -AuthKey "abcdef0123...ff"
    One-shot when you're sure quoting will survive. Use one of the
    other forms if you've been bitten by partial pastes.

.EXAMPLE
    .\scripts\setup_windows_client.ps1 -FromClipboard -InstallTask `
        -InstallPath "C:\Program Files\ClassControl\ClassControlClient"
    Full classroom deployment: key + firewall + boot-time scheduled task.
#>

[CmdletBinding(DefaultParameterSetName='Interactive')]
param(
    [Parameter(ParameterSetName='Key')]
    [string]$AuthKey,

    [Parameter(ParameterSetName='File')]
    [string]$AuthKeyFile,

    [Parameter(ParameterSetName='Clipboard')]
    [switch]$FromClipboard,

    [int]$Port = 11400,

    [switch]$Run,

    [switch]$InstallTask,

    [string]$InstallPath = "C:\Program Files\ClassControl\ClassControlClient",

    [switch]$NoFirewall
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------
# 1. Resolve the auth key from whichever source the user picked
# ---------------------------------------------------------------------

function Clean-HexKey([string]$raw) {
    if (-not $raw) { return "" }
    # Strip ALL whitespace (incl. CR/LF inside multi-line pastes) and
    # any wrapping quotes a user might have copied along with the hex.
    $cleaned = ($raw -replace '\s', '').Trim('"', "'")
    return $cleaned
}

switch ($PSCmdlet.ParameterSetName) {
    'File' {
        if (-not (Test-Path $AuthKeyFile)) {
            throw "Auth key file not found: $AuthKeyFile"
        }
        $AuthKey = Clean-HexKey ((Get-Content $AuthKeyFile -Raw))
    }
    'Clipboard' {
        Add-Type -AssemblyName System.Windows.Forms
        $clip = [System.Windows.Forms.Clipboard]::GetText()
        if (-not $clip) {
            throw "Clipboard is empty. Copy the hex key on your Mac first."
        }
        $AuthKey = Clean-HexKey $clip
        Write-Host "[+] Read key from clipboard ($($AuthKey.Length) chars)"
    }
    'Key' {
        $AuthKey = Clean-HexKey $AuthKey
    }
    'Interactive' {
        Write-Host ""
        Write-Host "Paste the 64-char hex auth key printed by:"
        Write-Host "  ./scripts/run_master.sh --print-key   (on your Mac)" -ForegroundColor Yellow
        Write-Host "then press Enter:"
        $pasted = Read-Host
        $AuthKey = Clean-HexKey $pasted
    }
}

if ($AuthKey -notmatch '^[0-9a-fA-F]{64}$') {
    $len = $AuthKey.Length
    $preview = if ($len -gt 0) {
        $head = $AuthKey.Substring(0, [Math]::Min(8, $len))
        $tail = if ($len -gt 16) { $AuthKey.Substring($len - 8) } else { "" }
        "starts '$head…' ends '$tail'"
    } else { "(empty)" }
    Write-Host ""
    Write-Host "Auth key validation failed." -ForegroundColor Red
    Write-Host "  Expected: exactly 64 hex characters (0-9, a-f)"
    Write-Host "  Got:      $len characters — $preview"
    Write-Host ""
    Write-Host "Try one of these instead:" -ForegroundColor Yellow
    Write-Host "  .\scripts\setup_windows_client.ps1                       # interactive prompt"
    Write-Host "  .\scripts\setup_windows_client.ps1 -FromClipboard        # read from your clipboard"
    Write-Host "  .\scripts\setup_windows_client.ps1 -AuthKeyFile <path>   # read from a file"
    throw "Invalid auth key."
}

# ---------------------------------------------------------------------
# 2. Install the key into %APPDATA%\ClassControl\client\auth.key
# ---------------------------------------------------------------------

$keyDir = Join-Path $env:APPDATA "ClassControl\client"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "auth.key"

# Out-File with -NoNewline + ascii avoids any UTF-8 BOM or CRLF
# tail that would corrupt the hex hex.fromhex() round-trip on the
# daemon side.
[System.IO.File]::WriteAllText($keyPath, $AuthKey)

Write-Step "Auth key installed: $keyPath"

# ---------------------------------------------------------------------
# 3. Windows Defender Firewall rule (idempotent)
# ---------------------------------------------------------------------

if (-not $NoFirewall) {
    try {
        $existing = Get-NetFirewallRule -DisplayName "ClassControl Client" `
                    -ErrorAction SilentlyContinue
        if ($existing) {
            Remove-NetFirewallRule -DisplayName "ClassControl Client"
        }
        New-NetFirewallRule `
            -DisplayName "ClassControl Client" `
            -Description "Allow inbound teacher connections" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -Profile Private,Domain `
            -Enabled True | Out-Null
        Write-Step "Firewall rule added: TCP $Port inbound (Private + Domain)"
    } catch {
        Write-Warn "Could not add firewall rule (need admin?): $_"
    }
}

# ---------------------------------------------------------------------
# 4. Optionally: register the scheduled task for boot-time auto-start
# ---------------------------------------------------------------------

if ($InstallTask) {
    $taskScript = Join-Path $PSScriptRoot "install_windows_client.ps1"
    if (-not (Test-Path $taskScript)) {
        Write-Warn "install_windows_client.ps1 not found alongside this script."
    } else {
        & $taskScript -InstallPath $InstallPath
        Write-Step "Scheduled task 'ClassControlClient' registered."
    }
}

# ---------------------------------------------------------------------
# 5. Optionally: launch the client now so you can verify
# ---------------------------------------------------------------------

if ($Run) {
    $exe = Join-Path $InstallPath "ClassControlClient.exe"
    if (-not (Test-Path $exe)) {
        Write-Warn "Cannot find $exe — did you build with build_windows.ps1 and place it at -InstallPath?"
    } else {
        Write-Step "Launching $exe — accept the UAC prompt when it appears."
        Start-Process -FilePath $exe
    }
}

# ---------------------------------------------------------------------
# 6. Summary + next steps
# ---------------------------------------------------------------------

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host "Auth key file:   $keyPath"
Write-Host "Listening port:  $Port"
if (-not $NoFirewall) {
    Write-Host "Firewall rule:   ClassControl Client (Inbound TCP $Port)"
}
Write-Host ""
if (-not $Run) {
    Write-Host "Start the client now with:"
    Write-Host "  $InstallPath\ClassControlClient.exe"
}
Write-Host ""
Write-Host "On the teacher Mac, add this PC to the roster with:"
Write-Host "  Host: <this machine's LAN IP — run `ipconfig` to find it>"
Write-Host "  Port: $Port"
