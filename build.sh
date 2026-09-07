#!/usr/bin/env bash
set -e

echo "======================================================="
echo "        Vision AI Build (Linux Desktop App)"
echo "======================================================="

cd vision-ai-backend
chmod +x build_linux_desktop.sh
./build_linux_desktop.sh

echo ""
echo "======================================================="
echo "Build completed successfully!"
echo "You can find the AppImage and .deb files in:"
echo "vision-ai-frontend/dist_app"
echo "======================================================="
