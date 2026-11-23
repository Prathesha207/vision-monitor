# ==============================================================================
# Duck Monitor - Windows Development Runner
# Mirrors run_dev_linux.sh: auto-checks venv, requirements, traycount wheel,
# CUDA kernels, node_modules, finds a free port, and starts backend + frontend.
# ==============================================================================
$ErrorActionPreference = 'Stop'

$BackendDir = if ($MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    Get-Location
}
$ProjectRoot = Split-Path -Parent $BackendDir
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$VenvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
$SetupScript = Join-Path $BackendDir 'setup_windows.ps1'

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "       Starting Duck Monitor Development Mode        " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Auto-run setup if virtual environment is missing
if (-not (Test-Path $VenvPython)) {
    Write-Host "[PRE-FLIGHT] Backend virtual environment is missing. Running setup_windows.ps1..." -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SetupScript
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
        throw "Setup failed or .venv is still missing."
    }
}

# 2. Fast pre-flight check: install missing Python requirements if needed
$DepsOk = & $VenvPython -c "import fastapi, uvicorn, cv2, torch, torchvision, yaml, ultralytics, mediapipe, timm, safetensors, huggingface_hub, scipy; print('OK')" 2>$null
if ($DepsOk -ne 'OK') {
    Write-Host "[PRE-FLIGHT] Missing backend/ML packages detected. Installing requirements.txt..." -ForegroundColor Yellow
    & $VenvPython -m pip install --quiet --prefer-binary -r (Join-Path $BackendDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install backend requirements.txt."
    }
}

# 3. Fast pre-flight check: install bundled TrayCount wheel if missing
$TrayCountOk = & $VenvPython -c "from traycount import TrayCounter; print('OK')" 2>$null
if ($TrayCountOk -ne 'OK') {
    $WhlDir = Join-Path $BackendDir 'app\ml\whl'
    $SearchRoot = if (Test-Path $WhlDir) { $WhlDir } else { Join-Path $BackendDir 'app\ml' }
    $TrayCountWheel = Get-ChildItem -Path $SearchRoot -Filter 'traycount-*.whl' -Recurse -ErrorAction SilentlyContinue |
        Sort-Object {
            if ($_.Name -match 'traycount-([0-9]+(\.[0-9]+)*)') {
                try { [System.Version]$matches[1] } catch { [System.Version]'0.0.0' }
            } else {
                [System.Version]'0.0.0'
            }
        } -Descending | Select-Object -First 1
    if ($TrayCountWheel) {
        Write-Host "[PRE-FLIGHT] Installing $($TrayCountWheel.Name)..." -ForegroundColor Yellow
        & $VenvPython -m pip install --quiet --no-deps --force-reinstall $TrayCountWheel.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install $($TrayCountWheel.Name)."
        }
    } else {
        Write-Host "[WARN] traycount wheel not found in app\ml\whl." -ForegroundColor Yellow
    }
}

# 4. Verify CUDA kernels when an NVIDIA GPU is present; auto-repair via setup if broken
$HasNvidia = $false
$SmiCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($SmiCmd) {
    & $SmiCmd.Source --query-gpu=name --format=csv,noheader 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $HasNvidia = $true }
}
if ($HasNvidia) {
    $CudaOk = & $VenvPython -c "import torch; x=torch.ones((1,), device='cuda:0'); assert (x+1).item()==2; torch.cuda.synchronize(0); print('OK:' + torch.cuda.get_device_name(0))" 2>$null
    if ($CudaOk -like 'OK:*') {
        Write-Host "[PRE-FLIGHT] GPU Ready: $($CudaOk.Substring(3))" -ForegroundColor Green
    } else {
        Write-Host "[PRE-FLIGHT] NVIDIA GPU detected, but installed PyTorch cannot execute CUDA kernels. Running setup..." -ForegroundColor Yellow
        & powershell -NoProfile -ExecutionPolicy Bypass -File $SetupScript
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to repair CUDA PyTorch environment."
        }
    }
} else {
    Write-Host "[PRE-FLIGHT] Inference device: CPU (no NVIDIA GPU detected)" -ForegroundColor Yellow
}

# 5. Ensure frontend dependencies exist
$ViteBin = Join-Path $FrontendDir 'node_modules\.bin\vite.cmd'
if (-not (Test-Path $ViteBin)) {
    Write-Host "[PRE-FLIGHT] Frontend dependencies missing. Installing node_modules..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        & npm.cmd install --include=optional
        if ($LASTEXITCODE -ne 0) {
            & npm.cmd install --include=optional --ignore-scripts
            if ($LASTEXITCODE -ne 0) { throw "npm install failed in $FrontendDir." }
        }
    } finally {
        Pop-Location
    }
}

# 6. Auto-select an available backend port (default 8000, override with BACKEND_PORT)
$TargetPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } elseif ($env:PORT) { [int]$env:PORT } else { 8000 }
$Port = $TargetPort

function Test-PortFree([int]$CheckPort) {
    $res = & $VenvPython -c "import socket, sys; s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); rc=0
try:
    s.bind(('0.0.0.0', int(sys.argv[1])))
except OSError:
    rc=1
finally:
    s.close()
sys.exit(rc)" $CheckPort 2>$null
    return ($LASTEXITCODE -eq 0)
}

while (-not (Test-PortFree $Port)) {
    Write-Host "Port $Port is already in use. Trying port $($Port + 1)..." -ForegroundColor Yellow
    $Port++
    if ($Port -gt ($TargetPort + 50)) {
        throw "Could not find an open port in range $TargetPort-$Port."
    }
}
if ($Port -ne $TargetPort) {
    Write-Host "Note: Port $TargetPort was busy. Using available port $Port for backend." -ForegroundColor Cyan
}

# 7. Start Backend and Frontend in dedicated console windows
Write-Host ""
Write-Host "[1/2] Starting Duck Monitor Backend API (Port $Port)..." -ForegroundColor Cyan
$BackendCmd = "`"$VenvPython`" -m uvicorn app.main:app --reload --reload-dir app --reload-exclude .venv* --reload-exclude data* --reload-exclude recordings* --port $Port"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title Duck Monitor - Backend API && $BackendCmd" -WorkingDirectory $BackendDir

Write-Host "[2/2] Starting Duck Monitor Frontend UI (Port 5173)..." -ForegroundColor Cyan
$FrontendCmd = "set VITE_API_BASE_URL=http://localhost:$Port && npm run dev"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title Duck Monitor - Frontend UI && $FrontendCmd" -WorkingDirectory $FrontendDir

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "[SUCCESS] Duck Monitor development servers started!" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend Application : http://localhost:5173" -ForegroundColor White
Write-Host "  Backend Health Check : http://localhost:$Port/health" -ForegroundColor White
Write-Host "  API Documentation    : http://localhost:$Port/docs" -ForegroundColor White
Write-Host ""
Write-Host "Keep both console windows open while working." -ForegroundColor Gray
Write-Host "To stop all servers, simply close the two console windows." -ForegroundColor Gray
Write-Host "=======================================================" -ForegroundColor Green
