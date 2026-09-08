#!/usr/bin/env bash
# One-click installer and builder for Vision Monitor on Linux
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ ! -f "$ROOT_DIR/vision-ai-backend/run.py" && -f "$PWD/vision-ai-backend/run.py" ]]; then
  ROOT_DIR="$PWD"
fi

echo "========================================================"
echo "  🚀 Vision Monitor - One-Step Linux Setup & App Builder "
echo "========================================================"

# Make inner scripts executable
chmod +x "$ROOT_DIR/vision-ai-backend/build_linux_desktop.sh"
chmod +x "$ROOT_DIR/vision-ai-backend/setup_linux.sh"
chmod +x "$ROOT_DIR/vision-ai-backend/run_dev_linux.sh"

MODE="${1:-app}"

if [[ "$MODE" == "--dev" || "$MODE" == "dev" ]]; then
  echo "Starting Vision Monitor development environment..."
  exec "$ROOT_DIR/vision-ai-backend/run_dev_linux.sh"
fi

if [[ "$MODE" == "--setup" || "$MODE" == "setup" ]]; then
  echo "Running Vision Monitor setup..."
  exec "$ROOT_DIR/vision-ai-backend/setup_linux.sh"
fi

# Run build script inside vision-ai-backend
cd "$ROOT_DIR/vision-ai-backend"
echo "Starting desktop application build..."
USE_CUDA="${USE_CUDA:-auto}" "$ROOT_DIR/vision-ai-backend/build_linux_desktop.sh"

echo
echo "========================================================"
echo "  🎉 SUCCESS! Your Linux App is ready to use:           "
echo "========================================================"
find "$ROOT_DIR/vision-ai-frontend/dist_app" -name "*.AppImage" -exec ls -lh {} + 2>/dev/null || true
find "$ROOT_DIR/vision-ai-frontend/dist_app" -name "*.deb" -exec ls -lh {} + 2>/dev/null || true
echo
echo "To run the AppImage, simply execute:"
echo "  chmod +x ./vision-ai-frontend/dist_app/Vision-Monitor-*.AppImage"
echo "  ./vision-ai-frontend/dist_app/Vision-Monitor-*.AppImage"
echo
echo "Or install the Debian package:"
echo "  sudo dpkg -i ./vision-ai-frontend/dist_app/vision-monitor_*.deb"
echo "========================================================"
