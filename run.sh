#!/usr/bin/env bash
set -e

echo "Starting Duck Monitor Development Servers..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR/backend"
chmod +x run_dev_linux.sh
./run_dev_linux.sh
