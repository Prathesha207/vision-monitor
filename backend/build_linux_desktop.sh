#!/usr/bin/env bash
# Build Linux desktop artifacts from a native Ubuntu/Debian machine.
# Run this script from backend or repo root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$SCRIPT_DIR/run.py" ]]; then
  BACKEND_DIR="$SCRIPT_DIR"
elif [[ -f "$PWD/backend/run.py" ]]; then
  BACKEND_DIR="$(cd "$PWD/backend" && pwd -P)"
elif [[ -f "$PWD/run.py" ]]; then
  BACKEND_DIR="$PWD"
else
  echo "Error: Cannot locate backend directory containing run.py."
  exit 1
fi

if [[ -d "$BACKEND_DIR/../frontend" ]]; then
  FRONTEND_DIR="$(cd "$BACKEND_DIR/../frontend" && pwd -P)"
elif [[ -d "$PWD/frontend" ]]; then
  FRONTEND_DIR="$(cd "$PWD/frontend" && pwd -P)"
else
  echo "Error: Cannot locate frontend directory."
  exit 1
fi

VENV_DIR="$BACKEND_DIR/.venv-linux-build"
MACHINE_ARCH="$(uname -m)"

case "$MACHINE_ARCH" in
  x86_64) ELECTRON_ARCH="x64" ;;
  aarch64|arm64) ELECTRON_ARCH="arm64" ;;
  *) echo "Unsupported Linux architecture: $MACHINE_ARCH"; exit 1 ;;
esac

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "frontend must be beside backend."
  exit 1
fi

if [[ -d "$BACKEND_DIR/.venv" && -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  echo "Using verified backend virtual environment ($BACKEND_DIR/.venv)..."
  VENV_DIR="$BACKEND_DIR/.venv"
else
  echo "Creating Linux build virtual environment ($VENV_DIR)..."
  python3 -m venv --system-site-packages "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip "setuptools<80" wheel

# 1. Install CUDA PyTorch & torchvision FIRST (before requirements.txt)
if [[ "${USE_CUDA:-0}" == "1" || ( "${USE_CUDA:-auto}" == "auto" && -n "$(command -v nvidia-smi 2>/dev/null || true)" ) ]]; then
  echo "NVIDIA GPU detected/requested; verifying CUDA-enabled PyTorch..."
  if python -c "import torch, torchvision; x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
    echo "CUDA PyTorch is already operational: $(python -c 'import torch; print(torch.__version__, torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")')"
  elif [[ "$MACHINE_ARCH" == "aarch64" || "$MACHINE_ARCH" == "arm64" ]]; then
    echo "ARM64 detected without vendor CUDA PyTorch; installing standard PyTorch."
    python -m pip install --prefer-binary torch torchvision 2>/dev/null || true
  else
    TORCH_OK=0
    if [[ -n "${PYTORCH_CUDA_INDEX:-}" ]]; then
      echo "Installing CUDA PyTorch from custom index $PYTORCH_CUDA_INDEX..."
      if python -m pip install --prefer-binary --index-url "$PYTORCH_CUDA_INDEX" torch==2.15.0 torchvision==0.29.2 \
         || python -m pip install --prefer-binary --index-url "$PYTORCH_CUDA_INDEX" torch torchvision; then
        TORCH_OK=1
      fi
    else
      for ENTRY in \
        "https://download.pytorch.org/whl/cu132|torch==2.15.0 torchvision==0.29.2" \
        "https://download.pytorch.org/whl/cu130|torch torchvision" \
        "https://download.pytorch.org/whl/cu128|torch torchvision" \
        "https://download.pytorch.org/whl/cu124|torch torchvision"; do
        IDX="${ENTRY%%|*}"
        PKGS="${ENTRY##*|}"
        echo "Installing CUDA PyTorch ($PKGS) from $IDX..."
        # shellcheck disable=SC2086
        if python -m pip install --prefer-binary --index-url "$IDX" $PKGS; then
          if python -c "import torch; assert torch.cuda.is_available(); x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
            TORCH_OK=1
            break
          fi
        fi
      done
    fi
    if [[ "$TORCH_OK" -ne 1 ]]; then
      echo "ERROR: Could not install a working CUDA PyTorch build."
      exit 1
    fi
  fi
else
  echo "Building with CPU-compatible PyTorch. Set USE_CUDA=1 to force CUDA."
  python -m pip install --prefer-binary torch torchvision
fi

# 2. Install backend + TrayCount ML requirements
python -m pip install --prefer-binary -r "$BACKEND_DIR/requirements.txt"

# 3. Install bundled TrayCount wheel
TRAYCOUNT_WHEEL="$(find "$BACKEND_DIR/app/ml/whl" "$BACKEND_DIR/app/ml" -maxdepth 2 -name 'traycount-*.whl' -print 2>/dev/null | sort -V -r | head -n 1)"
[[ -n "$TRAYCOUNT_WHEEL" ]] || { echo "The bundled traycount wheel is missing from app/ml/whl."; exit 1; }
python -m pip install --no-deps --force-reinstall "$TRAYCOUNT_WHEEL"

cd "$BACKEND_DIR"
rm -rf "$BACKEND_DIR/build" "$BACKEND_DIR/dist"

DATA_ARGS=()
for DATA_DIR in "app/ml/config" "app/ml/weights" "app/ml/model" "alembic"; do
  if [[ -d "$BACKEND_DIR/$DATA_DIR" ]]; then
    DATA_ARGS+=(--add-data "$BACKEND_DIR/$DATA_DIR:$DATA_DIR")
  fi
done

pyinstaller --noconfirm --clean --onedir --name backend "$BACKEND_DIR/run.py" \
  --distpath "$BACKEND_DIR/dist" \
  --workpath "$BACKEND_DIR/build" \
  --specpath "$BACKEND_DIR" \
  "${DATA_ARGS[@]}" \
  --copy-metadata torchvision \
  --copy-metadata ultralytics \
  --copy-metadata timm \
  --copy-metadata safetensors \
  --copy-metadata huggingface_hub \
  --collect-all app \
  --collect-all fastapi \
  --collect-all starlette \
  --collect-all uvicorn \
  --collect-all sqlalchemy \
  --collect-all cv2 \
  --collect-all torch \
  --collect-all torchvision \
  --collect-all ultralytics \
  --collect-all depthai \
  --collect-all av \
  --collect-all mediapipe \
  --collect-all scipy \
  --collect-all imageio_ffmpeg \
  --collect-all timm \
  --collect-all safetensors \
  --collect-all huggingface_hub \
  --collect-all traycount

# Strip non-runtime development files directly in dist/backend
chmod +x "$BACKEND_DIR/dist/backend/backend"
find "$BACKEND_DIR/dist/backend" -name "*.a" -delete 2>/dev/null || true
rm -rf "$BACKEND_DIR/dist/backend/_internal/torch/include" 2>/dev/null || true
rm -rf "$BACKEND_DIR/dist/backend/_internal/triton" 2>/dev/null || true

cd "$FRONTEND_DIR"
npm ci --include=optional 2>/dev/null || npm install --include=optional
npm run "package:linux:$ELECTRON_ARCH"

echo
echo "Linux $ELECTRON_ARCH artifacts are in: $FRONTEND_DIR/dist_app"
