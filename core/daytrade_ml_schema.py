"""
Canonical daytrade ML feature schema.

This module is the single source of truth shared by runtime scoring, offline
event dataset building, and model training. Broker/order/live gate code does
not depend on it for execution decisions.
"""

from __future__ import annotations

from typing import Any


FEATURE_VERSION = "daytrade_v1"

CORE_FEATURE_COLUMNS: tuple[str, ...] = (
    "feature_version",
    "ticker",
    "price",
    "ma5",
    "ma20",
    "ma120",
    "price_ma20_gap_pct",
    "price_ma120_gap_pct",
    "ma5_ma20_gap_pct",
    # MA120 slope/quality confluence (Avellaneda-Lee 2010 — slope > level for trend quality).
    # 학습/라이브 양쪽 동일 정의로 채워야 분포 일치. ma120_then 은 N봉 lookback (default 5분).
    "ma120_slope_pct",          # signed % change of ma120 over lookback
    "ma120_above",              # 1 if current_price > ma120, else 0
    "ma120_quality_strong_up",  # 1 if classified strong_up
    "ma120_quality_weak_up",    # 1 if classified weak_up
    "ma120_quality_flat",       # 1 if classified flat
    "ma120_quality_weak_down",  # 1 if classified weak_down
    "ma120_quality_strong_down",# 1 if classified strong_down
    "ma120_confluence_score",   # 0..3 (above + slope_up + strong_up)
    "ma120_distance_atr",       # |price-ma120|/atr (signed: positive=above)
    "rsi",
    "volume_ratio",
    "atr_pct",
    "spread_pct",
    "bid_price",
    "ask_price",
    "bid_qty",
    "ask_qty",
    "bid_ask_ratio",
    "queue_imbalance",
    "microprice_gap_pct",
    "rise_1m_pct",
    "rise_3m_pct",
    "strategies_passed",
    "strategies_required",
    "strategy_pass_ratio",
    "fast_score",
    "ai_confidence",
    "ai_news_blocked",
    "ai_is_executable",
    "ai_news_score",
    "minutes_since_open",
)

MICROSTRUCTURE_FEATURE_COLUMNS: tuple[str, ...] = (
    "has_microstructure_data",
)

# 라이브 / 학습 분포 일치를 위한 microstructure 프레즌스 정의:
#   bid_qty, ask_qty, queue_imbalance, microprice_gap_pct 중 적어도 하나가 0 이 아니면
#   해당 candidate 는 호가/체결 관측값을 가진 것으로 간주.
#   학습 데이터셋 빌더와 라이브 피처 빌더가 동일 정의를 사용한다.
MICROSTRUCTURE_SOURCE_COLUMNS: tuple[str, ...] = (
    "bid_qty",
    "ask_qty",
    "queue_imbalance",
    "microprice_gap_pct",
)

EVENT_FEATURE_COLUMNS: tuple[str, ...] = (
    "event_volume_zscore",
    "event_volume_ratio_20",
    "event_value_ratio_20",
    "event_cumulative_value",
    "event_range_pct",
    "event_range_zscore",
    "event_vwap_gap_pct",
    "event_prev_high_gap_pct",
    "event_breakout_strength_pct",
    "event_wick_upper_pct",
    "event_body_pct",
    "event_momentum_1m_pct",
    "event_momentum_3m_pct",
    "event_momentum_5m_pct",
    "event_realized_vol_pct",
    "event_liquidity_event_rank",
    "event_barrier_upper_pct",
    "event_barrier_lower_pct",
    "event_cost_pct",
    "event_count",
    "event_volume_spike",
    "event_rolling_high_breakout",
    "event_vwap_recovery",
    "event_short_momentum",
    "event_range_expansion",
    "event_false_breakout_candidate",
    "hard_negative_flag",
    "label_horizon_minutes",
    "sample_weight",
)

CROSS_SECTIONAL_FEATURE_COLUMNS: tuple[str, ...] = (
    "xs_volume_ratio_rank",
    "xs_event_volume_zscore_rank",
    "xs_event_cumulative_value_rank",
    "xs_liquidity_event_rank",
    "xs_event_momentum_3m_rank",
    "xs_event_range_zscore_rank",
    "xs_fast_score_rank",
)

