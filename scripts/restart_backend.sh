#!/usr/bin/env bash
# Stop whatever is on :8000 and start Meridian API (picks up new .env keys).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  kill "$(lsof -t -i:8000)" 2>/dev/null || true
fi

sleep 1
echo "Starting API from $ROOT (reloads .env on each process start)"
exec uvicorn main:app --host 127.0.0.1 --port 8000 --reload
