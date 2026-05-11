"""tests/test_direction_filter.py — DirectionFilter 단위 테스트."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.direction_filter import (   # noqa: E402
    TrendDirection,
    compute_ema,
    compute_slope,
    get_direction,
    has_higher_low,
    has_lower_high,
)


def test_compute_ema_returns_sma_seed():
    ema = compute_ema([10, 20, 30, 40, 50], period=2)
    # 첫 EMA = (10+20)/2 = 15
    assert ema[0] == 15
    # 그 후 EMA[1] = α*30 + (1-α)*15, α = 2/3
    expected_1 = (2/3) * 30 + (1/3) * 15
    assert abs(ema[1] - expected_1) < 1e-9


def test_compute_ema_returns_empty_when_too_short():
    assert compute_ema([10, 20], period=5) == []


def test_compute_slope_positive_for_increasing():
    assert compute_slope([1, 2, 3, 4, 5]) > 0


def test_compute_slope_negative_for_decreasing():
    assert compute_slope([5, 4, 3, 2, 1]) < 0


def test_compute_slope_zero_for_flat():
    assert compute_slope([5, 5, 5, 5]) == 0.0


def test_compute_slope_handles_short_input():
    assert compute_slope([5]) == 0.0
    assert compute_slope([]) == 0.0


def test_has_higher_low_true_when_monotonic_increasing():
    assert has_higher_low([100, 101, 102, 103, 104], n=5) is True


def test_has_higher_low_false_when_not_monotonic():
    assert has_higher_low([100, 102, 101, 103, 104], n=5) is False


def test_has_lower_high_true_when_monotonic_decreasing():
    assert has_lower_high([105, 104, 103, 102, 101], n=5) is True


def test_has_lower_high_false_when_not_monotonic():
    assert has_lower_high([105, 103, 104, 102, 101], n=5) is False


def test_get_direction_undefined_when_insufficient_data():
    out = get_direction([100, 101, 102], [101, 102, 103], [99, 100, 101],
                         ema_period=20)
    assert out.direction == TrendDirection.UNDEFINED


def test_get_direction_up_when_ema_slope_positive_and_higher_lows():
    # 점진적 상승 — closes 가 일관되게 상승, lows 도 단조 증가
    closes = list(range(100, 130))   # 30 개
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]   # lows 도 단조 증가
    out = get_direction(closes, highs, lows,
                         ema_period=20, slope_lookback=3, structure_lookback=5)
    assert out.direction == TrendDirection.UP
    assert out.ema_slope > 0
    assert out.higher_low is True


def test_get_direction_down_when_ema_slope_negative_and_lower_highs():
    closes = list(range(130, 100, -1))   # 점진적 하락
    highs = [c + 1 for c in closes]   # highs 도 단조 감소
    lows = [c - 1 for c in closes]
    out = get_direction(closes, highs, lows,
                         ema_period=20, slope_lookback=3, structure_lookback=5)
    assert out.direction == TrendDirection.DOWN
    assert out.ema_slope < 0
    assert out.lower_high is True


def test_get_direction_neutral_when_slope_positive_but_structure_not():
    # 상승 추세이지만 lows 가 단조 증가 X (whipsaw)
    closes = list(range(100, 130))
    highs = [c + 1 for c in closes]
    lows = [c - 1 if i % 2 == 0 else c - 3 for i, c in enumerate(closes)]   # 불규칙
    out = get_direction(closes, highs, lows,
                         ema_period=20, slope_lookback=3, structure_lookback=5)
    # slope+ 이지만 higher-low confirm 실패 → NEUTRAL
    assert out.direction == TrendDirection.NEUTRAL


def test_get_direction_neutral_when_slope_below_noise_floor():
    # 거의 flat — slope_min_abs 가 잡음 필터
    closes = [100 + (i % 2) * 0.01 for i in range(30)]   # 거의 flat
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    out = get_direction(closes, highs, lows,
                         ema_period=20, slope_lookback=3, structure_lookback=5,
                         slope_min_abs=0.5)   # 0.5 미만은 NEUTRAL
    assert out.direction == TrendDirection.NEUTRAL


def test_get_direction_safe_on_invalid_input():
    out = get_direction([float("nan")] * 30, [0] * 30, [0] * 30,
                         ema_period=20)
    # nan 은 _safe_float 로 0 처리 → UNDEFINED 또는 NEUTRAL
    assert out.direction in (TrendDirection.UNDEFINED, TrendDirection.NEUTRAL)