NON_MODEL_COLUMNS: set[str] = {
    "feature_version",
    "ticker",
    "hard_negative_flag",
    "label_horizon_minutes",
    "sample_weight",
}

MODEL_FEATURE_COLUMNS: tuple[str, ...] = tuple(
    col
    for col in (
        CORE_FEATURE_COLUMNS
        + EVENT_FEATURE_COLUMNS
        + CROSS_SECTIONAL_FEATURE_COLUMNS
        + MICROSTRUCTURE_FEATURE_COLUMNS
    )
    if col not in NON_MODEL_COLUMNS
)


def has_microstructure_data(features: dict[str, Any]) -> bool:
    """Source 컬럼 중 하나라도 비-zero 면 microstructure 관측이 있는 것으로 본다.

    학습 빌더 / 라이브 피처 양쪽이 모두 동일 정의로 채워야 분포가 일치한다.
    """
    for col in MICROSTRUCTURE_SOURCE_COLUMNS:
        try:
            v = float((features or {}).get(col, 0.0) or 0.0)
        except Exception:
            continue
        if v != 0.0 and v == v and v not in (float("inf"), float("-inf")):
            return True
    return False

_NEUTRAL_HALF_COLUMNS = {
    "xs_volume_ratio_rank",
    "xs_event_volume_zscore_rank",
    "xs_event_cumulative_value_rank",
    "xs_liquidity_event_rank",
    "xs_event_momentum_3m_rank",
    "xs_event_range_zscore_rank",
    "xs_fast_score_rank",
}

_ONE_DEFAULT_COLUMNS = {
    "volume_ratio",
    "event_volume_ratio_20",
    "event_value_ratio_20",
    "strategies_required",
}

_EVENT_FLAG_COLUMNS = (
    "event_volume_spike",
    "event_rolling_high_breakout",
    "event_vwap_recovery",
    "event_short_momentum",
    "event_range_expansion",
    "event_false_breakout_candidate",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value if value is not None else default)
    except Exception:
        return default
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _default_for_column(column: str) -> float:
    if column in _NEUTRAL_HALF_COLUMNS:
        return 0.5
    if column in _ONE_DEFAULT_COLUMNS:
        return 1.0
    return 0.0


def complete_daytrade_feature_schema(features: dict[str, Any]) -> dict[str, Any]:
    """Return a feature dict with every canonical model feature present.

    `has_microstructure_data` 는 source 컬럼 (bid_qty/ask_qty/queue_imbalance/
    microprice_gap_pct) 의 실측값에서 자동 도출한다. 호출자가 직접 1/0 을 박지 않아도
    학습 / 라이브 양쪽이 동일 정의로 채워진다.
    """
    out = dict(features or {})
    out["feature_version"] = str(out.get("feature_version") or FEATURE_VERSION)

    for column in MODEL_FEATURE_COLUMNS:
        if column not in out or out.get(column) is None:
            out[column] = _default_for_column(column)

    if _safe_float(out.get("event_liquidity_event_rank"), 0.0) <= 0:
        out["event_liquidity_event_rank"] = _safe_float(out.get("fast_score"), 0.0)

    if _safe_float(out.get("event_count"), 0.0) <= 0:
        out["event_count"] = int(sum(1 for col in _EVENT_FLAG_COLUMNS if _safe_float(out.get(col), 0.0) > 0))

    out["has_microstructure_data"] = 1 if has_microstructure_data(out) else 0

    return out


def schema_coverage(features: dict[str, Any]) -> dict[str, Any]:
    """Small diagnostic payload for train/prod feature-contract checks."""
    keys = set((features or {}).keys())
    model_keys = set(MODEL_FEATURE_COLUMNS)
    missing = sorted(model_keys - keys)
    extra = sorted(keys - model_keys - {"feature_version", "ticker"})
    return {
        "feature_version": str((features or {}).get("feature_version") or ""),
        "model_feature_count": len(MODEL_FEATURE_COLUMNS),
        "present_model_feature_count": len(model_keys & keys),
        "missing_model_feature_count": len(missing),
        "missing_model_features": missing,
        "extra_feature_count": len(extra),
        "extra_features": extra,
    }
