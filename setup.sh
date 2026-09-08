#!/usr/bin/env bash
set -e

echo "======================================================="
echo "           Vision Monitor Setup (Linux)"
echo "======================================================="
echo ""

cd vision-ai-backend
chmod +x setup_linux.sh
./setup_linux.sh

echo ""
echo "======================================================="
echo "Setup completed successfully!"
echo "To start the application, simply execute ./run.sh"
echo "======================================================="
