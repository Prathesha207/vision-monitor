@echo off
setlocal enabledelayedexpansion
title Duck Monitor

cd /d "%~dp0"

where powershell >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PowerShell is required to launch Duck Monitor but was not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend\run_dev_windows.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo [ERROR] Failed to start Duck Monitor. Review logs above.
    echo =======================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
pause
