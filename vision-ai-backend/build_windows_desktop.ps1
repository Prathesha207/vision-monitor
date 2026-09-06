[CmdletBinding()]
param(
  [ValidateSet('cpu', 'cuda')]
  [string]$Acceleration = 'cuda'
)

# Produces a self-contained 64-bit Windows NSIS installer. Run this on a
# 64-bit Windows machine: PyInstaller and PyTorch must be built natively.
$ErrorActionPreference = 'Stop'
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path (Split-Path -Parent $BackendDir) 'vision-ai-frontend'
$ReleaseVenv = Join-Path $BackendDir '.venv-release'
$Python = (Get-Command python).Source

if (-not [Environment]::Is64BitOperatingSystem) { throw 'A 64-bit Windows host is required.' }
if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) { throw 'vision-ai-frontend must be beside vision-ai-backend.' }

if (-not (Test-Path $ReleaseVenv)) {
  & $Python -m venv --system-site-packages $ReleaseVenv
}
$VenvPython = Join-Path $ReleaseVenv 'Scripts\python.exe'

# Verify CUDA PyTorch
$HasCuda = & $VenvPython -c "import torch; print(torch.cuda.is_available() or 'cu' in torch.__version__)"
Write-Host "CUDA PyTorch status in environment: $HasCuda"
if ($Acceleration -eq 'cuda' -and $HasCuda -ne 'True') {
  $CudaIndex = if ($env:PYTORCH_CUDA_INDEX) { $env:PYTORCH_CUDA_INDEX } else { 'https://download.pytorch.org/whl/cu121' }
  & $VenvPython -m pip install --index-url $CudaIndex torch torchvision
  if ($LASTEXITCODE -ne 0) { throw "Could not install CUDA PyTorch from $CudaIndex." }
}

$DuckAnalyzerWheel = Get-ChildItem (Join-Path $BackendDir 'app\ml\duck_analyzer-*.whl') |
  Sort-Object Name -Descending | Select-Object -First 1
if (-not $DuckAnalyzerWheel) { throw 'The bundled duck_analyzer wheel is missing.' }
& $VenvPython -m pip install $DuckAnalyzerWheel.FullName

Push-Location $BackendDir
try {
  Remove-Item -LiteralPath (Join-Path $BackendDir 'build') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDir 'dist') -Recurse -Force -ErrorAction SilentlyContinue
  & $VenvPython -m PyInstaller --noconfirm --clean --onedir --name backend run.py `
    --add-data 'app/ml/models;app/ml/models' `
    --add-data 'app/ml/config.yaml;app/ml' `
    --add-data 'alembic;alembic' `
    --collect-all app --collect-all fastapi --collect-all starlette --collect-all uvicorn `
    --collect-all sqlalchemy --collect-all cv2 --collect-all torch --collect-all torchvision `
    --collect-all ultralytics --collect-all segmentation_models_pytorch --collect-all depthai `
    --collect-all av --collect-all duck_analyzer --collect-all mediapipe --collect-all matplotlib
  if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally { Pop-Location }

$IconIco = Join-Path $FrontendDir 'public\icon.ico'
if (-not (Test-Path $IconIco)) {
  & $VenvPython (Join-Path $FrontendDir 'public\generate_icons.py')
}

$ReleaseBackend = Join-Path $FrontendDir 'release-backend'
Remove-Item -LiteralPath $ReleaseBackend -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $ReleaseBackend -Force | Out-Null
Copy-Item -Path (Join-Path $BackendDir 'dist\backend\*') -Destination $ReleaseBackend -Recurse -Force

# Strip non-runtime development files (static .lib, C++ headers, debug symbols)
Get-ChildItem -Path $ReleaseBackend -Recurse -Include *.lib, *.pdb, *.exp, *.a -File | Remove-Item -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ReleaseBackend '_internal\torch\include') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ReleaseBackend '_internal\_polars_runtime_32') -Recurse -Force -ErrorAction SilentlyContinue

Push-Location $FrontendDir
try {
  & npm.cmd ci --include=optional
  if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
  & npx electron-builder --win --dir --x64
  if ($LASTEXITCODE -ne 0) { throw 'electron-builder packaging failed.' }

  $Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
  if (-not $Iscc -or -not (Test-Path $Iscc)) {
    $Iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
  }
  if (-not (Test-Path $Iscc)) { throw 'Inno Setup compiler (ISCC.exe) not found.' }

  & $Iscc "installer.iss"
  if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
} finally { Pop-Location }

Write-Host "Installer ready in $FrontendDir\dist_app"
