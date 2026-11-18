[CmdletBinding()]
param(
    [switch]$Clean
)

# ==============================================================================
# Vision Monitor - Windows Environment Setup Script
# Configures Python virtual environment, CUDA PyTorch/TorchVision,
# TrayCount ML dependencies & wheel, and Node.js frontend packages.
# ==============================================================================
$ErrorActionPreference = 'Continue'

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "       Vision Monitor - Automated Setup (Windows)      " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# Determine directories
$BackendDir = if ($MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    Get-Location
}
$ProjectRoot = Split-Path -Parent $BackendDir
$FrontendDir = Join-Path $ProjectRoot 'vision-ai-frontend'

Write-Host "[SETUP] Backend Directory : $BackendDir"
Write-Host "[SETUP] Frontend Directory: $FrontendDir"
Write-Host ""

# ------------------------------------------------------------------------------
# 1. Locate and Validate Python (>= 3.10)
# ------------------------------------------------------------------------------
Write-Host "[1/7] Validating Python installation..." -ForegroundColor Yellow

function Find-ValidPython {
    $candidates = @('python', 'py', 'python3')
    foreach ($cmdName in $candidates) {
        $cmd = Get-Command $cmdName -ErrorAction SilentlyContinue
        if ($cmd) {
            $verOut = & $cmd.Source -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $verOut -match '^(\d+)\.(\d+)') {
                $major = [int]$matches[1]
                $minor = [int]$matches[2]
                if ($major -eq 3 -and $minor -ge 10) {
                    return $cmd.Source
                }
            }
        }
    }

    $defaultPaths = @(
        "$env:LocalAppData\Programs\Python\Python314\python.exe",
        "$env:LocalAppData\Programs\Python\Python313\python.exe",
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "$env:LocalAppData\Programs\Python\Python311\python.exe",
        "$env:LocalAppData\Programs\Python\Python310\python.exe",
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $defaultPaths) {
        if (Test-Path $p) {
            $verOut = & $p -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $verOut -match '^(\d+)\.(\d+)') {
                $major = [int]$matches[1]
                $minor = [int]$matches[2]
                if ($major -eq 3 -and $minor -ge 10) {
                    return $p
                }
            }
        }
    }
    return $null
}

$Python = Find-ValidPython
if (-not $Python) {
    Write-Host "[ERROR] Python 3.10+ was not found on your system." -ForegroundColor Red
    Write-Host "Please download and install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Be sure to check 'Add Python to PATH' during installation." -ForegroundColor Red
    throw "Python 3.10+ not found."
}

$PyVersion = & $Python --version 2>&1
Write-Host "[OK] Using Python: $PyVersion ($Python)" -ForegroundColor Green

# ------------------------------------------------------------------------------
# 2. Locate and Validate Node.js / npm
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/7] Validating Node.js and npm..." -ForegroundColor Yellow

$NpmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
if (-not $NpmCmd) {
    $NpmCmd = (Get-Command npm -ErrorAction SilentlyContinue)
}
if (-not $NpmCmd) {
    $nodePaths = @(
        "$env:ProgramFiles\nodejs\npm.cmd",
        "${env:ProgramFiles(x86)}\nodejs\npm.cmd"
    )
    foreach ($np in $nodePaths) {
        if (Test-Path $np) {
            $NpmCmd = Get-Item $np
            break
        }
    }
}

if (-not $NpmCmd) {
    Write-Host "[ERROR] Node.js / npm was not found on your system." -ForegroundColor Red
    Write-Host "Please download and install Node.js LTS (v20+/22+) from https://nodejs.org/" -ForegroundColor Red
    throw "Node.js / npm not found."
}

$NpmExecutable = if ($NpmCmd.Source) { $NpmCmd.Source } else { $NpmCmd.FullName }
$NpmVersion = & $NpmExecutable --version 2>&1
Write-Host "[OK] Using npm: v$NpmVersion ($NpmExecutable)" -ForegroundColor Green

