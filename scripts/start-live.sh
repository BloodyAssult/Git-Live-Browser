#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install --with-deps chromium

if [[ "${1:-}" == "--install-only" ]]; then
  echo "✅ Dependencies installed."
  exit 0
fi

export DOWNLOAD_DIR="${DOWNLOAD_DIR:-downloads}"
mkdir -p "$DOWNLOAD_DIR"

echo ""
echo "✅ Git Live Browser is starting on port 8787"
echo "Open this from VS Code Desktop forwarded port: http://127.0.0.1:8787"
echo ""
python -m uvicorn server.app:app --host 0.0.0.0 --port 8787
