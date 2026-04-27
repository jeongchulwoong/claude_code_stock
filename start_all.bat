@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

set BASE=%~dp0
set PYTHON=%BASE%venv\Scripts\python.exe

REM ============================================================
REM Step 1: Locate Python interpreter
REM ============================================================
if not exist "%PYTHON%" (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        set PYTHON=%%P
        goto :found_python
    )
)

:found_python
if not exist "%PYTHON%" (
    echo [ERROR] Python executable not found.
    echo         Expected: %BASE%venv\Scripts\python.exe
    echo         Recreate the venv, then: pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] Python:    %PYTHON%

REM ============================================================
REM Step 2: Locate cloudflared (PATH or known winget location)
REM ============================================================
set "CLOUDFLARED_EXE="
where cloudflared >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "CLOUDFLARED_EXE=cloudflared"
    goto :cf_done
)

REM winget per-user package install (most common after winget install)
REM NOTE: cmd `dir /s /b` does NOT support wildcards in the middle of a path.
REM       Use a trailing-only wildcard (`*cloudflared.exe`) so /s walks the tree.
for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\*cloudflared.exe" 2^>nul') do (
    set "CLOUDFLARED_EXE=%%F"
    goto :cf_done
)

REM other common locations
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe" set "CLOUDFLARED_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"
if exist "%ProgramFiles%\cloudflared\cloudflared.exe"            set "CLOUDFLARED_EXE=%ProgramFiles%\cloudflared\cloudflared.exe"
if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe"       set "CLOUDFLARED_EXE=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"

:cf_done
if defined CLOUDFLARED_EXE (
    echo [OK] cloudflared: !CLOUDFLARED_EXE!
) else (
    echo [WARN] cloudflared not found - external sharing skipped
    echo        Install: winget install --id Cloudflare.cloudflared
    echo        Then run start_all.bat again
)

REM ============================================================
REM Step 3: Launch core processes (each window forces UTF-8)
REM ============================================================
start "Main Bot" cmd /k "chcp 65001 > nul && cd /d "%BASE%" && "%PYTHON%" main_v2.py"
timeout /t 2 > nul

start "Dashboard 5000" cmd /k "chcp 65001 > nul && cd /d "%BASE%" && "%PYTHON%" dashboard\app.py"
timeout /t 2 > nul

start "Dashboard 5001 Realtime" cmd /k "chcp 65001 > nul && cd /d "%BASE%" && "%PYTHON%" dashboard\realtime_app.py"
timeout /t 2 > nul

start "Stock Watcher" cmd /k "chcp 65001 > nul && cd /d "%BASE%" && "%PYTHON%" scripts\fetch_real_stocks.py --watch --interval 30"

REM NOTE: Kiwoom WebSocket 은 main_v2.py 가 supervisor 스레드로 띄우므로 여기서 별도 실행하지 않는다.
REM       (이전에는 core\kiwoom_ws.py 를 또 띄워서 WS 가 2개 떠 토큰/연결 충돌이 발생했음.)

REM ============================================================
REM Step 4: Cloudflare Tunnel (auto-launch if available)
REM ============================================================
if defined CLOUDFLARED_EXE (
    timeout /t 3 > nul
    start "Cloudflare Tunnel 5000" cmd /k "chcp 65001 > nul && ""!CLOUDFLARED_EXE!"" tunnel --url http://localhost:5000 --metrics 127.0.0.1:20241"
    timeout /t 3 > nul
    start "Cloudflare Tunnel 5001" cmd /k "chcp 65001 > nul && ""!CLOUDFLARED_EXE!"" tunnel --url http://localhost:5001 --metrics 127.0.0.1:20242"
)

echo.
echo ==============================================================
echo   All processes started.
echo ==============================================================
echo   Local URLs:
echo     - Dashboard : http://localhost:5000/advanced
echo     - Realtime  : http://localhost:5001
echo.
if defined CLOUDFLARED_EXE (
    echo   External URLs:
    echo     - Check the "Cloudflare Tunnel 5000" window
    echo     - Copy the https://...trycloudflare.com/client URL for public client view
    echo.
    echo   Access policy:
    echo     - /client is public read-only ^(no password^)
    echo     - /login with DASHBOARD_ADMIN_PASSWORD is admin-only
)
echo ==============================================================
pause
