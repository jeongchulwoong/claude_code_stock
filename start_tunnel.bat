@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
REM ============================================================
REM Cloudflare Tunnel - issue temporary URLs for external sharing
REM
REM Prerequisites:
REM   1. winget install --id Cloudflare.cloudflared (one-time)
REM   2. dashboard\app.py and realtime_app.py must be running
REM ============================================================

REM ============================================================
REM Locate cloudflared (PATH or known winget location)
REM ============================================================
set "CLOUDFLARED_EXE="
where cloudflared >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "CLOUDFLARED_EXE=cloudflared"
    goto :cf_done
)

REM cmd `dir /s /b` rejects wildcards in mid-path; trailing-only is fine.
for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\*cloudflared.exe" 2^>nul') do (
    set "CLOUDFLARED_EXE=%%F"
    goto :cf_done
)

if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe" set "CLOUDFLARED_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"
if exist "%ProgramFiles%\cloudflared\cloudflared.exe"            set "CLOUDFLARED_EXE=%ProgramFiles%\cloudflared\cloudflared.exe"
if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe"       set "CLOUDFLARED_EXE=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"

:cf_done
if not defined CLOUDFLARED_EXE (
    echo [ERROR] cloudflared not found.
    echo.
    echo Install via PowerShell ^(as admin^):
    echo     winget install --id Cloudflare.cloudflared
    echo.
    pause
    exit /b 1
)
echo [OK] cloudflared: !CLOUDFLARED_EXE!

echo.
echo ============================================================
echo   Cloudflare Tunnel - external URLs for friends
echo ============================================================
echo   1. Dashboard 5000 tunnel
echo   2. Realtime  5001 tunnel
echo   Each console window will print a https://...trycloudflare.com URL.
echo   Share the 5000 /client URL for public read-only access.
echo.
echo   IMPORTANT: closing those console windows kills the tunnel.
echo   Restart this script after rebooting the laptop -- URLs will change.
echo ============================================================
echo.

start "Cloudflare Tunnel 5000" cmd /k "chcp 65001 > nul && ""!CLOUDFLARED_EXE!"" tunnel --url http://localhost:5000 --metrics 127.0.0.1:20241"
timeout /t 3 > nul
start "Cloudflare Tunnel 5001" cmd /k "chcp 65001 > nul && ""!CLOUDFLARED_EXE!"" tunnel --url http://localhost:5001 --metrics 127.0.0.1:20242"

echo.
echo Two new console windows opened.
echo Copy the Dashboard 5000 URL and add /client before sending it.
echo.
echo - /client: public read-only, no password
echo - /login: admin password required
echo.
pause
