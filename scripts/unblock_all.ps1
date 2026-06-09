<#
.SYNOPSIS
    Undo every form of network/web blocking ClassControl might have left
    on this Windows machine. Safe to re-run any time.

.DESCRIPTION
    1. Strips the managed block section from C:\Windows\System32\drivers\etc\hosts
       (with a timestamped backup).
    2. Searches for suspicious entries OUTSIDE the managed section and
       prints them so you can decide whether to remove by hand.
    3. Deletes every ClassControl Windows Firewall rule (the "ClassControl-Lockdown"
       group used by Internet Lockdown).
    4. Flushes the DNS resolver cache.
    5. Optionally clears the master's saved blocking.json with -ResetMaster.
    6. Runs a verification DNS query against google.com to prove it now
       resolves correctly.

    Run from an Administrator PowerShell.

.PARAMETER ResetMaster
    Also empties the master app's blocking.json so the next launch doesn't
    re-push the old list. Backup is written alongside.

.PARAMETER DryRun
    Print what would change without actually touching anything.

.EXAMPLE
    .\scripts\unblock_all.ps1
    Strip hosts entries + firewall rules + flush DNS.

.EXAMPLE
    .\scripts\unblock_all.ps1 -ResetMaster
    Same as above but also resets the master's saved block list.

.EXAMPLE
    .\scripts\unblock_all.ps1 -DryRun
    Dry run — show what would happen without changing anything.
#>

[CmdletBinding()]
param(
    [switch]$ResetMaster,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$HostsFile = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$HostsBegin = "# >>> classcontrol-block >>>"
$HostsEnd   = "# <<< classcontrol-block <<<"
$FirewallGroup = "ClassControl-Lockdown"

function Section($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Yellow }
function OK($t)      { Write-Host "  ok  " -ForegroundColor Green -NoNewline; Write-Host $t }
function Skip($t)    { Write-Host "  --  " -ForegroundColor DarkYellow -NoNewline; Write-Host $t }
function Bad($t)     { Write-Host "  !!  " -ForegroundColor Red -NoNewline; Write-Host $t }

# Admin check
$current = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Bad "This script needs Administrator privileges (to edit hosts + firewall)."
    Write-Host "Re-run from an elevated PowerShell:  Right-click → Windows Terminal (Admin)"
    exit 1
}

# ---------------------------------------------------------------------
# 1. hosts — managed section
# ---------------------------------------------------------------------
Section "hosts file — managed block section"
$hostsContent = ""
if (Test-Path $HostsFile) {
    $hostsContent = Get-Content $HostsFile -Raw
}
if ($hostsContent -match [regex]::Escape($HostsBegin)) {
    Write-Host "Found a ClassControl-managed section. Current entries:"
    $hostsLines = $hostsContent -split "`r?`n"
    $inside = $false
    foreach ($l in $hostsLines) {
        if ($l.Trim() -eq $HostsBegin) { $inside = $true; continue }
        if ($l.Trim() -eq $HostsEnd)   { $inside = $false; continue }
        if ($inside -and $l.Trim() -ne "") { Write-Host "    $l" }
    }
    if (-not $DryRun) {
        $stamp = (Get-Date).ToString("yyyyMMddHHmmss")
        Copy-Item $HostsFile "$HostsFile.bak.$stamp" -Force
        # Build new content with the managed section dropped
        $kept = New-Object System.Collections.Generic.List[string]
        $skip = $false
        foreach ($l in $hostsLines) {
            if ($l.Trim() -eq $HostsBegin) { $skip = $true; continue }
            if ($l.Trim() -eq $HostsEnd)   { $skip = $false; continue }
            if (-not $skip) { $kept.Add($l) | Out-Null }
        }
        [System.IO.File]::WriteAllText($HostsFile, ($kept -join "`r`n").TrimEnd() + "`r`n")
        OK "Removed the managed section. Backup at $HostsFile.bak.$stamp"
    } else {
        Skip "(dry-run; not modifying)"
    }
} else {
    OK "No ClassControl-managed section in hosts — already clean."
}

