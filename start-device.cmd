@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m voice_platform tray %*