# ------------------------------------------------------------------------------
# 3. Configure Python Virtual Environment (.venv) & Clean Reset
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/7] Setting up Python virtual environment..." -ForegroundColor Yellow

$VenvDir = Join-Path $BackendDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

if ($Clean) {
    Write-Host "[CLEAN] Clean setup requested. Terminating any running backend processes..." -ForegroundColor Yellow
    Get-Process -Name "python", "uvicorn" -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "*$BackendDir*"
    } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    if (Test-Path $VenvDir) {
        Write-Host "[CLEAN] Removing existing virtual environment at $VenvDir..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    $FrontendDist = Join-Path $FrontendDir 'dist'
    if (Test-Path $FrontendDist) {
        Write-Host "[CLEAN] Removing existing frontend build artifacts at $FrontendDist..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $FrontendDist -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating fresh virtual environment in $VenvDir..."
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
        throw "Failed to create Python virtual environment."
    }
    Write-Host "Upgrading pip, setuptools, and wheel..."
    & $VenvPython -m pip install --quiet --upgrade pip setuptools wheel
} else {
    Write-Host "Reusing existing virtual environment at $VenvDir."
}

# ------------------------------------------------------------------------------
# 4. Install & Verify PyTorch + TorchVision (CUDA FIRST before requirements.txt)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/7] Verifying PyTorch, torchvision, and CUDA GPU acceleration..." -ForegroundColor Yellow

$HasNvidia = $false
$ComputeCap = 0.0
$NvidiaGpus = $null

$SmiPath = "nvidia-smi"
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $SmiCandidates = @(
        "$env:SystemRoot\System32\nvidia-smi.exe",
        "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        "${env:ProgramFiles(x86)}\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    )
    foreach ($cand in $SmiCandidates) {
        if (Test-Path $cand) { $SmiPath = $cand; break }
    }
}
if ($SmiPath -ne "nvidia-smi" -or (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $SmiOutput = & $SmiPath --query-gpu=name,compute_cap --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0 -and $null -ne $SmiOutput) {
        $HasNvidia = $true
        $ComputeCaps = $SmiOutput | ForEach-Object {
            $parts = $_ -split ','
            if ($parts.Count -ge 2) { [float]$parts[1].Trim() } else { 0.0 }
        }
        if ($ComputeCaps) { $ComputeCap = ($ComputeCaps | Measure-Object -Maximum).Maximum }
        Write-Host "[GPU INFO] nvidia-smi detected NVIDIA GPU(s) with max Compute Capability: $ComputeCap" -ForegroundColor Cyan
    }
}

if (-not $HasNvidia) {
    $AllVideoControllers = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
    $NvidiaGpus = @($AllVideoControllers) | Where-Object {
        ($_.Name -match 'NVIDIA|GeForce|Quadro|Tesla|RTX') -or
        ($_.Caption -match 'NVIDIA|GeForce|Quadro|Tesla|RTX') -or
        ($_.Description -match 'NVIDIA|GeForce|Quadro|Tesla|RTX') -or
        ($_.VideoProcessor -match 'NVIDIA|GeForce|RTX') -or
        ($_.AdapterCompatibility -match 'NVIDIA') -or
        ($_.PNPDeviceID -match 'VEN_10DE')
    }
    if ($null -ne $NvidiaGpus -and @($NvidiaGpus).Count -gt 0) {
        $HasNvidia = $true
    } else {
        $NvidiaDriverFiles = @(
            "$env:SystemRoot\System32\nvcuda.dll",
            "$env:SystemRoot\System32\nvapi64.dll"
        )
        foreach ($ndf in $NvidiaDriverFiles) {
            if (Test-Path $ndf) { $HasNvidia = $true; break }
        }
    }
}

