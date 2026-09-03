# Install the backend and frontend correctly on a Windows development machine.
$ErrorActionPreference = 'Stop'

$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path (Split-Path -Parent $BackendDir) 'vision-ai-frontend'
$Python = (Get-Command python).Source

& $Python -m venv (Join-Path $BackendDir '.venv')
$VenvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $BackendDir 'requirements.txt')

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  Write-Host 'NVIDIA GPU detected; installing CUDA-enabled PyTorch...'
  $CudaIndex = if ($env:PYTORCH_CUDA_INDEX) { $env:PYTORCH_CUDA_INDEX } else { 'https://download.pytorch.org/whl/cu128' }
  & $VenvPython -m pip install --force-reinstall --index-url $CudaIndex torch torchvision
  if ($LASTEXITCODE -ne 0) {
    throw "CUDA wheel index $CudaIndex is unavailable for this Python/platform. Set PYTORCH_CUDA_INDEX to another supported PyTorch CUDA index."
  }
} else {
  Write-Host 'No NVIDIA GPU detected; keeping CPU-compatible PyTorch.'
}

& $VenvPython -m pip install (Join-Path $BackendDir 'app\ml\duck_analyzer-1.0.8-py3-none-any.whl')
Push-Location $FrontendDir
if (Test-Path 'node_modules') { Remove-Item 'node_modules' -Recurse -Force }
npm install --include=optional
Pop-Location

& $VenvPython -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
Write-Host 'Setup complete. Build the backend with: .venv\Scripts\python.exe build.py'