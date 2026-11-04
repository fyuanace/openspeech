@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
".venv\Scripts\python.exe" -u -m voice_platform wake-test --seconds 60
pause
