@echo off
REM scripts/start_microstructure_collector.bat
REM Windows launcher: Kiwoom microstructure real-time collector daemon (domestic 173 종목)
REM
REM 동작:
REM   - 영업일 08:55 KST 부터 15:30 KST 까지 자동 가동
REM   - 장 시간 외에는 대기 (CPU/네트워크 낭비 X)
REM   - 비정상 종료 시 자동 재시작 (시간당 최대 5회)
REM   - market-data only — broker/order/risk 코드 0 import. paper-only ML 학습용 데이터 누적.
REM
REM 사용:
REM   scripts\start_microstructure_collector.bat
REM     → daemon 이 백그라운드로 가동 (장 외에도 안전하게 대기).
REM
REM 모니터링:
REM   python scripts\check_microstructure_status.py
REM
REM 종료:
REM   taskkill /F /IM python.exe   (모든 python 종료. 다른 python 작업 주의)

setlocal
cd /d "%~dp0\.."

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LOG_DIR=logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "OUT=%LOG_DIR%\kiwoom_microstructure_daemon.out.log"
set "ERR=%LOG_DIR%\kiwoom_microstructure_daemon.err.log"

echo [%date% %time%] starting microstructure daemon (auto-wait + auto-restart)
echo   out: %OUT%
echo   err: %ERR%
echo   policy: domestic ~173 tickers, weekday 08:55-15:30 KST

start "MS_Daemon" /B cmd /c ^
  "venv\Scripts\python.exe -u scripts\run_microstructure_collector_daemon.py ^
    --watch-list domestic ^
    --max-tickers 200 ^
    --types tick,hoga,orderbook,hoga_depth ^
    --max-restarts-per-hour 5 ^
    > %OUT% 2> %ERR%"

echo [%date% %time%] daemon launched in background.
echo   - Verify: python scripts\check_microstructure_status.py
echo   - Logs: %OUT% / %ERR%
endlocal
