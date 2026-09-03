#!/usr/bin/env bash
# Install the backend and frontend correctly on a Linux development machine.
# Run from vision-ai-backend after both sibling folders are present.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$BACKEND_DIR/../vision-ai-frontend" && pwd)"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "vision-ai-frontend must be beside vision-ai-backend."
  exit 1
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$BACKEND_DIR/requirements.txt"

# The base requirements remain portable. Replace generic torch with CUDA torch
# only when this Linux machine has an NVIDIA driver and GPU.
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU detected; installing CUDA-enabled PyTorch..."
  python -m pip install --force-reinstall \
    --index-url https://download.pytorch.org/whl/cu121 \
    torch==2.5.1+cu121 torchvision==0.20.1+cu121
else
  echo "No NVIDIA GPU detected; keeping CPU-compatible PyTorch."
fi

python -m pip install "$BACKEND_DIR/app/ml/duck_analyzer-1.0.8-py3-none-any.whl"

# Native Rollup/Vite modules must be installed on this exact Linux architecture.
cd "$FRONTEND_DIR"
rm -rf node_modules
npm install --include=optional

python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
echo "Setup complete. Start development with: $BACKEND_DIR/run_dev_linux.sh"
