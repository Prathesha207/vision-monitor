#!/usr/bin/env bash
# Start backend and frontend together for Linux development.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$BACKEND_DIR/../vision-ai-frontend" && pwd)"

if [[ ! -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  echo "Backend environment is missing. Run ./setup_linux.sh first."
  exit 1
fi

if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]]; then
  echo "Frontend dependencies are missing. Run ./setup_linux.sh first."
  echo "Or run: cd $FRONTEND_DIR && npm install --include=optional"
  exit 1
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$BACKEND_DIR"
.venv/bin/python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

cd "$FRONTEND_DIR"
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!

wait
