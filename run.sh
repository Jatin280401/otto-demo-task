#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "ERROR: .venv/bin/activate not found. Run: python -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

if command -v ollama &>/dev/null; then
  if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Starting ollama server…"
    ollama serve &
    sleep 3
  fi
fi

if [ -n "${OTTO_PUBLIC_URL:-}" ]; then
  exec .venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --reload \
    --access-log \
    --env OTTO_PUBLIC_URL="${OTTO_PUBLIC_URL}"
else
  exec .venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --reload \
    --access-log
fi
