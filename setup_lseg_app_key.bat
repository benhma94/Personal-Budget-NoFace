@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_lseg_app_key.ps1"
exit /b %ERRORLEVEL%
