#!/usr/bin/env bash
set -euo pipefail

echo "======================================================="
echo "     Vision Monitor - CLEAN Build (Linux Desktop App)"
echo "======================================================="
echo ""
echo "This will clear ALL build caches and create a fresh build."
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

echo "[1/5] Stopping stale processes..."
pkill -f "vision-monitor" 2>/dev/null || true
pkill -f "dist/backend/backend" 2>/dev/null || true
sleep 1

echo "[2/5] Clearing Python bytecache..."
find "$SCRIPT_DIR/vision-ai-backend/app" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$SCRIPT_DIR/vision-ai-backend/app" -type f -name "*.pyc" -delete 2>/dev/null || true

echo "[3/5] Clearing PyInstaller build artifacts..."
rm -rf "$SCRIPT_DIR/vision-ai-backend/build" "$SCRIPT_DIR/vision-ai-backend/dist"

echo "[4/5] Clearing Electron and Vite build artifacts..."
rm -rf "$SCRIPT_DIR/vision-ai-frontend/dist" "$SCRIPT_DIR/vision-ai-frontend/dist_app"

echo "[5/5] Starting fresh Linux desktop build..."
echo ""
chmod +x "$SCRIPT_DIR/build.sh" "$SCRIPT_DIR/vision-ai-backend/build_linux_desktop.sh"
exec "$SCRIPT_DIR/build.sh"
