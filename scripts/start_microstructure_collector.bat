@echo off
REM scripts/start_microstructure_collector.bat
REM Windows launcher: Kiwoom microstructure real-time collector (domestic 173 종목)
REM
REM market-data-only — broker/order/risk 코드 0 import. paper-only ML 학습용 데이터 누적.
REM 매 영업일 09:00 직전 가동, 16:00 후 자동 종료 (collector 가 장 외에는 idle).
REM
REM 사용:
REM   scripts\start_microstructure_collector.bat
REM
REM 백그라운드 가동 후 logs/kiwoom_microstructure_collector.{out,err}.log 모니터링.

setlocal
cd /d "%~dp0\.."

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LOG_DIR=logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "OUT=%LOG_DIR%\kiwoom_microstructure_collector.out.log"
set "ERR=%LOG_DIR%\kiwoom_microstructure_collector.err.log"

echo [%date% %time%] starting microstructure collector (domestic ~173 tickers, 4 real_types)
echo   out: %OUT%
echo   err: %ERR%

REM Default: domestic 200 max (173 actual). Real types: tick + hoga + orderbook + hoga_depth (0D).
REM 종료 시그널 처리는 collector 안의 keepalive 로직에 의존.
start "MS_Collector" /B cmd /c ^
  "venv\Scripts\python.exe scripts\collect_kiwoom_realtime_microstructure.py ^
    --watch-list domestic ^
    --max-tickers 200 ^
    --types tick,hoga,orderbook,hoga_depth ^
    > %OUT% 2> %ERR%"

echo [%date% %time%] launched in background. Use scripts\check_microstructure_status.py to verify.
endlocal
