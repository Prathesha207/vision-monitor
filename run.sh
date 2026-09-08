#!/usr/bin/env bash
set -e

echo "Starting Vision Monitor Development Servers..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR/vision-ai-backend"
chmod +x run_dev_linux.sh
./run_dev_linux.sh
