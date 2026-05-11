"""tests/test_microstructure_daemon.py — daemon 로직 단위 테스트.

broker / order_manager / risk_manager 를 import 하지 않는다.
실제 subprocess / 네트워크 호출 없음 — 시간 / 영업일 판정 로직만 검증.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_microstructure_collector_daemon import (   # noqa: E402
    KST, MARKET_CLOSE_HHMM, MARKET_OPEN_HHMM,
    is_market_hours, next_market_open,
)


def _kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# ──────────────────────────────────────────────────────────────────
# is_market_hours
# ──────────────────────────────────────────────────────────────────

def test_market_hours_open_at_0900():
    # 2026-05-12 화요일 09:00 — 시장 개장 (warmup 시간 포함, 08:55 부터)
    assert is_market_hours(_kst(2026, 5, 12, 9, 0)) is True


def test_market_hours_warmup_8_55():
    # 08:55 (warmup 시작) — True
    assert is_market_hours(_kst(2026, 5, 12, 8, 55)) is True


def test_market_hours_before_warmup_8_30():
    # 08:30 — False (warmup 시작 전)
    assert is_market_hours(_kst(2026, 5, 12, 8, 30)) is False


def test_market_hours_close_at_15_30():
    # 15:30 정각 — False (close 시간 자체)
    assert is_market_hours(_kst(2026, 5, 12, 15, 30)) is False


def test_market_hours_just_before_close_15_29():
    # 15:29 — True
    assert is_market_hours(_kst(2026, 5, 12, 15, 29)) is True


def test_market_hours_after_close_17_00():
    # 17:00 — False
    assert is_market_hours(_kst(2026, 5, 12, 17, 0)) is False


def test_market_hours_saturday_blocked():
    # 토 09:30 — False (weekday 5)
    assert is_market_hours(_kst(2026, 5, 16, 9, 30)) is False


def test_market_hours_sunday_blocked():
    # 일 09:30 — False (weekday 6)
    assert is_market_hours(_kst(2026, 5, 17, 9, 30)) is False


def test_market_hours_monday_open():
    # 월 09:30 — True
    assert is_market_hours(_kst(2026, 5, 11, 9, 30)) is True


# ──────────────────────────────────────────────────────────────────
# next_market_open
# ──────────────────────────────────────────────────────────────────

def test_next_open_from_evening_returns_next_day():
    # 화 17:00 → 수 08:55
    now = _kst(2026, 5, 12, 17, 0)
    nxt = next_market_open(now)
    assert nxt.year == 2026 and nxt.month == 5 and nxt.day == 13
    assert nxt.hour == 8 and nxt.minute == 55


def test_next_open_from_friday_evening_skips_weekend():
    # 금 17:00 → 월 08:55
    now = _kst(2026, 5, 15, 17, 0)
    nxt = next_market_open(now)
    assert nxt.weekday() == 0   # Monday
    assert nxt.day == 18
    assert nxt.hour == 8 and nxt.minute == 55


def test_next_open_from_saturday_morning_returns_monday():
    # 토 09:00 → 월 08:55
    now = _kst(2026, 5, 16, 9, 0)
    nxt = next_market_open(now)
    assert nxt.weekday() == 0   # Monday
    assert nxt.hour == 8 and nxt.minute == 55


def test_next_open_from_early_morning_returns_today():
    # 월 06:00 → 월 08:55
    now = _kst(2026, 5, 11, 6, 0)
    nxt = next_market_open(now)
    assert nxt.day == 11
    assert nxt.hour == 8 and nxt.minute == 55


def test_market_constants_kospi_standard():
    assert MARKET_OPEN_HHMM == (9, 0)
    assert MARKET_CLOSE_HHMM == (15, 30)
