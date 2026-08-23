@echo off
setlocal
cd /d "%~dp0"
title Ranking Shorts

where ffmpeg >nul 2>nul || echo [!] FFmpeg not found on PATH. Install it:  winget install Gyan.FFmpeg

if not exist ".venv" (
  echo Creating virtual environment ^(first run only^)...
  python -m venv .venv || (echo. & echo Could not create venv - is Python 3.10+ installed and on PATH? & pause & exit /b 1)
)
call ".venv\Scripts\activate.bat"
echo Installing / updating dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo.
echo ============================================================
echo   Ranking Shorts is starting at  http://localhost:8000
echo   Leave this window open while you use it. Close it to stop.
echo ============================================================
start "" http://localhost:8000
python -m uvicorn backend.app:app --port 8000
pause
