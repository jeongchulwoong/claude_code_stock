@echo off
chcp 65001 > nul
cd /d "%~dp0"

set BASE=%~dp0
set PYTHON=%BASE%venv\Scripts\python.exe

if not exist "%PYTHON%" (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        set PYTHON=%%P
        goto :found_python
    )
)

:found_python
if not exist "%PYTHON%" (
    echo Python executable not found.
    echo Expected: %BASE%venv\Scripts\python.exe
    echo Recreate the venv, then run: pip install -r requirements.txt
    pause
    exit /b 1
)

echo Using Python: %PYTHON%

start "Main Bot" cmd /k "cd /d "%BASE%" && "%PYTHON%" main_v2.py"
timeout /t 2 > nul

start "Dashboard (5000)" cmd /k "cd /d "%BASE%" && "%PYTHON%" dashboard\app.py"
timeout /t 2 > nul

start "Dashboard Realtime (5001)" cmd /k "cd /d "%BASE%" && "%PYTHON%" dashboard\realtime_app.py"
timeout /t 2 > nul

start "Stock Watcher" cmd /k "cd /d "%BASE%" && "%PYTHON%" scripts\fetch_real_stocks.py --watch --interval 30"
timeout /t 2 > nul

start "Realtime WebSocket" cmd /k "cd /d "%BASE%" && "%PYTHON%" core\kiwoom_ws.py"

echo.
echo All processes started!
echo Advanced Dashboard : http://localhost:5000/advanced
echo Realtime Dashboard : http://localhost:5001
echo.
echo 외부 친구한테 공유하려면 별도로 start_tunnel.bat 실행 ^(Cloudflare Tunnel^)
pause
