#!/usr/bin/env bash
# Install the backend and frontend on a Linux development machine.
# Run from vision-ai-backend (or via root ./setup.sh / ./clean_setup.sh).
# Pass --clean or -c to remove .venv and frontend dist before reinstalling.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$BACKEND_DIR/../vision-ai-frontend" && pwd)"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "vision-ai-frontend must be beside vision-ai-backend."
  exit 1
fi

CLEAN_SETUP=0
if [[ "${1:-}" == "--clean" || "${1:-}" == "-c" ]]; then
  CLEAN_SETUP=1
  echo "[CLEAN] Clean setup requested. Stopping any running backend processes..."
  pkill -f "$VENV_DIR" 2>/dev/null || true
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 0.5
  echo "[CLEAN] Removing existing .venv and frontend dist..."
  rm -rf "$VENV_DIR"
  rm -rf "$FRONTEND_DIR/dist"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  VENV_FLAGS=""
  if [[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]]; then
    VENV_FLAGS="--system-site-packages"
  fi
  "$PYTHON_BIN" -m venv $VENV_FLAGS "$VENV_DIR" || {
    echo "ERROR: Failed to create Python virtual environment."
    echo "On Ubuntu/Debian, install the required packages:"
    echo "  sudo apt update && sudo apt install -y python3-venv python3-pip python3-dev"
    exit 1
  }
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

# 1. Install CUDA PyTorch & torchvision FIRST (before requirements.txt so PyPI never pulls CPU torch)
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU detected; verifying CUDA-enabled PyTorch..."
  if python -c "import torch, torchvision; x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
    echo "CUDA-enabled PyTorch is already installed and verified; GPU inference is available."
  else
    TORCH_OK=0
    if [[ -n "${PYTORCH_CUDA_INDEX:-}" ]]; then
      echo "Installing CUDA PyTorch from custom index $PYTORCH_CUDA_INDEX..."
      if python -m pip install --prefer-binary --index-url "$PYTORCH_CUDA_INDEX" torch torchvision; then
        if python -c "import torch; assert torch.cuda.is_available(); x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
          TORCH_OK=1
        fi
      fi
    else
      # Try official PyTorch CUDA indices from newest to older LTS releases
      for ENTRY in \
        "https://download.pytorch.org/whl/cu128|torch torchvision" \
        "https://download.pytorch.org/whl/cu126|torch torchvision" \
        "https://download.pytorch.org/whl/cu124|torch torchvision" \
        "https://download.pytorch.org/whl/cu121|torch torchvision"; do
        IDX="${ENTRY%%|*}"
        PKGS="${ENTRY##*|}"
        echo "Attempting CUDA PyTorch install ($PKGS) from $IDX..."
        # shellcheck disable=SC2086
        if python -m pip install --prefer-binary --index-url "$IDX" $PKGS; then
          if python -c "import torch; assert torch.cuda.is_available(); x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
            TORCH_OK=1
            echo "CUDA kernels verified successfully from $IDX."
            break
          fi
          echo "Installed from $IDX but CUDA kernel check failed; trying next fallback..."
        fi
      done
    fi

    # Fallback for Jetson / specialized ARM platforms if PyTorch index didn't build CUDA kernels
    if [[ "$TORCH_OK" -ne 1 && ("$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64") ]]; then
      echo "Testing ARM64 standard PyTorch package..."
      if python -m pip install --prefer-binary torch torchvision; then
        if python -c "import torch; assert torch.cuda.is_available(); x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0)" 2>/dev/null; then
          TORCH_OK=1
        fi
      fi
    fi

    if [[ "$TORCH_OK" -ne 1 ]]; then
      echo "ERROR: Could not install a working CUDA PyTorch build."
      echo "Retry with a specific index: PYTORCH_CUDA_INDEX=https://download.pytorch.org/whl/cu128 ./setup_linux.sh"
      exit 1
    fi
  fi
else
  echo "No NVIDIA GPU detected; installing CPU PyTorch (note: TrayCount inference requires a CUDA GPU)."
  python -m pip install --prefer-binary torch torchvision
fi

# 2. Install backend + TrayCount ML dependencies
python -m pip install --prefer-binary -r "$BACKEND_DIR/requirements.txt"

# 3. Install bundled TrayCount wheel
TRAYCOUNT_WHEEL="$(find "$BACKEND_DIR/app/ml/whl" "$BACKEND_DIR/app/ml" -maxdepth 2 -name 'traycount-*.whl' -print 2>/dev/null | sort -V -r | head -n 1)"
if [[ -z "$TRAYCOUNT_WHEEL" ]]; then
  echo "ERROR: The bundled traycount wheel is missing from app/ml/whl."
  exit 1
fi
echo "Installing $(basename "$TRAYCOUNT_WHEEL")..."
python -m pip install --no-deps --force-reinstall "$TRAYCOUNT_WHEEL"

# 4. Pre-flight smoke test
python -c "
import torch, torchvision, timm, safetensors, huggingface_hub, mediapipe, importlib.metadata as md
from ultralytics import YOLO
from traycount import TrayCounter
print('PyTorch      :', torch.__version__)
print('TorchVision  :', torchvision.__version__)
print('TrayCount    :', md.version('traycount'))
print('CUDA Active  :', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU Device   :', torch.cuda.get_device_name(0))
"

# Check Luxonis OAK camera udev rules & headless MediaPipe OpenGL library on Linux
if [[ ! -f /etc/udev/rules.d/80-movidius.rules ]]; then
  echo "--------------------------------------------------------"
  echo "NOTE: DepthAI OAK camera USB udev rules not detected."
  echo "If using USB OAK cameras without root permissions, run:"
  echo "  echo 'SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"03e7\", MODE=\"0666\"' | sudo tee /etc/udev/rules.d/80-movidius.rules"
  echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
  echo "--------------------------------------------------------"
fi
if ! ldconfig -p 2>/dev/null | grep -q "libGLESv2"; then
  echo "NOTE: libGLESv2 not detected. On headless Linux, MediaPipe may require: sudo apt install -y libgles2"
fi

# 5. Native Rollup/Vite modules must be installed on this exact Linux architecture
cd "$FRONTEND_DIR"
npm install --include=optional || npm install --include=optional --ignore-scripts
npm run build

echo "Setup complete. Start development with: $BACKEND_DIR/run_dev_linux.sh"
