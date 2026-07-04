#!/usr/bin/env bash
# Radiology AI Assistant launcher (macOS / Linux). Runs everything locally.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies (local only)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Warn if the local model runtime is not reachable.
if ! curl -s http://localhost:11434/v1/models >/dev/null 2>&1; then
  echo "WARNING: Ollama does not appear to be running on :11434."
  echo "Start it with 'ollama serve' and pull a model, e.g. 'ollama pull gpt-oss:20b'."
fi

HOST="${RADIOLOGY_AI_HOST:-127.0.0.1}"
PORT="${RADIOLOGY_AI_PORT:-8000}"
echo "Radiology AI Assistant running at http://$HOST:$PORT  (local only)"
exec python -m uvicorn backend.app:app --host "$HOST" --port "$PORT"
