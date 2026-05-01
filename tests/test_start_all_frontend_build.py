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


def test_start_all_auto_builds_only_when_manifest_missing() -> None:
    """launcher 는 manifest 부재 시에만 *one-time* 빌드. 매번 빌드 금지.

    허용:
      - manifest 부재 + npm 발견 + -NoBuild 플래그 미지정 → npm run build 1회.
    금지:
      - 백그라운드로 npm 영구 실행 (Start-ManagedProcess 와 함께).
      - 매 launcher 시작마다 무조건 빌드 (manifest 존재해도 빌드).
    """
    src = START_ALL_PS1.read_text(encoding="utf-8")

    # 백그라운드 실행 금지 — auto-build 는 foreground 한 번만.
    forbidden_bg = (
        "Start-ManagedProcess -Name \"frontend",
        "Start-ManagedProcess -FilePath $npmCmd",
        "Start-Process -FilePath $npmCmd",
        "-LogName \"frontend",
    )
    for token in forbidden_bg:
        assert token not in src, f"npm must not run as background managed process: {token!r}"

    # 무조건 빌드 (manifest 존재해도) 금지 — manifest 체크 *분기 안* 에서만 build.
    # if ($reactBuilt) { ... [OK] ... } else { ... build ... } 패턴을 강제.
    assert "Test-Path -LiteralPath $manifest" in src, "manifest 존재 체크 필요"
    assert "if ($reactBuilt)" in src, "manifest 존재 분기 필요"
    # NoBuild 옵션으로 자동 빌드 비활성 가능해야 함.
    assert "$NoBuild" in src, "operator 가 -NoBuild 로 자동 빌드 끌 수 있어야 함"


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
