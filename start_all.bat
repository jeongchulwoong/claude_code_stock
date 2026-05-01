@echo off
REM ============================================================================
REM start_all.bat - Quant Desk full stack launcher (Windows).
REM
REM IMPORTANT: This .bat MUST stay ASCII-only.
REM   Windows batch parser reads files in the OEM codepage (cp949 on Korean
REM   Windows). UTF-8 multi-byte characters (Korean / em-dash / etc.) get
REM   mis-tokenized BEFORE chcp 65001 has any effect, producing errors like
REM   ''xx' is not recognized as an internal or external command'.
REM   Keep all comments and echo strings in ASCII.
REM
REM Targets:
REM   - main.py                      : trading loop (PID-locked via scripts\start_trader.py)
REM   - dashboard\app.py             : Flask console on 5000 (Jinja + JSON API + JWT)
REM   - dashboard\realtime_app.py    : Socket.IO realtime on 5001
REM   - scripts\fetch_real_stocks.py : background watcher
REM   - cloudflared                  : optional external tunnel
REM
REM Safety:
REM   - main.py is live-order capable. Launcher uses PID lock; never starts twice.
REM   - One Ctrl+C cleans up every background process and the trader.
REM   - frontend\dist\.vite\manifest.json missing -> React UI inactive (Jinja fallback).
REM   - FRONTEND_REACT_ENABLED / FRONTEND_CLIENT_REACT_ENABLED control React mounts.
REM
REM Options (forwarded to the PowerShell helper):
REM   start_all.bat                : full stack, browser auto-open.
REM   start_all.bat -NoBrowser     : do not open browser.
REM   start_all.bat -SkipTrader    : do not start main.py (UI-only smoke).
REM   start_all.bat -ShowWindows   : show background process windows (debug).
REM ============================================================================

setlocal EnableExtensions
chcp 65001 > nul
cd /d "%~dp0"

REM Wipe injected proxy env vars (broker / yfinance / cloudflared need direct net).
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set NO_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
set no_proxy=

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist "%~dp0scripts\start_all.ps1" (
    echo [ERROR] scripts\start_all.ps1 not found.
    echo         The launcher requires the PowerShell helper.
    echo         Make sure the working tree is intact.
    exit /b 2
)

REM Foreground controller. Ctrl+C is caught by the PS1 CancelKeyPress handler
REM which then runs Stop-ManagedProcesses + scripts\stop_trader.py for cleanup.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1" %*
set RC=%ERRORLEVEL%

echo.
echo start_all exited with code %RC%.
exit /b %RC%