# ---------------------------------------------------------------------
# 2. hosts — suspicious entries OUTSIDE managed section
# ---------------------------------------------------------------------
Section "hosts file — anything else pointing localhost to common sites"
$pattern = '(google|youtube|facebook|twitter|tiktok|reddit|netflix|instagram|snapchat|twitch)'
$suspicious = @()
foreach ($l in (Get-Content $HostsFile -ErrorAction SilentlyContinue)) {
    if ($l -match '^\s*#') { continue }
    if ($l -match '^\s*(127\.|0\.0\.0\.0|::1)' -and $l -match $pattern) {
        $suspicious += $l
    }
}
if ($suspicious.Count -gt 0) {
    Bad "Found entries OUTSIDE the managed section that look like blocks:"
    foreach ($l in $suspicious) { Write-Host "    $l" }
    Write-Host ""
    Write-Host "These were NOT removed because they're outside the area" -ForegroundColor Yellow
    Write-Host "ClassControl owns. To remove by hand:" -ForegroundColor Yellow
    Write-Host "    notepad $HostsFile  (as Administrator)" -ForegroundColor Yellow
} else {
    OK "No common-site loopback entries found."
}

# ---------------------------------------------------------------------
# 3. Firewall — remove our group
# ---------------------------------------------------------------------
Section "Windows Defender Firewall — ClassControl-Lockdown rules"
$rules = Get-NetFirewallRule -Group $FirewallGroup -ErrorAction SilentlyContinue
if ($rules) {
    Write-Host "Found $($rules.Count) rule(s) in group '$FirewallGroup'."
    if (-not $DryRun) {
        Remove-NetFirewallRule -Group $FirewallGroup -ErrorAction SilentlyContinue
        OK "Removed every rule in group '$FirewallGroup'."
    } else {
        Skip "(dry-run; would remove $($rules.Count) rule(s))"
    }
} else {
    OK "No ClassControl-Lockdown firewall rules found."
}

# ---------------------------------------------------------------------
# 4. DNS cache
# ---------------------------------------------------------------------
Section "DNS resolver cache"
if (-not $DryRun) {
    & ipconfig /flushdns | Out-Null
    OK "DNS resolver cache flushed."
} else {
    Skip "(dry-run; would run: ipconfig /flushdns)"
}

# ---------------------------------------------------------------------
# 5. Optionally reset master's blocking.json
# ---------------------------------------------------------------------
if ($ResetMaster) {
    Section "Master block list"
    $masterBl = Join-Path $env:APPDATA "ClassControl\master\blocking.json"
    if (Test-Path $masterBl) {
        if (-not $DryRun) {
            $stamp = (Get-Date).ToString("yyyyMMddHHmmss")
            Copy-Item $masterBl "$masterBl.bak.$stamp" -Force
            $empty = @{
                apps_master = $false
                urls_master = $false
                apps = @{}
                urls = @{}
            } | ConvertTo-Json -Depth 3
            [System.IO.File]::WriteAllText($masterBl, $empty)
            OK "Reset $masterBl. Backup at $masterBl.bak.$stamp"
        } else {
            Skip "(dry-run; would clear $masterBl)"
        }
    } else {
        OK "No master blocking.json found — nothing to reset."
    }
}

# ---------------------------------------------------------------------
# 6. Verify
# ---------------------------------------------------------------------
Section "Verification — DNS query for google.com"
try {
    $resolved = Resolve-DnsName -Name google.com -ErrorAction Stop
    $resolved | Select-Object -First 3 NameHost, IPAddress, Name | Format-Table -AutoSize | Out-String | Write-Host
    $blocked = $resolved | Where-Object {
        $_.IPAddress -match '^127\.|^0\.0\.0\.0|^::1'
    }
    if ($blocked) {
        Bad "google.com STILL resolves to a loopback / null address."
        Write-Host ""
        Write-Host "Things to check next:" -ForegroundColor Yellow
        Write-Host "  - Your browser may have its own DNS cache - quit + relaunch it."
        Write-Host "    Chrome:  chrome://net-internals/#dns -> Clear host cache."
        Write-Host "  - Examine the hosts file by hand:  notepad $HostsFile"
        Write-Host "  - Check for other blockers: parental controls, Pi-Hole on your"
        Write-Host "    router, NextDNS, AV / endpoint protection web filters."
    } else {
        OK "google.com resolves to a real public IP — looks unblocked."
    }
} catch {
    Bad "DNS query failed: $_"
}

Write-Host ""
OK "Done."
