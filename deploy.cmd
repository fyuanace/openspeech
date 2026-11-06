@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
set "deployExitCode=%errorlevel%"
if not "%deployExitCode%"=="0" pause
exit /b %deployExitCode%
