param(
    [switch]$Launch,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Write-Host ""
Write-Host "TaskFlow environment is ready."
Write-Host "Run the app with: .\.venv\Scripts\python.exe main.py"
Write-Host "Build the desktop app with: .\build_exe.ps1"

if ($Build) {
    & .\build_exe.ps1
}

if ($Launch) {
    & .\.venv\Scripts\python.exe main.py
}
