# Build both ClassControl Windows executables.
#
# Usage (from project root, PowerShell):
#     ./scripts/build_windows.ps1
#
# Output:
#     dist\ClassControlTeacher\ClassControlTeacher.exe
#     dist\ClassControlClient\ClassControlClient.exe

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment in .venv\..."
    python -m venv .venv
}
# . ./.venv/bin/Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

if (Test-Path "build") { Remove-Item -Recurse -Force build }
if (Test-Path "dist")  { Remove-Item -Recurse -Force dist  }

Write-Host "Building teacher app..."
pyinstaller --noconfirm packaging\classcontrol_teacher.spec

Write-Host "Building client daemon..."
pyinstaller --noconfirm packaging\classcontrol_client.spec

Write-Host ""
Write-Host "Done. Built:"
Get-ChildItem dist
