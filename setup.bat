@echo off
setlocal
cd /d "%~dp0"
title ATLAS & NOVA Setup

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py") else (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create environment. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo Could not update pip.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" install_assets.py
if errorlevel 1 (
  echo TTS model or FFmpeg download failed. Check your internet connection and retry.
  pause
  exit /b 1
)

echo Setup complete. Run start_atlas.bat (ATLAS) and start_nova.bat (NOVA).
pause