$TorchCheck = try {
    & $VenvPython -c "
try:
    import torch
    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
        torch.cuda.init()
        t = torch.zeros((1, 1), device='cuda:0')
        _ = t + 1.0
        torch.cuda.synchronize(0)
        del t
        print('CUDA_OPERATIONAL:' + torch.cuda.get_device_name(0))
    else:
        print('CPU_OPERATIONAL')
except Exception as e:
    print('CUDA_ERROR:' + str(e))
" 2>$null
} catch { $null }

$TorchVisionCheck = try {
    & $VenvPython -c "
try:
    import torchvision, importlib.metadata
    _ = importlib.metadata.version('torchvision')
    print('TV_OK')
except Exception:
    pass
" 2>$null
} catch { $null }

$GpuName = if ($NvidiaGpus) { ($NvidiaGpus | Select-Object -First 1).Name } else { 'NVIDIA GPU' }

if ($TorchCheck -like 'CUDA_OPERATIONAL*' -and $TorchVisionCheck -eq 'TV_OK') {
    Write-Host "[OK] PyTorch and torchvision are already installed and verified operational ($($TorchCheck.Substring(17)))." -ForegroundColor Green
} elseif ($HasNvidia) {
    Write-Host "[INFO] NVIDIA GPU detected ($GpuName). Installing CUDA PyTorch and torchvision first..." -ForegroundColor Cyan
    $TorchInstalled = $false

    if ($env:PYTORCH_CUDA_INDEX) {
        Write-Host "Fetching CUDA wheels from custom index $($env:PYTORCH_CUDA_INDEX)..."
        & $VenvPython -m pip install --prefer-binary --index-url $env:PYTORCH_CUDA_INDEX torch==2.15.0 torchvision==0.29.2
        if ($LASTEXITCODE -ne 0) {
            & $VenvPython -m pip install --prefer-binary --index-url $env:PYTORCH_CUDA_INDEX torch torchvision
        }
        if ($LASTEXITCODE -eq 0) { $TorchInstalled = $true }
    } else {
        # Try TrayCount tested CUDA 13.2 build first, then fallback to compatible CUDA indices
        $CudaAttempts = @(
            @{ Index = 'https://download.pytorch.org/whl/cu132'; Pkgs = @('torch==2.15.0', 'torchvision==0.29.2'); Label = 'cu132 (torch 2.15.0)' },
            @{ Index = 'https://download.pytorch.org/whl/cu130'; Pkgs = @('torch', 'torchvision'); Label = 'cu130' },
            @{ Index = 'https://download.pytorch.org/whl/cu128'; Pkgs = @('torch', 'torchvision'); Label = 'cu128' },
            @{ Index = 'https://download.pytorch.org/whl/cu124'; Pkgs = @('torch', 'torchvision'); Label = 'cu124' }
        )
        foreach ($attempt in $CudaAttempts) {
            Write-Host "Attempting CUDA PyTorch install ($($attempt.Label)) from $($attempt.Index)..." -ForegroundColor Cyan
            & $VenvPython -m pip install --prefer-binary --index-url $attempt.Index @($attempt.Pkgs)
            if ($LASTEXITCODE -eq 0) {
                $KernelOk = & $VenvPython -c "import torch; assert torch.cuda.is_available(); t=torch.ones((1,), device='cuda:0'); assert (t+1).item()==2; torch.cuda.synchronize(0); print('OK')" 2>$null
                if ($KernelOk -eq 'OK') {
                    $TorchInstalled = $true
                    Write-Host "[OK] Verified CUDA PyTorch ($($attempt.Label)) on GPU." -ForegroundColor Green
                    break
                }
                Write-Host "[WARN] Installed $($attempt.Label) but CUDA kernel test failed; trying next fallback..." -ForegroundColor Yellow
            } else {
                Write-Host "[WARN] $($attempt.Label) unavailable for this Python/driver; trying next fallback..." -ForegroundColor Yellow
            }
        }
    }

    if (-not $TorchInstalled) {
        throw "Failed to install a working CUDA PyTorch and torchvision build."
    }
} elseif ($TorchCheck -eq 'CPU_OPERATIONAL' -and $TorchVisionCheck -eq 'TV_OK') {
    Write-Host "[OK] CPU PyTorch and torchvision are installed. (Note: TrayCount inference requires an NVIDIA CUDA GPU)." -ForegroundColor Yellow
} else {
    Write-Host "[WARN] No NVIDIA GPU detected. Installing CPU-only PyTorch (TrayCount inference requires CUDA GPU)..." -ForegroundColor Yellow
    & $VenvPython -m pip install --prefer-binary torch torchvision
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install CPU PyTorch and torchvision."
    }
}

