#!/usr/bin/env bash
set -e

echo "======================================================="
echo "        Duck Monitor - Clean Setup (Linux)"
echo "======================================================="
echo ""
echo "This will remove the existing Python virtual environment (.venv)"
echo "and frontend dist build, then perform a fresh setup from scratch."
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR/backend"
chmod +x setup_linux.sh
./setup_linux.sh --clean

echo ""
echo "======================================================="
echo "Clean setup completed successfully!"
echo "To start the application, simply execute ./run.sh"
echo "======================================================="
