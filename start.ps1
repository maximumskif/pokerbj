# Windows launcher: creates the venv, installs deps, builds the dashboard, starts on 127.0.0.1.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path backend\.venv)) { py -3.12 -m venv backend\.venv }
backend\.venv\Scripts\python -m pip install -q -r backend\requirements.txt
if (-not (Test-Path frontend\dist)) { Push-Location frontend; npm install --no-audit --no-fund; npm run build; Pop-Location }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
backend\.venv\Scripts\python backend\run.py