# Install backend + ML requirements (after CUDA PyTorch so PyPI never overwrites CUDA torch)
$BackendCheck = try {
    & $VenvPython -c "
import importlib.util as u
pkgs = ['fastapi', 'uvicorn', 'ultralytics', 'cv2', 'mediapipe', 'yaml', 'timm', 'safetensors', 'huggingface_hub', 'scipy', 'torchvision']
if all(u.find_spec(p) is not None for p in pkgs):
    print('INSTALLED')
else:
    print('MISSING')
" 2>$null
} catch { 'MISSING' }

if ($BackendCheck -eq 'INSTALLED' -and -not $Clean) {
    Write-Host "[OK] Backend and ML dependencies are already installed." -ForegroundColor Green
} else {
    $ReqFile = Join-Path $BackendDir 'requirements.txt'
    if (Test-Path $ReqFile) {
        Write-Host "Installing backend and ML requirements from $ReqFile..."
        & $VenvPython -m pip install --prefer-binary -r $ReqFile
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Python requirements from $ReqFile."
        }
    }
}

# Verify torchvision metadata & CUDA status
$TorchVisionVerify = try {
    & $VenvPython -c "
import torch, torchvision, importlib.metadata
_ = importlib.metadata.version('torchvision')
print('VERIFIED')
" 2>$null
} catch { 'FAILED' }
if ($TorchVisionVerify -ne 'VERIFIED') {
    throw "PyTorch / torchvision verification failed: 'torchvision' package metadata is missing or corrupted."
}

$CudaFinalCheck = try {
    & $VenvPython -c "
import torch
if torch.cuda.is_available() and torch.cuda.device_count() > 0:
    print('CUDA_ACTIVE:' + torch.cuda.get_device_name(0))
else:
    print('CPU_ONLY')
" 2>$null
} catch { 'UNKNOWN' }

if ($CudaFinalCheck -like 'CUDA_ACTIVE:*') {
    $activeGpu = $CudaFinalCheck.Substring(12)
    Write-Host "[GPU SUCCESS] Hardware Acceleration Confirmed: $activeGpu is ready for ML inference!" -ForegroundColor Green
} elseif ($HasNvidia) {
    Write-Host "[WARNING] NVIDIA GPU hardware was detected, but PyTorch is in CPU mode. Verify NVIDIA driver is up to date." -ForegroundColor Yellow
} else {
    Write-Host "[INFO] ML Inference configured for CPU mode (TrayCount requires CUDA GPU)." -ForegroundColor Cyan
}

# ------------------------------------------------------------------------------
# 5. Install Bundled TrayCount Wheel
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/7] Verifying TrayCount ML package..." -ForegroundColor Yellow

