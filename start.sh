#!/usr/bin/env bash
# Linux/macOS launcher (replay mode works everywhere; live capture targets Windows first).
set -euo pipefail
cd "$(dirname "$0")"
[ -d backend/.venv ] || python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -q -r backend/requirements.txt
[ -d frontend/dist ] || (cd frontend && npm install --no-audit --no-fund && npm run build)
[ -f .env ] || cp .env.example .env
exec backend/.venv/bin/python backend/run.py
