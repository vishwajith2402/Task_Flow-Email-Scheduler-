$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller .\TaskFlow.spec --noconfirm --clean

Write-Host "Build complete. Executable available in .\dist\TaskFlow\"
