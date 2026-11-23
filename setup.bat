@echo off
setlocal enabledelayedexpansion
title Duck Monitor Setup

echo =======================================================
echo          Duck Monitor Setup (Windows)
echo =======================================================
echo.

cd /d "%~dp0"

where powershell >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PowerShell is required to run the automated setup but was not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0duck-monitor-backend\setup_windows.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo [ERROR] Setup encountered an issue. Review the logs above.
    echo =======================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo =======================================================
echo [SUCCESS] Setup completed successfully!
echo To launch Duck Monitor, simply double-click run.bat
echo =======================================================
pause
