#!/usr/bin/env bash
# One-click installer and builder for Vision AI on Linux
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "========================================================"
echo "  🚀 Vision AI - One-Step Linux Setup & App Builder     "
echo "========================================================"

# Sudo is NOT needed. Everything builds in user space (virtualenv + npm).


# 3. Make inner scripts executable
chmod +x "$ROOT_DIR/vision-ai-backend/build_linux_desktop.sh"
chmod +x "$ROOT_DIR/vision-ai-backend/setup_linux.sh"

# 4. Build desktop application with CUDA auto-detection
echo "Starting desktop application build..."
USE_CUDA="${USE_CUDA:-auto}" "$ROOT_DIR/vision-ai-backend/build_linux_desktop.sh"

echo
echo "========================================================"
echo "  🎉 SUCCESS! Your Linux App is ready to use:           "
echo "========================================================"
find "$ROOT_DIR/vision-ai-frontend/dist_app" -name "*.AppImage" -exec ls -lh {} + 2>/dev/null || true
echo
echo "To run it, simply execute:"
echo "  ./vision-ai-frontend/dist_app/Vision-AI-*.AppImage"
echo "========================================================"
