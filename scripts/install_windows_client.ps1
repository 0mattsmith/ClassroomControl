<#
.SYNOPSIS
    Register the ClassControl client as an auto-starting "service-like"
    scheduled task on a Windows student machine.

.DESCRIPTION
    Creates a scheduled task that:
      * Runs **as the currently logged-in user** (BUILTIN\Users group)
        — so PyQt overlays and message popups appear in the student's
        session. SYSTEM-account services live in Session 0 and cannot
        display UI to the user, so we deliberately don't use SYSTEM.
      * Triggers **At log on** — the daemon starts the moment the
        student signs in. (Also: At startup is no good for us because
        no user session exists yet.)
      * Has **highest privileges** — auto-elevates without prompting
        the student for UAC, because YOU (an admin) authorised the
        task at install time. This is the same trick AutoUpdater and
        many enterprise apps use.
      * Runs **hidden** (no console window) and **auto-restarts** if
        it ever exits unexpectedly.

    The task installs under the name ``ClassControlClient`` — view it
    in Task Scheduler under "Task Scheduler Library".

    For a full Windows Service (Session 0 daemon + per-session GUI
    workers via WTSCreateProcessAsUser) the architecture would need
    splitting into separate service + worker binaries — see the
    README. The scheduled task is what most enterprise apps actually
    use because it's simpler and works.

.PARAMETER InstallPath
    Directory containing ClassControlClient.exe. Default:
    ``C:\Program Files\ClassControl\ClassControlClient``.

.PARAMETER TaskName
    Scheduled task name. Default: ``ClassControlClient``.

.EXAMPLE
    .\scripts\install_windows_client.ps1
    Install the task with default paths. Run from an Administrator
    PowerShell.

.EXAMPLE
    .\scripts\install_windows_client.ps1 -InstallPath "D:\Apps\ClassControlClient"
    Use a non-default install location.

#>

[CmdletBinding()]
param(
    [string]$InstallPath = "C:\Program Files\ClassControl\ClassControlClient",
    [string]$TaskName    = "ClassControlClient"
)

$ErrorActionPreference = "Stop"

$exe = Join-Path $InstallPath "ClassControlClient.exe"
if (-not (Test-Path $exe)) {
    throw "Cannot find $exe. Build with build_windows.ps1 first and place the dist\ClassControlClient folder at $InstallPath."
}

# ---------------------------------------------------------------------
# Action — what the task runs.
# ---------------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $exe

# ---------------------------------------------------------------------
# Trigger — at every logon, any user.
# ---------------------------------------------------------------------
$trigger = New-ScheduledTaskTrigger -AtLogOn

# ---------------------------------------------------------------------
# Principal — run as the logged-in user with highest privileges.
#
# Using the BUILTIN\Users group + InteractiveToken means the task runs
# in the user's session (so PyQt overlays render) but elevated to
# admin (so pfctl/hosts/firewall/shutdown all succeed). The student
# is NOT prompted for UAC at logon because the task itself was
# authorised by an admin (you, when you ran this script).
# ---------------------------------------------------------------------
$principal = New-ScheduledTaskPrincipal `
    -GroupId "BUILTIN\Users" `
    -RunLevel Highest

# ---------------------------------------------------------------------
# Settings — hidden, auto-restart, no battery / on-demand limits.
# ---------------------------------------------------------------------
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -Hidden `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no time limit

# ---------------------------------------------------------------------
# (Re)register the task.
# ---------------------------------------------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description "ClassControl student client (auto-elevated, at logon)" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings | Out-Null

Write-Host "[+] Task '$TaskName' registered." -ForegroundColor Green
Write-Host "    Runs:        the logged-in user, with highest privileges"
Write-Host "    Trigger:     at every logon"
Write-Host "    Visibility:  hidden (no console window)"
Write-Host "    Restart:     yes, after 1 minute, up to 999 times"
Write-Host ""
Write-Host "The client will start automatically at the next logon."
Write-Host "To start it now without logging out, run:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
