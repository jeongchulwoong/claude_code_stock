"""
core/daytrade_ml_features.py — 분단타 ML feature dict 생성 (순수 함수).

설계 원칙:
  - 순수 함수. 시간/네트워크/랜덤/IO 0건. 호출자가 모든 입력을 제공.
  - **look-ahead bias 절대 금지** — snapshot 시점에 이미 알 수 있는 값만 사용.
  - 출력 dict 의 모든 값은 JSON 직렬화 가능 (float/int/bool/str). NaN/inf 는 0.0 으로 정리.
  - 민감 키 (account_no/token/appkey/secret/authorization/bearer) 는 키/값 모두 포함하지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from core.daytrade_ml_schema import (
    FEATURE_VERSION,
    complete_daytrade_feature_schema,
)
from core.ma120_slope import evaluate_ma120_slope_quality, normalize_distance_by_atr

# 절대 feature dict 키/값에 들어가서는 안 되는 토큰.
_FORBIDDEN_SUBSTRINGS = (
    "account_no",
    "account-no",
    "token",
    "appkey",
    "secret",
    "password",
    "authorization",
    "bearer",
)


def _sanitize_float(v: Any, default: float = 0.0) -> float:
    """NaN / inf / 변환 실패는 모두 default 로."""
    try:
        n = float(v)
    except Exception:
        return default
    if n != n:           # NaN
        return default
    if n in (float("inf"), float("-inf")):
        return default
    return n


def _sanitize_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _gap_pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _imbalance(left: float, right: float) -> float:
    denom = left + right
    if denom <= 0:
        return 0.0
    return (left - right) / denom


def _has_forbidden_token(s: str) -> bool:
    low = s.lower()
    return any(bad in low for bad in _FORBIDDEN_SUBSTRINGS)


def _strip_sensitive_keys(d: dict) -> dict:
    """key 또는 string value 에 민감 토큰이 있으면 제거.

    nested dict 는 1단계만 처리 — feature dict 는 평탄해야 한다 (정책).
    """
    out: dict = {}
    for k, v in d.items():
        if not isinstance(k, str):
            continue
        if _has_forbidden_token(k):
            continue
        if isinstance(v, str) and _has_forbidden_token(v):
            continue
        out[k] = v
    return out


def _ai_fields(ai_verdict: Any) -> dict[str, Any]:
    """AIVerdict / IntegratedVerdict / CachedAIVerdict / dict / None 어느 형태든 안전 추출."""
    if ai_verdict is None:
        return {
            "ai_confidence": 0,
            "ai_news_blocked": False,
            "ai_is_executable": False,
            "ai_news_score": 0,
        }
    if is_dataclass(ai_verdict) and not isinstance(ai_verdict, type):
        d = asdict(ai_verdict)
    elif isinstance(ai_verdict, dict):
        d = dict(ai_verdict)
    else:
        d = {
            "confidence":     getattr(ai_verdict, "confidence", 0),
            "news_blocked":   getattr(ai_verdict, "news_blocked", False),
            "news_score":     getattr(ai_verdict, "news_score", 0),
        }
        # is_executable 은 property 일 수 있음.
        try:
            d["is_executable"] = bool(getattr(ai_verdict, "is_executable"))
        except Exception:
            d["is_executable"] = False
    return {
        "ai_confidence":     _sanitize_int(d.get("confidence", 0), 0),
        "ai_news_blocked":   bool(d.get("news_blocked", False)),
        "ai_is_executable":  bool(d.get("is_executable", False)),
        "ai_news_score":     _sanitize_int(d.get("news_score", 0), 0),
    }


def build_daytrade_entry_features(
    snap: Any,
    *,
    fast_score: Any,
    strategies_passed: int,
    strategies_required: int,
    ai_verdict: Any = None,
    market_context: dict | None = None,
) -> dict:
    """
    snapshot + fast_score + AI verdict + 시장 context → ML feature dict.

    Args:
      snap: StockSnapshot.
      fast_score: DaytradeFastScore.
      strategies_passed/required: int.
      ai_verdict: AIVerdict / IntegratedVerdict / CachedAIVerdict / None.
      market_context: optional {"minutes_since_open": int, "kospi_pct": float, ...}.

    Returns:
      feature dict (모든 값 JSON 직렬화 가능).
    """
    ticker = str(getattr(snap, "ticker", "") or "")
    price  = _sanitize_float(getattr(snap, "current_price", 0))
    ma5    = _sanitize_float(getattr(snap, "ma5", 0))
    ma20   = _sanitize_float(getattr(snap, "ma20", 0))
    ma120  = _sanitize_float(getattr(snap, "ma120", 0))
    rsi    = _sanitize_float(getattr(snap, "rsi", 0))
    vol_r  = _sanitize_float(getattr(snap, "volume_ratio", 1.0), default=1.0)
    atr_p  = _sanitize_float(getattr(snap, "atr_pct", 0))
    spread = _sanitize_float(getattr(snap, "spread_pct", 0))
    rise1m = _sanitize_float(getattr(snap, "rise_1m_pct", 0))
    rise3m = _sanitize_float(getattr(snap, "rise_3m_pct", 0))
    bid_price = _sanitize_float(getattr(snap, "bid_price", 0))
    ask_price = _sanitize_float(getattr(snap, "ask_price", 0))
    bid_qty = _sanitize_float(getattr(snap, "bid_qty", 0))
    ask_qty = _sanitize_float(getattr(snap, "ask_qty", 0))
    bid_ask_ratio = _sanitize_float(getattr(snap, "bid_ask_ratio", 0))
    queue_imbalance = _imbalance(bid_qty, ask_qty)
    microprice = 0.0
    if bid_qty + ask_qty > 0 and bid_price > 0 and ask_price > 0:
        microprice = (ask_price * bid_qty + bid_price * ask_qty) / (bid_qty + ask_qty)
    microprice_gap_pct = _gap_pct(microprice, price) if microprice > 0 and price > 0 else 0.0
    event_volume_zscore = _sanitize_float(getattr(snap, "volume_zscore", 0))
    event_range_pct = _sanitize_float(getattr(snap, "range_pct", atr_p))
    event_range_zscore = _sanitize_float(getattr(snap, "range_zscore", 0))
    event_vwap_gap_pct = _sanitize_float(getattr(snap, "vwap_gap_pct", 0))
    event_prev_high_gap_pct = _sanitize_float(getattr(snap, "prev_high_gap_pct", 0))
    event_breakout_strength_pct = _sanitize_float(getattr(snap, "breakout_strength_pct", 0))
    event_wick_upper_pct = _sanitize_float(getattr(snap, "wick_upper_pct", 0))
    event_body_pct = _sanitize_float(getattr(snap, "body_pct", 0))
    event_momentum_5m_pct = _sanitize_float(getattr(snap, "rise_5m_pct", rise3m))

    fast_s = _sanitize_int(getattr(fast_score, "score", 0), 0)
    sp     = _sanitize_int(strategies_passed, 0)
    sr     = max(1, _sanitize_int(strategies_required, 1))
    pass_ratio = sp / sr if sr else 0.0

    # MA120 slope confluence (paper-only safe; raise 안 함).
    # snap.ma120_then 이 있으면 사용, 없으면 ma120 동일값 → slope=0 (flat).
    ma120_then = _sanitize_float(getattr(snap, "ma120_then", ma120), default=ma120)
    atr_value = _sanitize_float(getattr(snap, "atr", 0.0), default=0.0)
    if atr_value <= 0 and atr_p > 0 and price > 0:
        # snap 이 atr (절대값) 안 갖고 atr_pct (%) 만 가질 때 추정.
        atr_value = price * (atr_p / 100.0)
    slope_quality = evaluate_ma120_slope_quality(
        current_price=price, ma120_now=ma120, ma120_then=ma120_then,
    )
    ma120_distance_atr_signed = 0.0
    if atr_value > 0 and ma120 > 0 and price > 0:
        raw_dist = normalize_distance_by_atr(price, ma120, atr_value)
        # raw_dist 는 항상 양수. above/below 부호를 부여해 신호 보존.
        if raw_dist != 999.0:
            ma120_distance_atr_signed = raw_dist if price >= ma120 else -raw_dist

    ai = _ai_fields(ai_verdict)

    minutes_since_open = 0
    if isinstance(market_context, dict):
        minutes_since_open = _sanitize_int(market_context.get("minutes_since_open", 0), 0)

    feats: dict[str, Any] = {
        "feature_version":     FEATURE_VERSION,
        "ticker":              ticker,
        "price":               price,
        "ma5":                 ma5,
        "ma20":                ma20,
        "ma120":               ma120,
        "price_ma20_gap_pct":  _gap_pct(price, ma20),
        "price_ma120_gap_pct": _gap_pct(price, ma120),
        "ma5_ma20_gap_pct":    _gap_pct(ma5, ma20),
        "ma120_slope_pct":          slope_quality.slope_pct,
        "ma120_above":              1 if slope_quality.above_ma120 else 0,
        "ma120_quality_strong_up":  1 if slope_quality.quality == "strong_up"   else 0,
        "ma120_quality_weak_up":    1 if slope_quality.quality == "weak_up"     else 0,
        "ma120_quality_flat":       1 if slope_quality.quality == "flat"        else 0,
        "ma120_quality_weak_down":  1 if slope_quality.quality == "weak_down"   else 0,
        "ma120_quality_strong_down":1 if slope_quality.quality == "strong_down" else 0,
        "ma120_confluence_score":   slope_quality.confluence_score,
        "ma120_distance_atr":       ma120_distance_atr_signed,
        "rsi":                 rsi,
        "volume_ratio":        vol_r,
        "atr_pct":             atr_p,
        "spread_pct":          spread,
        "bid_price":           bid_price,
        "ask_price":           ask_price,
        "bid_qty":             bid_qty,
        "ask_qty":             ask_qty,
        "bid_ask_ratio":       bid_ask_ratio,
        "queue_imbalance":     queue_imbalance,
        "microprice_gap_pct":  microprice_gap_pct,
        "rise_1m_pct":         rise1m,
        "rise_3m_pct":         rise3m,
        "strategies_passed":   sp,
        "strategies_required": sr,
        "strategy_pass_ratio": _sanitize_float(pass_ratio, 0.0),
        "fast_score":          fast_s,
        "ai_confidence":       ai["ai_confidence"],
        "ai_news_blocked":     ai["ai_news_blocked"],
        "ai_is_executable":    ai["ai_is_executable"],
        "ai_news_score":       ai["ai_news_score"],
        "minutes_since_open":  minutes_since_open,
        "event_volume_zscore":             event_volume_zscore,
        "event_volume_ratio_20":           vol_r,
        "event_value_ratio_20":            _sanitize_float(getattr(snap, "value_ratio_20", vol_r), 1.0),
        "event_cumulative_value":          _sanitize_float(getattr(snap, "cumulative_value", 0.0)),
        "event_range_pct":                 event_range_pct,
        "event_range_zscore":              event_range_zscore,
        "event_vwap_gap_pct":              event_vwap_gap_pct,
        "event_prev_high_gap_pct":         event_prev_high_gap_pct,
        "event_breakout_strength_pct":     event_breakout_strength_pct,
        "event_wick_upper_pct":            event_wick_upper_pct,
        "event_body_pct":                  event_body_pct,
        "event_momentum_1m_pct":           rise1m,
        "event_momentum_3m_pct":           rise3m,
        "event_momentum_5m_pct":           event_momentum_5m_pct,
        "event_realized_vol_pct":          atr_p,
        "event_liquidity_event_rank":      fast_s,
        "event_barrier_upper_pct":         0.0,
        "event_barrier_lower_pct":         0.0,
        "event_cost_pct":                  0.0,
        "event_count":                     0,
        "event_volume_spike":              1 if vol_r >= 2.0 or event_volume_zscore >= 2.0 else 0,
        "event_rolling_high_breakout":     1 if event_breakout_strength_pct > 0 else 0,
        "event_vwap_recovery":             1 if event_vwap_gap_pct > 0 else 0,
        "event_short_momentum":            1 if rise3m >= 0.25 and rise1m > 0 else 0,
        "event_range_expansion":           1 if event_range_zscore >= 1.5 else 0,
        "event_false_breakout_candidate":  0,
        # Cross-sectional ranks are known only when scoring a candidate batch.
        # Single-snapshot live scoring uses neutral values, while offline
        # event datasets fill these from same-minute peers.
        "xs_volume_ratio_rank":          0.5,
        "xs_event_volume_zscore_rank":   0.5,
        "xs_event_cumulative_value_rank": 0.5,
        "xs_liquidity_event_rank":       0.5,
        "xs_event_momentum_3m_rank":     0.5,
        "xs_event_range_zscore_rank":    0.5,
        "xs_fast_score_rank":            0.5,
    }
    return _strip_sensitive_keys(complete_daytrade_feature_schema(feats))
