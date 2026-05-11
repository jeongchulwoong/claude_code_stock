"""
core/direction_filter.py — 5분봉 추세 방향 필터 (paper-only research).

사용자 spec section 4.2 구현.

규칙:
  UP   : EMA20 기울기 > 0 AND 최근 N개 봉이 higher-low 유지
  DOWN : EMA20 기울기 < 0 AND 최근 N개 봉이 lower-high 유지
  NEUTRAL: 그 외 (진입 차단)

  - 기울기는 최근 slope_lookback 개 EMA 값의 1차 linear regression 기울기
  - higher-low: 연속 N개 봉의 low 가 단조 증가
  - lower-high: 연속 N개 봉의 high 가 단조 감소

순수 함수 — broker / order 영향 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class TrendDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"
    UNDEFINED = "UNDEFINED"   # 데이터 부족 등


@dataclass(frozen=True)
class DirectionVerdict:
    direction: TrendDirection
    ema_value: float
    ema_slope: float
    higher_low: bool
    lower_high: bool
    reason: str = ""


def _safe_float(v, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        n = float(v)
    except Exception:
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n


def compute_ema(values: Sequence[float], period: int) -> list[float]:
    """단순 EMA 계산. values 가 period 보다 짧으면 빈 list 반환.

    EMA[t] = α * value[t] + (1-α) * EMA[t-1], α = 2/(period+1)
    """
    if period <= 0 or len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    # 초기값: 첫 period 의 단순 평균 (Wilder 방식 대신 SMA seed)
    seed = sum(values[:period]) / period
    ema = [seed]
    for v in values[period:]:
        ema.append(alpha * v + (1 - alpha) * ema[-1])
    return ema


def compute_slope(values: Sequence[float]) -> float:
    """1차 선형 회귀 기울기 (단위: 값/index)."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den > 0 else 0.0


def has_higher_low(lows: Sequence[float], n: int) -> bool:
    """최근 n 개 low 가 strict 단조 증가."""
    if len(lows) < n or n < 2:
        return False
    seg = list(lows[-n:])
    return all(seg[i] < seg[i + 1] for i in range(n - 1))


def has_lower_high(highs: Sequence[float], n: int) -> bool:
    """최근 n 개 high 가 strict 단조 감소."""
    if len(highs) < n or n < 2:
        return False
    seg = list(highs[-n:])
    return all(seg[i] > seg[i + 1] for i in range(n - 1))


def get_direction(
    closes: Sequence[float],
    highs:  Sequence[float],
    lows:   Sequence[float],
    *,
    ema_period: int = 20,
    slope_lookback: int = 3,
    structure_lookback: int = 5,
    slope_min_abs: float = 0.0,
) -> DirectionVerdict:
    """5분봉 closes/highs/lows 시퀀스 → 추세 방향 판정.

    Args:
      closes/highs/lows: 시간 순서 (오래된 → 최신).
      ema_period: EMA 길이 (기본 20)
      slope_lookback: 기울기 측정 EMA 샘플 수
      structure_lookback: higher-low / lower-high 확인 봉 수
      slope_min_abs: 기울기 절대값이 이 값 이하면 NEUTRAL (잡음 필터)
    """
    n = len(closes)
    if n < ema_period + slope_lookback - 1:
        return DirectionVerdict(
            direction=TrendDirection.UNDEFINED,
            ema_value=0.0, ema_slope=0.0,
            higher_low=False, lower_high=False,
            reason=f"insufficient data ({n} < {ema_period + slope_lookback - 1})",
        )

    ema = compute_ema([_safe_float(c) for c in closes], ema_period)
    if len(ema) < slope_lookback:
        return DirectionVerdict(
            direction=TrendDirection.UNDEFINED,
            ema_value=0.0, ema_slope=0.0,
            higher_low=False, lower_high=False,
            reason="ema too short",
        )

    recent_ema = ema[-slope_lookback:]
    slope = compute_slope(recent_ema)
    ema_value = recent_ema[-1]

    hl = has_higher_low([_safe_float(l) for l in lows], structure_lookback)
    lh = has_lower_high([_safe_float(h) for h in highs], structure_lookback)

    # 기울기가 잡음 수준이면 NEUTRAL
    if abs(slope) < slope_min_abs:
        return DirectionVerdict(
            direction=TrendDirection.NEUTRAL,
            ema_value=ema_value, ema_slope=slope,
            higher_low=hl, lower_high=lh,
            reason=f"slope {slope:.4f} below noise floor {slope_min_abs}",
        )

    if slope > 0 and hl:
        return DirectionVerdict(
            direction=TrendDirection.UP,
            ema_value=ema_value, ema_slope=slope,
            higher_low=hl, lower_high=lh,
        )
    if slope < 0 and lh:
        return DirectionVerdict(
            direction=TrendDirection.DOWN,
            ema_value=ema_value, ema_slope=slope,
            higher_low=hl, lower_high=lh,
        )
    return DirectionVerdict(
        direction=TrendDirection.NEUTRAL,
        ema_value=ema_value, ema_slope=slope,
        higher_low=hl, lower_high=lh,
        reason=f"slope {slope:.4f} but structure not confirming (hl={hl}, lh={lh})",
    )
