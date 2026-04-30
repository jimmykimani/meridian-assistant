#!/usr/bin/env bash
# Free port 8000 and start THIS repo's FastAPI (fixes 404 on /auth/verify).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  kill $(lsof -t -i:8000) 2>/dev/null || true
fi

echo "Starting Meridian API from: $ROOT"
exec uvicorn main:app --host 127.0.0.1 --port 8000 --reload
