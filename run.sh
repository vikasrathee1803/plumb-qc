#!/usr/bin/env bash
# One-click launcher for Plumb (macOS / Linux, runs from a source checkout).
# Builds the web UI and installs Python deps on first run, then starts the app
# and opens your browser. Run: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

if [ ! -f "web/ui/dist/index.html" ]; then
  echo "Building the web UI (first run only)..."
  (cd web/ui && npm install && npm run build)
fi

if ! "$PY" -c "import uvicorn, fastapi, plumb" >/dev/null 2>&1; then
  echo "Installing Python dependencies (first run only)..."
  "$PY" -m pip install -e .
fi

echo "Starting Plumb at http://127.0.0.1:8000  (Ctrl-C to stop)"
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:8000) || \
  (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000) || true ) &
PYTHONPATH="$(pwd)" "$PY" -m uvicorn web.api.app:app --host 127.0.0.1 --port 8000
