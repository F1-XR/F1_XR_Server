@echo off
cd /d "%~dp0"
if /I "%~1"=="--startup" (
  timeout /t 8 /nobreak >nul
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" tray_server.py %*
) else (
  start "" pythonw tray_server.py %*
)
