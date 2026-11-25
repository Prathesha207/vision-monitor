@echo off
setlocal enabledelayedexpansion
title Duck Monitor - Clean Build
echo =======================================================
echo     Duck Monitor - CLEAN Build (Windows Desktop App)
echo =======================================================
echo.
echo This will clear ALL build caches and create a fresh build.
echo.

cd /d "%~dp0"

REM ── 1. Kill any running backend / frontend processes ──
echo [1/6] Killing stale processes...
taskkill /F /IM "duck-monitor.exe" 2>nul
taskkill /F /IM "backend.exe" 2>nul
timeout /t 1 /nobreak >nul

REM ── 2. Clear Python bytecache ──
echo [2/6] Clearing Python bytecache...
pushd backend
for /d /r %%d in (__pycache__) do (
    if exist "%%d" rd /s /q "%%d" 2>nul
)
del /s /q *.pyc 2>nul
popd

REM ── 3. Clear PyInstaller build artifacts ──
echo [3/6] Clearing PyInstaller build artifacts...
if exist "backend\build" rd /s /q "backend\build"
if exist "backend\dist" rd /s /q "backend\dist"
if exist "backend\backend.spec" (
    echo     Keeping backend.spec (needed by build)
)

REM ── 4. Clear Electron / Vite build artifacts ──
echo [4/6] Clearing Electron and Vite build artifacts...
if exist "frontend\dist" rd /s /q "frontend\dist"
if exist "frontend\dist_app" rd /s /q "frontend\dist_app"

REM ── 5. Clear npm cache for frontend ──
echo [5/6] Clearing npm cache...
pushd frontend
call npm cache clean --force 2>nul
popd

REM ── 6. Run the normal build ──
echo [6/6] Starting fresh build...
echo.
call "%~dp0build.bat"
