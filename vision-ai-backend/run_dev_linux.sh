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

# Configurable base port (default 8000, or override with BACKEND_PORT=8080)
TARGET_PORT="${BACKEND_PORT:-${PORT:-8000}}"

# Function to check if a port is available without killing existing processes
is_port_free() {
  "$BACKEND_DIR/.venv/bin/python" -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('0.0.0.0', int(sys.argv[1])))
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
" "$1"
}

# Auto-select an available port if the target port is already in use by another service
PORT="$TARGET_PORT"
while ! is_port_free "$PORT"; do
  echo "Port $PORT is already in use by another application. Trying port $((PORT + 1))..."
  PORT=$((PORT + 1))
  if [[ "$PORT" -gt "$((TARGET_PORT + 50))" ]]; then
    echo "ERROR: Could not find an open port in range $TARGET_PORT-$PORT."
    exit 1
  fi
done

if [[ "$PORT" != "$TARGET_PORT" ]]; then
  echo "Note: Port $TARGET_PORT was busy. Using available port $PORT for backend."
fi

cleanup() {
  echo ""
  echo "Stopping development servers..."
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$BACKEND_DIR"
.venv/bin/python -m uvicorn app.main:app --reload --port "$PORT" &
BACKEND_PID=$!

cd "$FRONTEND_DIR"
VITE_API_BASE_URL="http://localhost:$PORT" npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!

echo ""
echo "=================================================="
echo " Vision AI Dev Servers Started"
echo " Backend:  http://localhost:$PORT"
echo " Frontend: http://localhost:5173"
echo "=================================================="
echo ""

wait