$TrayCountInstalled = & $VenvPython -c "from traycount import TrayCounter; print('INSTALLED')" 2>$null
if ($TrayCountInstalled -eq 'INSTALLED' -and -not $Clean) {
    Write-Host "[OK] TrayCount package is already installed." -ForegroundColor Green
} else {
    $WhlDir = Join-Path $BackendDir 'app\ml\whl'
    $SearchRoot = if (Test-Path $WhlDir) { $WhlDir } else { Join-Path $BackendDir 'app\ml' }
    $TrayCountWheel = Get-ChildItem -Path $SearchRoot -Filter 'traycount-*.whl' -Recurse -ErrorAction SilentlyContinue |
        Sort-Object {
            if ($_.Name -match 'traycount-([0-9]+(\.[0-9]+)*)') {
                try { [System.Version]$matches[1] } catch { [System.Version]'0.0.0' }
            } else {
                [System.Version]'0.0.0'
            }
        } -Descending |
        Select-Object -First 1

    if (-not $TrayCountWheel) {
        throw "The bundled traycount wheel is missing from app\ml\whl."
    }

    Write-Host "Installing $($TrayCountWheel.Name)..."
    & $VenvPython -m pip install --prefer-binary --no-deps --force-reinstall $TrayCountWheel.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install $($TrayCountWheel.Name)."
    }
}

# ------------------------------------------------------------------------------
# 6. Install Frontend Dependencies & Build Verification
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[6/7] Verifying frontend dependencies..." -ForegroundColor Yellow

$NodeModulesDir = Join-Path $FrontendDir 'node_modules'
$DistHtml = Join-Path $FrontendDir 'dist\index.html'

if ((Test-Path $NodeModulesDir) -and (Test-Path $DistHtml)) {
    Write-Host "[OK] Frontend packages and production build are already up-to-date." -ForegroundColor Green
} else {
    Push-Location $FrontendDir
    try {
        Write-Host "Installing frontend dependencies..."
        & $NpmExecutable install --include=optional
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] npm install failed (e.g. electron postinstall requires Node >= 22). Retrying with --ignore-scripts for web/dev build..." -ForegroundColor Yellow
            & $NpmExecutable install --include=optional --ignore-scripts
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed in $FrontendDir."
            }
        }

        Write-Host "Building frontend assets..."
        & $NpmExecutable run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build verification failed."
        }
        Write-Host "[OK] Frontend build verified successfully." -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
}

# ------------------------------------------------------------------------------
# 7. End-to-End Pre-Flight Smoke Test (Model & Inference Readiness)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[7/7] Verifying inference engine and model components..." -ForegroundColor Yellow

$SmokeTestResult = & $VenvPython -c "
import sys
try:
    import torch
    import torchvision
    import importlib.metadata
    import timm
    import safetensors
    import huggingface_hub
    import mediapipe
    from ultralytics import YOLO
    from traycount import TrayCounter
    tv = importlib.metadata.version('torchvision')
    tc = importlib.metadata.version('traycount')
    print(f'PASS|{torch.__version__}|{tv}|{tc}')
except Exception as e:
    print(f'FAIL|{e}', file=sys.stderr)
    sys.exit(1)
" 2>&1

if ($LASTEXITCODE -ne 0 -or -not ($SmokeTestResult -match '^PASS\|')) {
    throw "Pre-flight inference verification failed: $SmokeTestResult. Inference would fail at runtime."
}
Write-Host "[OK] Pre-flight inference smoke test PASSED! (YOLO, MediaPipe, timm, safetensors, TrayCount, and torchvision ready)." -ForegroundColor Green

# ------------------------------------------------------------------------------
# Completion Summary
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "       SETUP COMPLETED SUCCESSFULLY WITH NO ERRORS!    " -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""

& $VenvPython -c "
import torch, torchvision, importlib.metadata as md
print(f'  PyTorch Version     : {torch.__version__}')
print(f'  TorchVision Version : {torchvision.__version__}')
print(f'  TrayCount Version   : {md.version(\"traycount\")}')
print(f'  CUDA Enabled        : {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'  GPU Device          : {torch.cuda.get_device_name(0)}')
"
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "  1. Run the application in development: Double-click run.bat" -ForegroundColor White
Write-Host "  2. Package the Windows desktop app   : Double-click build.bat" -ForegroundColor White
Write-Host "=======================================================" -ForegroundColor Green
