@echo off
echo Starting Vision Monitor Development Servers...

cd vision-ai-backend
echo Starting Backend...
start "Vision Monitor Backend" cmd /c ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"

cd ..\vision-ai-frontend
echo Starting Frontend...
start "Vision Monitor Frontend" cmd /c "npm run dev"

echo.
echo ==================================================
echo Vision Monitor is running!
echo Backend API: http://localhost:8000
echo Frontend UI: http://localhost:5173
echo.
echo Close the two command prompt windows to stop the servers.
echo ==================================================
pause
