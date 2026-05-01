@echo off
REM ============================================================================
REM start_all.bat — Quant Desk 전체 스택 실행 (Windows).
REM
REM 실행 대상:
REM   - main.py                  : 트레이딩 루프 (PID 락 기반, scripts\start_trader.py 가 관리)
REM   - dashboard\app.py         : Flask 운영 콘솔 5000 (Jinja + JSON API + JWT 인증)
REM   - dashboard\realtime_app.py: Socket.IO 5001 (실시간 푸시)
REM   - fetch_real_stocks.py --watch : 종목 메타 수집기 (백그라운드)
REM   - cloudflared              : 외부 공유 터널 (설치돼 있을 때만)
REM
REM 안전:
REM   - main.py 는 실주문 가능. 본 스크립트는 PID 락 기준으로만 띄우고, 이미 떠 있으면 skip.
REM   - Ctrl+C 1회 → 모든 백그라운드 + 트레이더 정리 후 종료.
REM   - frontend\dist\.vite\manifest.json 이 없으면 React UI 비활성 (Jinja fallback).
REM   - FRONTEND_REACT_ENABLED / FRONTEND_CLIENT_REACT_ENABLED 가 React mount 결정.
REM
REM 옵션 (PowerShell 에 그대로 전달):
REM   start_all.bat                  : 기본 — 전체 스택, browser auto-open.
REM   start_all.bat -NoBrowser        : 브라우저 자동 오픈 비활성.
REM   start_all.bat -SkipTrader       : main.py 시작 건너뜀 (UI 만 검증).
REM   start_all.bat -ShowWindows      : 백그라운드 프로세스 창 표시 (디버그).
REM ============================================================================

setlocal EnableExtensions
chcp 65001 > nul
cd /d "%~dp0"

REM Codex/IDE 셸이 가짜 proxy 환경변수를 주입할 때 broker / yfinance / cloudflared 가
REM 직접 네트워크 접근하도록 강제로 비움.
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
    echo         The launcher requires the PS1 helper. Make sure the working tree is intact.
    exit /b 2
)

REM Foreground controller — Ctrl+C 는 PS1 의 CancelKeyPress 핸들러에서 받아 자식 프로세스 정리.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1" %*
set RC=%ERRORLEVEL%

echo.
echo start_all exited with code %RC%.
exit /b %RC%
