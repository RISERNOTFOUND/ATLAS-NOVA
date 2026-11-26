@echo off
title NOVA
cd /d "%~dp0"

set PYTHON_EXEC=python
if exist ".venv\Scripts\python.exe" (
  set PYTHON_EXEC=".venv\Scripts\python.exe"
)

:loop
%PYTHON_EXEC% nova.py
if errorlevel 3 (
  echo.
  echo NOVA needs attention - see the message above.
  pause
  exit /b
)
echo NOVA stopped. Restarting in 5 seconds...
timeout /t 5 >nul
goto loop
