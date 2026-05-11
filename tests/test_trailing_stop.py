"""tests/test_trailing_stop.py — TrailingStop 단위 테스트.

main / order_manager / risk_manager 를 import 하지 않는다.
broker 영향 0 — 본 모듈은 stop 가격 계산만, 청산 주문 발생 X.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trailing_stop import (   # noqa: E402
    TRAILING_STOP_VERSION,
    TrailingStopState,
    init_trailing_stop,
    is_triggered,
    simulate_trade,
    update_trailing_stop,
)


# ──────────────────────────────────────────────────────────────────
# 초기화
# ──────────────────────────────────────────────────────────────────

def test_init_long_sets_initial_stop_below_entry():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0,
                            is_long=True, initial_atr_mult=1.5)
    assert s.current_stop == 9850.0   # 10000 - 1.5*100
    assert s.stage == 0
    assert s.high_water_mark == 10000.0


def test_init_short_sets_initial_stop_above_entry():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0,
                            is_long=False, initial_atr_mult=1.5)
    assert s.current_stop == 10150.0   # 10000 + 1.5*100


def test_init_with_invalid_atr_falls_back_to_entry():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=0.0)
    assert s.current_stop == 10000.0   # ATR invalid → stop=entry (BE)


# ──────────────────────────────────────────────────────────────────
# 단계 진입 (롱)
# ──────────────────────────────────────────────────────────────────

def test_no_lockin_when_profit_below_step0():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    # high 10080 → profit_atr = 0.8 < step[0]=1.0 → 초기 유지
    s2 = update_trailing_stop(s, candle_high=10080, candle_low=10050)
    assert s2.stage == 0
    assert s2.current_stop == 9850.0   # 초기 유지


def test_break_even_lockin_at_step0():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    # high 10150 → profit_atr = 1.5 ≥ step[0]=1.0 → BE
    s2 = update_trailing_stop(s, candle_high=10150, candle_low=10100)
    assert s2.stage == 1
    assert s2.current_stop == 10000.0   # entry (BE)


def test_plus_one_atr_lockin_at_step1():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    # high 10220 → profit_atr = 2.2 ≥ step[1]=2.0 → +1 ATR
    s2 = update_trailing_stop(s, candle_high=10220, candle_low=10100)
    assert s2.stage == 2
    assert s2.current_stop == 10100.0   # entry + (2.0-1.0)*100 = +1 ATR


def test_chandelier_mode_at_step2():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    # high 10350 → profit_atr = 3.5 ≥ step[2]=3.0 → chandelier
    s2 = update_trailing_stop(s, candle_high=10350, candle_low=10200,
                               chandelier_atr=1.5)
    assert s2.stage == 3
    assert s2.current_stop == 10200.0   # 10350 - 1.5*100


# ──────────────────────────────────────────────────────────────────
# 단조 증가 (롱)
# ──────────────────────────────────────────────────────────────────

def test_stop_monotonically_increases():
    """가격이 떨어져도 stop 은 절대 내려가지 않는다."""
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    s = update_trailing_stop(s, candle_high=10350, candle_low=10200, chandelier_atr=1.5)
    assert s.current_stop == 10200.0
    # 다음 봉: 가격 후퇴 (high 10100 < HWM 10350)
    s2 = update_trailing_stop(s, candle_high=10100, candle_low=10050, chandelier_atr=1.5)
    # HWM 유지 → stop 유지
    assert s2.high_water_mark == 10350.0
    assert s2.current_stop == 10200.0   # 감소 안 함


def test_stage_monotonically_increases():
    """stage 도 단조 증가 (한 번 진입한 단계에서 내려가지 않음)."""
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    s = update_trailing_stop(s, candle_high=10350, candle_low=10200)
    assert s.stage == 3
    # 다음 봉: 가격 후퇴
    s2 = update_trailing_stop(s, candle_high=10100, candle_low=10050)
    assert s2.stage == 3   # 단조 증가


# ──────────────────────────────────────────────────────────────────
# Trigger 판정
# ──────────────────────────────────────────────────────────────────

def test_trigger_when_low_touches_stop_long():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    s = update_trailing_stop(s, candle_high=10350, candle_low=10200, chandelier_atr=1.5)
    # current_stop = 10200. 다음 봉 low=10180 → trigger
    assert is_triggered(s, candle_low=10180, candle_high=10250) is True
    assert is_triggered(s, candle_low=10210, candle_high=10250) is False


def test_trigger_when_high_touches_stop_short():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0, is_long=False)
    # short — initial stop = 10150
    assert is_triggered(s, candle_low=10100, candle_high=10160) is True
    assert is_triggered(s, candle_low=10100, candle_high=10140) is False


# ──────────────────────────────────────────────────────────────────
# 숏 미러
# ──────────────────────────────────────────────────────────────────

def test_short_break_even_lockin():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0, is_long=False)
    # low 9850 → profit_atr = 1.5 (entry-low/atr) → BE
    s2 = update_trailing_stop(s, candle_high=9920, candle_low=9850)
    assert s2.stage == 1
    assert s2.current_stop == 10000.0   # entry (BE)


def test_short_chandelier_mode():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0, is_long=False)
    # low 9650 → profit_atr = 3.5 → chandelier (LWM + cand*atr)
    s2 = update_trailing_stop(s, candle_high=9750, candle_low=9650, chandelier_atr=1.5)
    assert s2.stage == 3
    assert s2.current_stop == 9800.0   # 9650 + 1.5*100


# ──────────────────────────────────────────────────────────────────
# 가비지 입력 안전성
# ──────────────────────────────────────────────────────────────────

def test_update_with_zero_high_low_returns_unchanged():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    s2 = update_trailing_stop(s, candle_high=0, candle_low=0)
    assert s2.current_stop == s.current_stop
    assert s2.stage == s.stage


def test_update_with_invalid_atr_returns_unchanged():
    s = TrailingStopState(entry_price=10000.0, entry_atr_value=0.0,
                           is_long=True, current_stop=9850.0)
    s2 = update_trailing_stop(s, candle_high=10150, candle_low=10000)
    assert s2 is s   # 변경 없음


def test_is_triggered_with_garbage_returns_false():
    s = init_trailing_stop(entry_price=10000.0, entry_atr_value=100.0)
    assert is_triggered(s, candle_low=0, candle_high=0) is False
    assert is_triggered(s, candle_low=float("nan"), candle_high=10000) is False


# ──────────────────────────────────────────────────────────────────
# simulate_trade — 시나리오
# ──────────────────────────────────────────────────────────────────

def test_simulate_horizon_exit_when_no_stop_hit():
    bars = [
        {"minute_offset": 1, "high": 10050, "low": 10000, "close": 10030},
        {"minute_offset": 2, "high": 10080, "low": 10020, "close": 10060},
        {"minute_offset": 3, "high": 10100, "low": 10050, "close": 10090},
    ]
    out = simulate_trade(entry_price=10000.0, entry_atr_value=100.0,
                          future_bars=bars, horizon_minutes=10, cost_pct=0.4)
    assert out["exit_reason"] == "horizon"
    assert out["exit_minute"] == 3
    assert out["exit_price"] == 10090
    # gross 0.9% → net 0.5%
    assert abs(out["gross_return_pct"] - 0.9) < 1e-6
    assert abs(out["net_return_pct"] - 0.5) < 1e-6


def test_simulate_trailing_stop_triggered():
    bars = [
        {"minute_offset": 1, "high": 10350, "low": 10100, "close": 10300},  # chandelier 모드
        {"minute_offset": 2, "high": 10250, "low": 10180, "close": 10200},  # low 10180 < stop 10200 → trigger
    ]
    out = simulate_trade(entry_price=10000.0, entry_atr_value=100.0,
                          future_bars=bars, horizon_minutes=10,
                          chandelier_atr=1.5, cost_pct=0.4)
    assert out["exit_reason"] == "trailing_stop"
    assert out["exit_price"] == 10200.0   # chandelier stop
    assert out["max_stage"] == 3
    # gross +2% → net 1.6%
    assert abs(out["gross_return_pct"] - 2.0) < 1e-6
    assert abs(out["net_return_pct"] - 1.6) < 1e-6


def test_simulate_initial_stop_hit_first_bar():
    bars = [
        {"minute_offset": 1, "high": 10000, "low": 9800, "close": 9820},  # low 9800 < init_stop 9850
    ]
    out = simulate_trade(entry_price=10000.0, entry_atr_value=100.0,
                          future_bars=bars, horizon_minutes=10,
                          initial_atr_mult=1.5, cost_pct=0.4)
    assert out["exit_reason"] == "trailing_stop"
    assert out["exit_price"] == 9850.0   # 초기 stop
    assert out["max_stage"] == 0
    # gross -1.5% → net -1.9%
    assert abs(out["gross_return_pct"] - (-1.5)) < 1e-6


def test_simulate_no_bars_safe_return():
    out = simulate_trade(entry_price=10000.0, entry_atr_value=100.0,
                          future_bars=[], horizon_minutes=10)
    assert out["exit_reason"] == "no_bars"
    assert out["bars_held"] == 0


def test_simulate_invalid_atr_returns_safe():
    out = simulate_trade(entry_price=10000.0, entry_atr_value=0.0,
                          future_bars=[{"minute_offset": 1, "high": 10050, "low": 10000, "close": 10030}],
                          horizon_minutes=10)
    assert out["exit_reason"] == "invalid_atr"


def test_simulate_records_max_profit_atr_for_chandelier_event():
    bars = [
        {"minute_offset": 1, "high": 10400, "low": 10100, "close": 10350},  # profit_atr 4.0
        {"minute_offset": 2, "high": 10200, "low": 10240, "close": 10240},  # trigger at chandelier stop
    ]
    out = simulate_trade(entry_price=10000.0, entry_atr_value=100.0,
                          future_bars=bars, horizon_minutes=10, cost_pct=0.4)
    assert out["max_profit_atr"] >= 4.0
    assert out["max_stage"] == 3
