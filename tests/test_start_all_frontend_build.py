"""tests/test_start_all_frontend_build.py — start_all.ps1 정책 회귀.

정책 (Phase F6 launcher):
  - launcher 는 frontend 를 자동 빌드하지 않는다 (operator 가 명시적으로 npm run build).
    auto-build 은 cold start 시간을 늘리고 의존성 재설치 위험을 만든다.
  - launcher 는 manifest 존재 여부를 *읽고* 상태만 보고한다 — React UI 활성/비활성을 운영자에게 알림.
  - 5000 / 5001 포트의 좀비 listener 는 시작 전에 정리한다.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
START_ALL_PS1 = ROOT / "scripts" / "start_all.ps1"


def test_start_all_does_not_auto_build_frontend() -> None:
    """launcher 는 frontend 를 *실행 단계에서* 자동 빌드하지 않는다.

    상태 표시용으로 'npm run build' 같은 문자열을 *안내 메시지로* 출력하는 것은 허용.
    하지만 PS 가 npm 을 *호출* 하는 패턴 (Start-Process npm / & $npm / Invoke-Expression npm)
    은 금지 — 빌드는 운영자가 명시적으로 실행해야 하는 별도 단계.
    """
    src = START_ALL_PS1.read_text(encoding="utf-8")
    forbidden_invocations = (
        "Ensure-FrontendBuild",        # 옛 auto-build helper 함수.
        "Find-Npm",                     # npm 위치 탐색기.
        "Start-Process -FilePath \"npm",  # npm exec.
        "Start-Process \"npm",
        "Start-Process npm",
        "Start-ManagedProcess -Name \"frontend",  # 백그라운드로 npm 실행.
        "Invoke-Expression \"npm",
        "& \"npm",                       # call operator.
        "& 'npm",
        "& $Npm",
        "Invoke-Expression \"npx",
    )
    for token in forbidden_invocations:
        assert token not in src, f"start_all.ps1 must not invoke npm/npx: {token!r}"


def test_start_all_reports_react_ui_status() -> None:
    """launcher 는 React UI 활성/비활성 상태를 banner 에 표시한다 (status 읽기만, 빌드 X)."""
    src = START_ALL_PS1.read_text(encoding="utf-8")
    # manifest 경로를 *체크* 하는 코드는 허용 — 상태 표시 용도.
    assert "manifest.json" in src, "should reference Vite manifest for status check"
    # FRONTEND_REACT_ENABLED 환경변수도 banner 에 노출.
    assert "FRONTEND_REACT_ENABLED" in src, "should display admin React flag status"
    assert "FRONTEND_CLIENT_REACT_ENABLED" in src, "should display client React flag status"


def test_start_all_cleans_existing_dashboard_ports_before_starting() -> None:
    src = START_ALL_PS1.read_text(encoding="utf-8")
    assert "Stop-ListenersOnPort 5000" in src
    assert "Stop-ListenersOnPort 5001" in src
    assert "Stop-Process -Id $ownerPid -Force" in src
    # PowerShell 자동 변수 $pid 와 충돌하지 않게 ownerPid 같은 별칭만 사용.
    assert "$pid =" not in src
    assert "-Id $pid" not in src


def test_start_all_uses_lock_aware_trader_helpers() -> None:
    """trader 시작/정지는 PID lock 기반 helper 만 사용한다 — Python 직접 kill 금지."""
    src = START_ALL_PS1.read_text(encoding="utf-8")
    assert "scripts\\start_trader.py" in src, "trader start must go through start_trader.py"
    assert "scripts\\stop_trader.py" in src,  "trader stop must go through stop_trader.py"
    # main.py 에 직접 Stop-Process 를 거는 패턴은 금지 (다른 Python 프로세스 잘못 죽임).
    assert "Stop-Process -Name python" not in src
    assert "Stop-Process python" not in src
