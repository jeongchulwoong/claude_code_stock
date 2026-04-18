@echo off
cd /d "%~dp0"

set PYTHON=C:\Users\hanso\AppData\Local\Programs\Python\Python312\python.exe
set BASE=%~dp0

start "Main Bot" cmd /k "cd /d %BASE% && %PYTHON% main_v2.py"
timeout /t 2 > nul

start "Dashboard (5000)" cmd /k "cd /d %BASE% && %PYTHON% dashboard\app.py"
timeout /t 2 > nul

start "Dashboard Realtime (5001)" cmd /k "cd /d %BASE% && %PYTHON% dashboard\realtime_app.py"
timeout /t 2 > nul

start "Stock Watcher" cmd /k "cd /d %BASE% && %PYTHON% scripts\fetch_real_stocks.py --watch --interval 30"
timeout /t 2 > nul

start "Foreign Scheduler" cmd /k "cd /d %BASE% && %PYTHON% foreign\scheduler.py --interval 30"
timeout /t 2 > nul

start "Realtime WebSocket" cmd /k "cd /d %BASE% && %PYTHON% core\kiwoom_ws.py"

echo.
echo All processes started!
echo Advanced Dashboard : http://localhost:5000/advanced
echo Realtime Dashboard : http://localhost:5001
pause
