@echo off
setlocal
cd /d "%~dp0"
title ATLAS

if not exist ".venv\Scripts\python.exe" (
  call setup.bat
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" install_assets.py
if errorlevel 1 (
  echo TTS model or FFmpeg is missing. Check your internet connection and run setup.bat.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" bot.py
echo.
echo ATLAS stopped. If the token was invalid, reset it in the Discord Developer Portal and restart.
pause
