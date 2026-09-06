#!/usr/bin/env bash
# One-click installer and builder for Vision AI on Linux
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "========================================================"
echo "  🚀 Vision AI - One-Step Linux Setup & App Builder     "
echo "========================================================"

# 1. Install required system libraries if apt is available
if command -v apt-get >/dev/null 2>&1; then
  MISSING_PKGS=()
  for pkg in build-essential python3 python3-venv python3-pip nodejs npm libgl1 libglib2.0-0 libusb-1.0-0; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      MISSING_PKGS+=("$pkg")
    fi
  done

  if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Installing missing system dependencies: ${MISSING_PKGS[*]}..."
    sudo apt-get update -y
    sudo apt-get install -y "${MISSING_PKGS[@]}"
  fi
fi

# 2. Configure OAK-D camera USB access permissions if missing
if [ ! -f /etc/udev/rules.d/80-movidius.rules ]; then
  echo "Configuring OAK-D camera USB permissions..."
  echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules >/dev/null
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger || true
fi

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
