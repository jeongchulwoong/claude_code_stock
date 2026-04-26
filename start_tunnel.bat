@echo off
chcp 65001 > nul
REM ──────────────────────────────────────────────
REM Cloudflare Tunnel — 외부 친구들에게 임시 URL 발급
REM
REM 사전 조건:
REM   1. winget install --id Cloudflare.cloudflared (한 번만)
REM   2. dashboard\app.py / realtime_app.py 가 먼저 실행 중이어야 함
REM
REM 실행 후 두 콘솔 창에 각각 다음 형태의 URL 출력됨:
REM   https://xxxx-xxxx.trycloudflare.com   ← 친구한테 던질 주소
REM
REM ⚠️ 노트북 끄거나 이 창 종료하면 URL 무효화됨.
REM    재시작 시 새 URL 친구한테 보내줘야 함 (영구 URL 원하면 도메인 + named tunnel).
REM ──────────────────────────────────────────────

where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] cloudflared 설치 안됨.
    echo.
    echo 설치 방법 ^(관리자 PowerShell^):
    echo     winget install --id Cloudflare.cloudflared
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Cloudflare Tunnel — 외부 친구 접속용 임시 URL 발급
echo ============================================================
echo   1. 메인 대시보드  ^(5000^) 터널 시작
echo   2. 실시간 대시보드 ^(5001^) 터널 시작
echo   각 창에 출력되는 https://...trycloudflare.com URL 을 친구에게 공유
echo ============================================================
echo.

start "Cloudflare Tunnel — Dashboard 5000" cmd /k "cloudflared tunnel --url http://localhost:5000"
timeout /t 3 > nul
start "Cloudflare Tunnel — Realtime 5001" cmd /k "cloudflared tunnel --url http://localhost:5001"

echo.
echo 두 콘솔 창이 새로 열렸습니다.
echo 각 창의 https://...trycloudflare.com URL 을 친구한테 보내세요.
echo.
echo client 비번으로 로그인하면 스크리너/차트/종목만 볼 수 있고,
echo admin 비번이어야 설정 변경/주문 조회 가능합니다.
echo.
pause
