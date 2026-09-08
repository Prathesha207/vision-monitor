@echo off
echo =======================================================
echo          Vision Monitor Setup (Windows)
echo =======================================================
echo.

cd vision-ai-backend
powershell -ExecutionPolicy Bypass -File setup_windows.ps1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Setup failed! Please check the logs above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo =======================================================
echo Setup completed successfully!
echo To start the application, simply execute run.bat
echo =======================================================
pause
