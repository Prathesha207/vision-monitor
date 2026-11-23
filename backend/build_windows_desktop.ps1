[CmdletBinding()]
param(
  [ValidateSet('cpu', 'cuda', 'auto')]
  [string]$Acceleration = 'auto',
  [switch]$SkipPyInstaller
)

# Produces a self-contained 64-bit Windows NSIS installer. Run this on a
# 64-bit Windows machine: PyInstaller and PyTorch must be built natively.
$ErrorActionPreference = 'Stop'
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path (Split-Path -Parent $BackendDir) 'vision-ai-frontend'
$ReleaseVenv = if (Test-Path (Join-Path $BackendDir '.venv')) {
  Join-Path $BackendDir '.venv'
} else {
  Join-Path $BackendDir '.venv-release'
}
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
  $PythonCmd = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $PythonCmd) { throw 'Python was not found in PATH. Please install Python 3.10+ and add it to PATH.' }
$Python = $PythonCmd.Source

if (-not [Environment]::Is64BitOperatingSystem) { throw 'A 64-bit Windows host is required.' }
if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) { throw 'vision-ai-frontend must be beside vision-ai-backend.' }

if (-not (Test-Path $ReleaseVenv)) {
  & $Python -m venv $ReleaseVenv
}
$VenvPython = Join-Path $ReleaseVenv 'Scripts\python.exe'
& $VenvPython -m pip install --quiet --upgrade pip setuptools wheel

# Auto-detect acceleration if 'auto' is specified
if ($Acceleration -eq 'auto') {
  $HasNvidia = $false
  $SmiOutput = & nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null
  if ($LASTEXITCODE -eq 0 -and $null -ne $SmiOutput) {
    $HasNvidia = $true
    Write-Host "nvidia-smi detected NVIDIA GPU(s): $SmiOutput"
  } else {
    $NvidiaGpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" }
    if ($null -ne $NvidiaGpus -and @($NvidiaGpus).Count -gt 0) {
      $HasNvidia = $true
      Write-Host "WMI detected NVIDIA GPU: $(($NvidiaGpus | Select-Object -First 1).Name)"
    }
  }
  $Acceleration = if ($HasNvidia) { 'cuda' } else { 'cpu' }
}

# 1. Verify / Install PyTorch + TorchVision FIRST (CUDA build matching driver)
$TorchCudaOk = try {
  & $VenvPython -c "import torch, torchvision, importlib.metadata as md; _=md.version('torchvision'); assert torch.cuda.is_available(); t=torch.ones((1,), device='cuda:0'); assert (t+1).item()==2; torch.cuda.synchronize(0); print('CUDA_OK:' + torch.__version__)" 2>$null
} catch { $null }

$TorchCpuOk = try {
  & $VenvPython -c "import torch, torchvision, importlib.metadata as md; _=md.version('torchvision'); print('CPU_OK:' + torch.__version__)" 2>$null
} catch { $null }

if ($Acceleration -eq 'cuda') {
  if ($TorchCudaOk -like 'CUDA_OK:*') {
    Write-Host "Operational CUDA PyTorch already installed: $($TorchCudaOk.Substring(8))"
  } else {
    $TorchInstalled = $false
    if ($env:PYTORCH_CUDA_INDEX) {
      & $VenvPython -m pip install --prefer-binary --index-url $env:PYTORCH_CUDA_INDEX torch==2.15.0 torchvision==0.29.2
      if ($LASTEXITCODE -ne 0) {
        & $VenvPython -m pip install --prefer-binary --index-url $env:PYTORCH_CUDA_INDEX torch torchvision
      }
      if ($LASTEXITCODE -eq 0) { $TorchInstalled = $true }
    } else {
      $CudaAttempts = @(
        @{ Index = 'https://download.pytorch.org/whl/cu132'; Pkgs = @('torch==2.15.0', 'torchvision==0.29.2'); Label = 'cu132 (torch 2.15.0)' },
        @{ Index = 'https://download.pytorch.org/whl/cu130'; Pkgs = @('torch', 'torchvision'); Label = 'cu130' },
        @{ Index = 'https://download.pytorch.org/whl/cu128'; Pkgs = @('torch', 'torchvision'); Label = 'cu128' },
        @{ Index = 'https://download.pytorch.org/whl/cu124'; Pkgs = @('torch', 'torchvision'); Label = 'cu124' }
      )
      foreach ($attempt in $CudaAttempts) {
        Write-Host "Installing CUDA PyTorch ($($attempt.Label)) from $($attempt.Index)..."
        & $VenvPython -m pip install --prefer-binary --index-url $attempt.Index @($attempt.Pkgs)
        if ($LASTEXITCODE -eq 0) {
          $VerifyCuda = & $VenvPython -c "import torch; assert torch.cuda.is_available(); t=torch.ones((1,), device='cuda:0'); assert (t+1).item()==2; torch.cuda.synchronize(0); print('OK')" 2>$null
          if ($VerifyCuda -eq 'OK') {
            $TorchInstalled = $true
            break
          }
        }
      }
    }
    if (-not $TorchInstalled) { throw 'Could not install a working CUDA PyTorch & TorchVision build.' }
  }
} elseif ($Acceleration -eq 'cpu') {
  if ($TorchCpuOk -like 'CPU_OK:*' -and $TorchCpuOk -notmatch 'cu') {
    Write-Host "CPU PyTorch already installed: $($TorchCpuOk.Substring(7))"
  } else {
    Write-Host 'Installing CPU-only PyTorch & TorchVision for CPU bundle...'
    & $VenvPython -m pip install --prefer-binary --index-url https://download.pytorch.org/whl/cpu torch torchvision --upgrade --force-reinstall
    if ($LASTEXITCODE -ne 0) { throw 'Could not install CPU PyTorch from PyTorch CPU index.' }
  }
}

