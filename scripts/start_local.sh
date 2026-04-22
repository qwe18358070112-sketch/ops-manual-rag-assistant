#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
HOST="${OPS_ASSISTANT_HOST:-127.0.0.1}"
PORT="${OPS_ASSISTANT_PORT:-8000}"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
