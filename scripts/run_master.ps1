# Launch the ClassControl teacher app from source on Windows.
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    . ./.venv/bin/Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
} else {
    . ./.venv/bin/Activate.ps1
}
python -m master.app @args