# 2. Install backend + TrayCount ML requirements
$ReqFile = Join-Path $BackendDir 'requirements.txt'
if (Test-Path $ReqFile) {
  & $VenvPython -m pip install --prefer-binary -r $ReqFile
  if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend requirements.txt.' }
}

# 3. Install bundled TrayCount wheel
$WhlDir = Join-Path $BackendDir 'app\ml\whl'
$SearchRoot = if (Test-Path $WhlDir) { $WhlDir } else { Join-Path $BackendDir 'app\ml' }
$TrayCountWheel = Get-ChildItem -Path $SearchRoot -Filter 'traycount-*.whl' -Recurse |
  Sort-Object {
    if ($_.Name -match 'traycount-([0-9]+(\.[0-9]+)*)') {
      try { [System.Version]$matches[1] } catch { [System.Version]'0.0.0' }
    } else {
      [System.Version]'0.0.0'
    }
  } -Descending | Select-Object -First 1
if (-not $TrayCountWheel) { throw 'The bundled traycount wheel is missing from app\ml\whl.' }

& $VenvPython -m pip install --prefer-binary --no-deps --force-reinstall $TrayCountWheel.FullName
if ($LASTEXITCODE -ne 0) { throw "Failed to install $($TrayCountWheel.Name)." }

if (-not $SkipPyInstaller -or -not (Test-Path (Join-Path $BackendDir 'dist\backend\backend.exe'))) {
  Push-Location $BackendDir
  try {
    Remove-Item -LiteralPath (Join-Path $BackendDir 'build') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $BackendDir 'dist') -Recurse -Force -ErrorAction SilentlyContinue

    $PyInstallerArgs = @(
      '--noconfirm', '--clean', '--onedir', '--name', 'backend', 'run.py'
    )
    foreach ($dataFolder in @('app/ml/config', 'app/ml/weights', 'app/ml/model', 'alembic')) {
      if (Test-Path (Join-Path $BackendDir $dataFolder)) {
        $PyInstallerArgs += @('--add-data', "$dataFolder;$dataFolder")
      }
    }
    $PyInstallerArgs += @(
      '--copy-metadata', 'torchvision',
      '--copy-metadata', 'ultralytics',
      '--copy-metadata', 'timm',
      '--copy-metadata', 'safetensors',
      '--copy-metadata', 'huggingface_hub',
      '--collect-all', 'app', '--collect-all', 'fastapi', '--collect-all', 'starlette', '--collect-all', 'uvicorn',
      '--collect-all', 'sqlalchemy', '--collect-all', 'cv2', '--collect-all', 'torch', '--collect-all', 'torchvision',
      '--collect-all', 'ultralytics', '--collect-all', 'depthai',
      '--collect-all', 'av', '--collect-all', 'mediapipe',
      '--collect-all', 'scipy', '--collect-all', 'imageio_ffmpeg',
      '--collect-all', 'timm', '--collect-all', 'safetensors', '--collect-all', 'huggingface_hub',
      '--collect-all', 'traycount'
    )
    & $VenvPython -m PyInstaller @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
  } finally { Pop-Location }
} else {
  Write-Host 'Reusing existing dist\backend...'
}

$IconIco = Join-Path $FrontendDir 'public\icon.ico'
if (-not (Test-Path $IconIco)) {
  & $VenvPython (Join-Path $FrontendDir 'public\generate_icons.py')
}

$BackendDist = Join-Path $BackendDir 'dist\backend'
if (Test-Path $BackendDist) {
  Write-Host 'Optimizing dist\backend by removing non-runtime development files...'
  Get-ChildItem -Path $BackendDist -Recurse -Include *.lib, *.pdb, *.exp, *.a -File | Remove-Item -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\torch\include') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\torch\testing') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\torch\test') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\torch\bin') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\jaxlib') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\psycopg2_binary.libs') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\hf_xet') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\_polars_runtime_32') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\app\ml\output') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $BackendDist '_internal\app\unused') -Recurse -Force -ErrorAction SilentlyContinue
}

Push-Location $FrontendDir
try {
  Remove-Item -LiteralPath (Join-Path $FrontendDir 'dist_app\win-unpacked') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -Path (Join-Path $FrontendDir 'dist_app\*.nsis.7z*') -Force -ErrorAction SilentlyContinue
  Remove-Item -Path (Join-Path $FrontendDir 'dist_app\Vision-Monitor*') -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -Path (Join-Path $FrontendDir 'dist_app\Vision-AI*') -Recurse -Force -ErrorAction SilentlyContinue

  if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    & npm.cmd ci --include=optional
    if ($LASTEXITCODE -ne 0) {
      Write-Host 'npm ci failed, falling back to npm install...'
      & npm.cmd install --include=optional
    }
  }
  & npm.cmd run package:win:x64
  if ($LASTEXITCODE -ne 0) { throw 'electron-builder NSIS packaging failed.' }
} finally { Pop-Location }

Write-Host "Installer ready in $FrontendDir\dist_app"
