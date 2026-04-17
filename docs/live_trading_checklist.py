"""
docs/live_trading_checklist.py — 실거래 전환 전 자동 검증기

실행:
    python docs/live_trading_checklist.py

모든 항목이 PASS여야 실거래 전환이 가능하다.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 결과 구조 ─────────────────────────────────

@dataclass
class CheckItem:
    category: str
    name:     str
    status:   Literal["PASS", "FAIL", "WARN", "SKIP"]
    detail:   str


results: list[CheckItem] = []


def check(category, name, condition, detail_ok, detail_fail, warn=False):
    if condition:
        results.append(CheckItem(category, name, "PASS", detail_ok))
    else:
        status = "WARN" if warn else "FAIL"
        results.append(CheckItem(category, name, status, detail_fail))


# ═══════════════════════════════════════════════
# 1. 환경 설정 검사
# ═══════════════════════════════════════════════

def check_environment():
    from dotenv import load_dotenv
    load_dotenv()

    # Python 비트
    import struct
    bits = struct.calcsize("P") * 8
    check("환경", "Python 32bit 확인",
          bits == 32,
          f"Python {bits}bit ✓",
          f"Python {bits}bit — 키움 API는 32bit 필수!", warn=True)

    # Windows 플랫폼
    check("환경", "Windows 플랫폼",
          sys.platform == "win32",
          "Windows ✓",
          f"현재 플랫폼: {sys.platform} (Windows 필수)", warn=True)

    # .env 존재
    env_path = Path(__file__).parent.parent / ".env"
    check("환경", ".env 파일 존재",
          env_path.exists(),
          ".env 파일 확인 ✓",
          ".env 파일 없음 — .env.example을 복사해 설정하세요")

    # API 키 설정
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    check("환경", "ANTHROPIC_API_KEY 설정",
          bool(anthropic_key) and anthropic_key.startswith("sk-ant"),
          "Claude API 키 설정 ✓",
          "ANTHROPIC_API_KEY 미설정 또는 형식 오류")

    # 계좌번호
    account = os.getenv("KIWOOM_ACCOUNT_NO", "")
    check("환경", "KIWOOM_ACCOUNT_NO 설정",
          bool(account) and len(account) >= 8,
          f"계좌번호 설정 ✓ ({account[:4]}****)",
          "KIWOOM_ACCOUNT_NO 미설정")

    # 텔레그램
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    check("환경", "텔레그램 설정",
          bool(tg_token and tg_chat),
          "텔레그램 Bot/Chat ID 설정 ✓",
          "텔레그램 미설정 — 알림 비활성화 상태", warn=True)


# ═══════════════════════════════════════════════
# 2. config.py 리스크 파라미터 검사
# ═══════════════════════════════════════════════

def check_risk_config():
    try:
        from config import RISK_CONFIG, PAPER_TRADING, TRADING_MODE
    except Exception as e:
        results.append(CheckItem("리스크", "config.py 로드", "FAIL", str(e)))
        return

    check("리스크", "PAPER_TRADING 해제 여부",
          not PAPER_TRADING,
          "TRADING_MODE=live 설정 ✓",
          "TRADING_MODE=paper — .env에서 live로 변경 필요")

    check("리스크", "손절선 설정 (-3% ~ -10%)",
          -0.10 <= RISK_CONFIG.get("stop_loss_pct", 0) <= -0.01,
          f"손절선: {RISK_CONFIG['stop_loss_pct']:.0%} ✓",
          f"손절선 비정상: {RISK_CONFIG.get('stop_loss_pct')}")

    check("리스크", "1회 투자금 ≤ 500만원",
          RISK_CONFIG.get("max_invest_per_trade", 0) <= 5_000_000,
          f"1회 투자금: {RISK_CONFIG['max_invest_per_trade']:,}원 ✓",
          f"1회 투자금 과도: {RISK_CONFIG.get('max_invest_per_trade'):,}원")

    check("리스크", "일일 손실 한도 설정",
          RISK_CONFIG.get("daily_loss_limit", 0) < 0,
          f"일손실 한도: {RISK_CONFIG['daily_loss_limit']:,}원 ✓",
          "일일 손실 한도 미설정")

    check("리스크", "AI 신뢰도 최소값 ≥ 65",
          RISK_CONFIG.get("min_confidence", 0) >= 65,
          f"신뢰도 기준: {RISK_CONFIG['min_confidence']}점 ✓",
          f"신뢰도 기준 너무 낮음: {RISK_CONFIG.get('min_confidence')}점")

    check("리스크", "최대 보유 종목 ≤ 10",
          1 <= RISK_CONFIG.get("max_positions", 0) <= 10,
          f"최대 종목: {RISK_CONFIG['max_positions']}개 ✓",
          f"최대 종목 비정상: {RISK_CONFIG.get('max_positions')}개")


# ═══════════════════════════════════════════════
# 3. 페이퍼 트레이딩 성과 검증
# ═══════════════════════════════════════════════

def check_paper_trading_record():
    try:
        from config import DB_PATH
        with sqlite3.connect(DB_PATH) as con:
            total = con.execute(
                "SELECT COUNT(*) FROM orders WHERE status='PAPER_FILLED'"
            ).fetchone()[0]
            days = con.execute(
                "SELECT COUNT(DISTINCT DATE(timestamp)) FROM orders WHERE status='PAPER_FILLED'"
            ).fetchone()[0]
            win = con.execute(
                "SELECT COUNT(*) FROM orders "
                "WHERE order_type='SELL' AND status='PAPER_FILLED'"
            ).fetchone()[0]
    except Exception:
        total, days, win = 0, 0, 0

    check("페이퍼 트레이딩", "최소 14일 이상 운영",
          days >= 14,
          f"페이퍼 운영 {days}일 ✓",
          f"페이퍼 운영 기간 부족: {days}일 (14일 이상 필요)")

    check("페이퍼 트레이딩", "최소 30건 이상 거래",
          total >= 30,
          f"총 {total}건 체결 ✓",
          f"거래 건수 부족: {total}건 (30건 이상 필요)")

    check("페이퍼 트레이딩", "매도 거래 존재",
          win >= 5,
          f"매도 {win}건 확인 ✓",
          f"매도 거래 부족: {win}건 (5건 이상 필요)", warn=True)


# ═══════════════════════════════════════════════
# 4. 키움 API 연결 검사
# ═══════════════════════════════════════════════

def check_kiwoom_api():
    if sys.platform != "win32":
        results.append(CheckItem("키움 API", "COM 오브젝트 로드",
                                 "SKIP", "비-Windows 환경 — 실제 환경에서 확인 필요"))
        results.append(CheckItem("키움 API", "로그인 가능",
                                 "SKIP", "비-Windows 환경"))
        results.append(CheckItem("키움 API", "모의투자 계좌 확인",
                                 "SKIP", "비-Windows 환경"))
        return

    try:
        from PyQt5.QtWidgets import QApplication
        from core.kiwoom_api import KiwoomAPI
        app = QApplication.instance() or QApplication(sys.argv)
        api = KiwoomAPI()
        check("키움 API", "COM 오브젝트 로드", True, "KiwoomAPI 초기화 ✓", "")
        connected = api.login()
        check("키움 API", "로그인 가능", connected, "로그인 성공 ✓", "로그인 실패")
        if connected:
            accounts = api.get_account_list()
            check("키움 API", "계좌 목록 조회",
                  bool(accounts), f"계좌: {accounts} ✓", "계좌 조회 실패")
    except Exception as e:
        results.append(CheckItem("키움 API", "COM 오브젝트 로드", "FAIL", str(e)))


# ═══════════════════════════════════════════════
# 5. 안전장치 코드 검사
# ═══════════════════════════════════════════════

def check_safety_code():
    # order_manager.py에 중복 주문 방지 코드 존재 여부
    om_path = Path(__file__).parent.parent / "core" / "order_manager.py"
    content = om_path.read_text(encoding="utf-8") if om_path.exists() else ""
    check("안전장치", "중복 주문 방지 로직",
          "_pending" in content,
          "중복 주문 방지 코드 확인 ✓",
          "중복 주문 방지 코드 없음 — 심각!")

    # risk_manager.py에 일일 한도 코드 존재
    rm_path = Path(__file__).parent.parent / "core" / "risk_manager.py"
    rm_content = rm_path.read_text(encoding="utf-8") if rm_path.exists() else ""
    check("안전장치", "일일 손실 한도 자동 중단",
          "_halted" in rm_content and "daily_loss_limit" in rm_content,
          "일일 손실 한도 중단 코드 ✓",
          "일일 손실 한도 코드 없음 — 심각!")

    # main.py에 실거래 카운트다운 존재
    main_path = Path(__file__).parent.parent / "main.py"
    main_content = main_path.read_text(encoding="utf-8") if main_path.exists() else ""
    check("안전장치", "실거래 카운트다운 안전장치",
          "10초" in main_content or "time.sleep(10)" in main_content,
          "실거래 카운트다운 코드 ✓",
          "실거래 카운트다운 없음", warn=True)

    # 텔레그램 알림 실패 무시 코드
    tg_path = Path(__file__).parent.parent / "core" / "telegram_bot.py"
    tg_content = tg_path.read_text(encoding="utf-8") if tg_path.exists() else ""
    check("안전장치", "텔레그램 실패 시 거래 계속",
          "except Exception" in tg_content,
          "텔레그램 장애 무시 코드 ✓",
          "텔레그램 장애 처리 없음", warn=True)


# ═══════════════════════════════════════════════
# 결과 출력
# ═══════════════════════════════════════════════

def print_results():
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}
    color= {"PASS": "\033[92m", "FAIL": "\033[91m", "WARN": "\033[93m", "SKIP": "\033[90m"}
    RESET= "\033[0m"

    current_cat = ""
    pass_count  = sum(1 for r in results if r.status == "PASS")
    fail_count  = sum(1 for r in results if r.status == "FAIL")
    warn_count  = sum(1 for r in results if r.status == "WARN")
    skip_count  = sum(1 for r in results if r.status == "SKIP")

    print("\n" + "═" * 60)
    print("  🚀 실거래 전환 체크리스트")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("═" * 60)

    for r in results:
        if r.category != current_cat:
            current_cat = r.category
            print(f"\n  [{current_cat}]")
        c = color[r.status]
        print(f"  {icon[r.status]} {c}{r.name:<30}{RESET}  {r.detail}")

    print("\n" + "═" * 60)
    print(f"  결과: PASS {pass_count} | FAIL {fail_count} | WARN {warn_count} | SKIP {skip_count}")

    if fail_count == 0:
        print("  🎉 모든 필수 항목 통과 — 실거래 전환 가능!")
        print("  ⚠️  최종 확인: .env에서 TRADING_MODE=live 설정 후 python main.py")
    else:
        print(f"  ❌ {fail_count}개 항목 실패 — 수정 후 재실행하세요.")
        fails = [r for r in results if r.status == "FAIL"]
        for f in fails:
            print(f"     → [{f.category}] {f.name}: {f.detail}")

    print("═" * 60 + "\n")
    return fail_count == 0


# ═══════════════════════════════════════════════
# 실거래 전환 가이드 문서 출력
# ═══════════════════════════════════════════════

GUIDE = """
┌─────────────────────────────────────────────────────────┐
│          실거래 전환 단계별 가이드                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  STEP 1. 모의투자 검증 (최소 2주)                          │
│    □ python main.py 로 페이퍼 트레이딩 실행               │
│    □ 일일 손익 텔레그램으로 수신 확인                       │
│    □ 손절/익절 정상 동작 확인                              │
│    □ 14일간 승률·MDD·손익비 분석                          │
│                                                         │
│  STEP 2. 소액 실거래 테스트 (1~2주)                        │
│    □ .env: TRADING_MODE=live 변경                        │
│    □ RISK_CONFIG.max_invest_per_trade = 100_000 (10만원) │
│    □ RISK_CONFIG.daily_loss_limit = -50_000 (5만원)      │
│    □ 실제 주문 체결 및 잔고 확인                           │
│                                                         │
│  STEP 3. 정상 운영                                        │
│    □ 투자금 점진적 증가 (10만→30만→50만원)                 │
│    □ 일일 리포트 텔레그램 수신                             │
│    □ 주간 백테스팅 재실행으로 전략 점검                     │
│    □ 월간 AI 신뢰도 캘리브레이션                           │
│                                                         │
│  ⚠️  절대 금지 사항                                        │
│    ✗ 모의투자 없이 바로 실거래 전환                         │
│    ✗ config.py 리스크 파라미터 삭제                        │
│    ✗ PAPER_TRADING 체크 로직 제거                         │
│    ✗ API 키를 코드에 직접 하드코딩                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
"""


if __name__ == "__main__":
    print(GUIDE)
    check_environment()
    check_risk_config()
    check_paper_trading_record()
    check_kiwoom_api()
    check_safety_code()
    ok = print_results()
    sys.exit(0 if ok else 1)
