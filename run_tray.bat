@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" tray_server.py %*
) else (
  start "" pythonw tray_server.py %*
)
