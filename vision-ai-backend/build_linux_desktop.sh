#!/usr/bin/env bash
# Build Linux desktop artifacts from a native Ubuntu/Debian machine.
# Run this script from vision-ai-backend after cloning BOTH sibling folders:
#   vision-ai-backend/ and vision-ai-frontend/
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$BACKEND_DIR/../vision-ai-frontend" && pwd)"
VENV_DIR="$BACKEND_DIR/.venv-linux-build"
MACHINE_ARCH="$(uname -m)"

case "$MACHINE_ARCH" in
  x86_64) ELECTRON_ARCH="x64" ;;
  aarch64|arm64) ELECTRON_ARCH="arm64" ;;
  *) echo "Unsupported Linux architecture: $MACHINE_ARCH"; exit 1 ;;
esac

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "vision-ai-frontend must be beside vision-ai-backend."
  exit 1
fi

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$BACKEND_DIR/requirements.txt"

if [[ "${USE_CUDA:-0}" == "1" || ( "${USE_CUDA:-auto}" == "auto" && -n "$(command -v nvidia-smi 2>/dev/null || true)" ) ]]; then
  echo "NVIDIA GPU detected/requested; installing CUDA-enabled PyTorch..."
  if [[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]]; then
    if python -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
      echo "ARM64 vendor PyTorch with CUDA is already installed; keeping it."
    else
      echo "ARM64 detected without vendor CUDA PyTorch; building with CPU PyTorch."
    fi
  else
    PYTORCH_CUDA_INDEX="${PYTORCH_CUDA_INDEX:-https://download.pytorch.org/whl/cu121}"
    python -m pip install --force-reinstall \
      --index-url "$PYTORCH_CUDA_INDEX" \
      torch torchvision
  fi
else
  echo "Building with CPU-compatible PyTorch. Set USE_CUDA=1 to force CUDA."
fi
DUCK_ANALYZER_WHEEL="$(find "$BACKEND_DIR/app/ml" -maxdepth 1 -name 'duck_analyzer-*.whl' -print | sort -r | head -n 1)"
[[ -n "$DUCK_ANALYZER_WHEEL" ]] || { echo "The bundled duck_analyzer wheel is missing."; exit 1; }
python -m pip install "$DUCK_ANALYZER_WHEEL"

cd "$BACKEND_DIR"
rm -rf build dist
pyinstaller --noconfirm --clean --onedir --name backend run.py \
  --add-data "app/ml/models:app/ml/models" \
  --add-data "app/ml/config.yaml:app/ml" \
  --add-data "alembic:alembic" \
  --collect-all app \
  --collect-all fastapi \
  --collect-all starlette \
  --collect-all uvicorn \
  --collect-all sqlalchemy \
  --collect-all cv2 \
  --collect-all torch \
  --collect-all torchvision \
  --collect-all ultralytics \
  --collect-all segmentation_models_pytorch \
  --collect-all depthai \
  --collect-all av \
  --collect-all duck_analyzer \
  --collect-all mediapipe \
  --collect-all matplotlib

# Keep release files separate from developer/runtime data in frontend/backend.
# Electron maps this directory to resources/backend inside each installer.
rm -rf "$FRONTEND_DIR/release-backend"
mkdir -p "$FRONTEND_DIR/release-backend"
cp -a "$BACKEND_DIR/dist/backend/." "$FRONTEND_DIR/release-backend/"
chmod +x "$FRONTEND_DIR/release-backend/backend"

cd "$FRONTEND_DIR"
npm ci --include=optional
npm run "package:linux:$ELECTRON_ARCH"

echo
echo "Linux $ELECTRON_ARCH artifacts are in: $FRONTEND_DIR/dist_app"
