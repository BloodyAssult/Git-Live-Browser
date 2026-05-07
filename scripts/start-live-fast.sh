#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
python -m uvicorn server.app:app --host 0.0.0.0 --port 8787
