@echo off
setlocal

if defined FINANCE_HUB_LAUNCH_HIDDEN goto :run

set "FINANCE_HUB_LAUNCH_HIDDEN=1"
wscript.exe "%~dp0run_hidden.vbs" "%~f0" %*
exit /b 0

:run
cd /d "%~dp0"
set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\uv\project-envs\personal-budget"

rem OneDrive's Files On-Demand cloud filter driver attaches to the whole
rem drive once enabled, not just the OneDrive folder, so it can still break
rem uv's default hardlink install (Windows error 396) even though this
rem cache/env live outside OneDrive. Force plain-copy installs to avoid it.
set "UV_LINK_MODE=copy"

rem A newly persisted user variable may not yet be present in this process.
rem Load it directly from HKCU without printing the value. Only needed if you
rem press Refresh on the Portfolio tab; the rest of the app works without it.
if not defined LSEG_APP_KEY (
    for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v LSEG_APP_KEY 2^>nul') do (
        if /i "%%A"=="REG_SZ" set "LSEG_APP_KEY=%%B"
        if /i "%%A"=="REG_EXPAND_SZ" set "LSEG_APP_KEY=%%B"
    )
)

where uv >nul 2>&1
if errorlevel 1 (
  py -m uv --version >nul 2>&1
  if errorlevel 1 (
    wscript.exe "%~dp0msgbox.vbs" "uv is not installed or is not on PATH. Install it from https://docs.astral.sh/uv/ and try again."
    exit /b 1
  )
  set "UV_CMD=py -m uv"
) else (
  set "UV_CMD=uv"
)

if not exist "data\Personal Budget.xlsx" (
  wscript.exe "%~dp0msgbox.vbs" "data\Personal Budget.xlsx was not found. Place your workbook there first."
  exit /b 1
)

if not exist "logs" mkdir "logs"

%UV_CMD% run --frozen finance-hub %* > "logs\finance-hub.log" 2>&1
if errorlevel 1 (
  wscript.exe "%~dp0msgbox.vbs" "Finance Hub exited with an error. See logs\finance-hub.log for details."
)
endlocal
