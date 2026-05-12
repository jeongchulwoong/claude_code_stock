@echo off
REM scripts/start_auto_retrain.bat
REM Windows launcher: 자동 재학습 daemon (paper-only research)
REM
REM 동작:
REM   - 매 5분 microstructure 누적 readiness 체크
REM   - 영업일 16:00 ~ 23:30 KST + 10+ 세션 × 150+ 종목 + 7일 cooldown 만족 시
REM     dataset rebuild + iterZB recipe 학습 + phase_2 backtest 자동 실행
REM   - 결과: models/research/dl_auto_<tag>.joblib + reports/ml/research/auto_retrain_*.json
REM   - 매니페스트 operational path 절대 수정 X (운영자 결정 보존)
REM   - broker / order / risk 코드 0 import
REM
REM 사용:
REM   scripts\start_auto_retrain.bat
REM
REM 모니터링:
REM   tail -f logs\auto_retrain_daemon.{out,err}.log
REM   cat db\ml\auto_retrain_state.json

setlocal
cd /d "%~dp0\.."

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LOG_DIR=logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "OUT=%LOG_DIR%\auto_retrain_daemon.out.log"
set "ERR=%LOG_DIR%\auto_retrain_daemon.err.log"

echo [%date% %time%] starting auto-retrain daemon
echo   readiness: 10+ sessions x 150+ tickers, 7d cooldown
echo   out: %OUT%
echo   err: %ERR%

start "Auto_Retrain" /B cmd /c ^
  "venv\Scripts\python.exe -u scripts\auto_retrain_daemon.py ^
    --min-sessions 10 --min-tickers 150 --cooldown-days 7 ^
    --check-interval-sec 300 ^
    > %OUT% 2> %ERR%"

echo [%date% %time%] daemon launched in background.
echo   - State: db\ml\auto_retrain_state.json
echo   - Pipeline log: logs\auto_retrain.log
endlocal
