r"""
scripts/preflight_check.py — 장 시작 전 단독 점검 스크립트

사용:
    .\venv\Scripts\python.exe scripts\preflight_check.py
    .\venv\Scripts\python.exe scripts\preflight_check.py --send-telegram

점검 항목:
    1. Python / venv 정상 (실행 자체로 확인)
    2. .env 의 필수 키 존재
    3. 키움 REST 로그인 성공 (토큰 발급)
    4. 예수금 / 주문가능금액 조회 성공
    5. 보유종목 조회 성공
    6. (옵션) 텔레그램 테스트 메시지 송신

종료 코드:
    0 = 모든 critical 점검 통과
    1 = 하나 이상 critical 실패 (login / .env)
    2 = warning 만 있음 (예수금 0 / 보유 조회 부분 실패 등)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 프로젝트 루트를 import path 에 추가 (스크립트 단독 실행용)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 출력 헬퍼 ─────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"  [\033[32mOK\033[0m]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [\033[33mWARN\033[0m] {msg}")


def _fail(msg: str) -> None:
    print(f"  [\033[31mFAIL\033[0m] {msg}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# ── 점검 함수들 ──────────────────────────────────

def check_env() -> tuple[bool, list[str]]:
    """필수 .env 키 존재 여부."""
    _section("1) .env 키 점검")
    required = ["KIWOOM_APPKEY", "KIWOOM_SECRETKEY", "KIWOOM_ACCOUNT_NO"]
    optional = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "DASHBOARD_ADMIN_PASSWORD", "DASHBOARD_CLIENT_PASSWORD"]
    missing = []
    for key in required:
        if os.environ.get(key):
            _ok(f"{key} 설정됨")
        else:
            _fail(f"{key} 누락 — 필수")
            missing.append(key)
    for key in optional:
        if os.environ.get(key):
            _ok(f"{key} 설정됨")
        else:
            _warn(f"{key} 미설정 — 옵션")
    return (len(missing) == 0, missing)


def check_python() -> bool:
    _section("0) Python / venv")
    pyv = sys.version.split()[0]
    _ok(f"Python {pyv}")
    venv_active = (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or "venv" in sys.executable.lower()
    )
    if venv_active:
        _ok(f"venv: {sys.executable}")
    else:
        _warn(f"venv 가 활성화되지 않은 것으로 보임: {sys.executable}")
    return True


def check_kiwoom_login() -> tuple[bool, "object | None"]:
    """키움 REST 로그인 (토큰 발급) 시도."""
    _section("2) 키움 REST 로그인")
    try:
        from core.kiwoom_api import KiwoomRestAPI
    except Exception as e:
        _fail(f"KiwoomRestAPI import 실패: {e}")
        return (False, None)

    try:
        kw = KiwoomRestAPI()
    except Exception as e:
        _fail(f"KiwoomRestAPI 인스턴스화 실패: {e}")
        return (False, None)

    try:
        ok = kw.login()
    except Exception as e:
        _fail(f"login() 예외: {e}")
        return (False, None)

    if ok is False:
        _fail("login() 반환 False — 토큰 발급 실패. APPKEY/SECRETKEY 또는 네트워크 확인")
        return (False, None)

    _ok("토큰 발급 성공")
    # TTL 노출
    try:
        import time as _t
        ttl = max(0, int(getattr(kw, "_token_expiry_ts", 0) - _t.time()))
        if ttl > 0:
            _ok(f"토큰 TTL ≈ {ttl}초 (~{ttl/3600:.1f}h)")
    except Exception:
        pass
    return (True, kw)


def check_deposit(kw) -> tuple[bool, int]:
    """예수금 / 주문가능금액 조회."""
    _section("3) 예수금 / 주문가능금액")
    try:
        deposit = kw.get_deposit_detail() if hasattr(kw, "get_deposit_detail") else {}
    except Exception as e:
        _fail(f"예수금 조회 예외: {e}")
        return (False, 0)

    if not deposit:
        _fail("응답 없음 또는 빈 dict")
        return (False, 0)

    candidates = [
        ("ord_alow_amt",     deposit.get("ord_alow_amt", 0)),
        ("d2_ord_psbl_amt",  deposit.get("d2_ord_psbl_amt", 0)),
        ("d2_entra",         deposit.get("d2_entra", 0)),
        ("entr",             deposit.get("entr", 0)),
    ]
    for label, val in candidates:
        if val:
            _ok(f"{label} = {val:,}")
    best = max((v for _, v in candidates if v > 0), default=0)
    if best <= 0:
        _warn("주문가능금액 0 — 신규매수 자동 차단됨 (매도/강제청산은 동작)")
        return (True, 0)   # 조회 자체는 성공, 금액만 0
    _ok(f"주문가능금액(최대): {best:,}원")
    return (True, best)


def check_holdings(kw) -> tuple[bool, int]:
    """보유종목 조회."""
    _section("4) 보유종목 조회")
    try:
        holdings = kw.get_holdings() if hasattr(kw, "get_holdings") else []
    except Exception as e:
        _fail(f"보유 조회 예외: {e}")
        return (False, 0)

    n = len(holdings or [])
    _ok(f"{n}개 종목 조회됨")
    for h in (holdings or [])[:10]:
        nm = h.get("name") or h.get("ticker")
        qty = h.get("qty", 0)
        avg = h.get("avg_price", 0)
        rate = h.get("pnl_rate", 0)
        print(f"      - {nm} {qty}주 @{avg:,.0f} ({rate:+.2f}%)")
    return (True, n)


def check_telegram(send: bool) -> bool:
    """텔레그램 — 토큰 존재 여부 확인. send=True 면 테스트 메시지 송신."""
    _section("5) 텔레그램")
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        _warn("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 누락 — 알림 비활성")
        return False

    _ok("텔레그램 자격증명 존재")
    if not send:
        print("      (--send-telegram 옵션을 주면 실제 테스트 메시지 송신)")
        return True

    try:
        import requests
        from datetime import datetime
        msg = f"[preflight] 점검 메시지 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=5,
        )
        if r.status_code == 200:
            _ok(f"테스트 메시지 송신 성공 → chat_id={chat_id}")
            return True
        _fail(f"송신 실패: HTTP {r.status_code} | {r.text[:120]}")
        return False
    except Exception as e:
        _fail(f"송신 예외: {e}")
        return False


# ── 메인 ──────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="장 시작 전 시스템 점검")
    ap.add_argument("--send-telegram", action="store_true",
                    help="텔레그램으로 테스트 메시지를 실제 송신한다")
    args = ap.parse_args()

    print("=" * 56)
    print("  Pre-flight check  —  자동매매 봇 운영 전 확인")
    print("=" * 56)

    # config 가 import 되면서 .env 가 load_dotenv 로 읽힘
    try:
        import config  # noqa: F401
    except Exception as e:
        _fail(f"config import 실패: {e}")
        return 1

    fails:    list[str] = []
    warnings: list[str] = []

    check_python()

    env_ok, missing = check_env()
    if not env_ok:
        fails.append(f".env 누락: {', '.join(missing)}")
        # 키움 키가 없으면 이후 단계 의미 없음
        print("\n결론: 필수 .env 누락. 봇 실행 전 .env 설정 필요.")
        return 1

    login_ok, kw = check_kiwoom_login()
    if not login_ok or kw is None:
        fails.append("키움 로그인 실패")
        print("\n결론: 키움 로그인 실패. 토큰 발급 차단 — 봇 실행 보류.")
        return 1

    dep_ok, cash = check_deposit(kw)
    if not dep_ok:
        fails.append("예수금 조회 실패")
    elif cash <= 0:
        warnings.append("주문가능금액 0 — 신규매수 자동 차단")

    hold_ok, n_hold = check_holdings(kw)
    if not hold_ok:
        warnings.append("보유종목 조회 실패")

    check_telegram(send=args.send_telegram)

    # ── 종합 ──────────────────────────────
    print("\n" + "=" * 56)
    if fails:
        print("결론: \033[31mFAIL\033[0m — critical 항목 실패")
        for f in fails:
            print(f"  · {f}")
        return 1
    if warnings:
        print("결론: \033[33mPARTIAL OK\033[0m — 매수만 제한, 봇 실행은 가능")
        for w in warnings:
            print(f"  · {w}")
        if cash > 0:
            print(f"  · 주문가능금액 {cash:,}원 / 보유 {n_hold}종목")
        return 2
    print("결론: \033[32mALL OK\033[0m — 봇 실행 가능")
    print(f"  · 주문가능금액 {cash:,}원 / 보유 {n_hold}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
