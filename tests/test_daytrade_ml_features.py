"""tests/test_daytrade_ml_features.py — ML feature dict 단위 테스트."""

from __future__ import annotations

import math
import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.daytrade_fast_score import score_daytrade_snapshot                      # noqa: E402
from core.daytrade_ml_features import FEATURE_VERSION, build_daytrade_entry_features  # noqa: E402
from core.daytrade_ml_schema import MODEL_FEATURE_COLUMNS, schema_coverage  # noqa: E402


@dataclass
class FakeSnap:
    ticker: str = "005930.KS"
    current_price: float = 70_000.0
    ma5: float = 69_500.0
    ma20: float = 68_000.0
    ma120: float = 60_000.0
    rsi: float = 60.0
    volume_ratio: float = 1.8
    atr_pct: float = 1.5
    spread_pct: float = 0.2
    bid_price: float = 69_900.0
    ask_price: float = 70_100.0
    bid_qty: int = 12_000
    ask_qty: int = 8_000
    bid_ask_ratio: float = 1.5
    rise_1m_pct: float = 0.3
    rise_3m_pct: float = 0.6


@dataclass
class FakeAI:
    confidence: int = 75
    news_blocked: bool = False
    news_score: int = 5

    @property
    def is_executable(self) -> bool:
        return not self.news_blocked and self.confidence >= 55


_RISK = {
    "rsi_buy_min": 45, "rsi_buy_max": 68,
    "daytrade_min_volume_ratio": 1.5, "daytrade_max_atr_pct": 3.0,
    "fast_score_min": 75,
}


def _build():
    snap = FakeSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    return build_daytrade_entry_features(
        snap, fast_score=fast, strategies_passed=3, strategies_required=3, ai_verdict=FakeAI(),
    )


def test_required_keys_present():
    f = _build()
    expected = {
        "feature_version", "ticker",
        "price", "ma5", "ma20", "ma120",
        "price_ma20_gap_pct", "price_ma120_gap_pct", "ma5_ma20_gap_pct",
        "rsi", "volume_ratio", "atr_pct", "spread_pct",
        "bid_price", "ask_price", "bid_qty", "ask_qty",
        "bid_ask_ratio", "queue_imbalance", "microprice_gap_pct",
        "rise_1m_pct", "rise_3m_pct",
        "strategies_passed", "strategies_required", "strategy_pass_ratio",
        "fast_score",
        "ai_confidence", "ai_news_blocked", "ai_is_executable",
        "minutes_since_open",
        "xs_volume_ratio_rank", "xs_event_volume_zscore_rank",
        "xs_event_cumulative_value_rank", "xs_liquidity_event_rank",
        "xs_event_momentum_3m_rank", "xs_event_range_zscore_rank",
        "xs_fast_score_rank",
        "event_value_ratio_20", "event_cumulative_value", "event_liquidity_event_rank",
    }
    missing = expected - set(f)
    assert not missing, f"missing keys: {missing}"
    assert f["feature_version"] == FEATURE_VERSION


def test_runtime_features_cover_canonical_model_schema():
    f = _build()
    coverage = schema_coverage(f)
    assert coverage["missing_model_feature_count"] == 0
    assert set(MODEL_FEATURE_COLUMNS).issubset(set(f))


def test_no_nan_or_inf_values():
    f = _build()
    for k, v in f.items():
        if isinstance(v, float):
            assert not math.isnan(v), f"NaN in {k}"
            assert not math.isinf(v), f"inf in {k}"


def test_gap_pct_calculations():
    f = _build()
    # price=70000, ma20=68000 → +2.94%
    assert abs(f["price_ma20_gap_pct"] - ((70000 - 68000) / 68000 * 100)) < 1e-6
    # price=70000, ma120=60000 → +16.66%
    assert abs(f["price_ma120_gap_pct"] - ((70000 - 60000) / 60000 * 100)) < 1e-6
    # ma5=69500, ma20=68000 → +2.20%
    assert abs(f["ma5_ma20_gap_pct"] - ((69500 - 68000) / 68000 * 100)) < 1e-6


def test_microstructure_features_are_computed_from_snapshot():
    f = _build()
    assert abs(f["queue_imbalance"] - 0.2) < 1e-9
    microprice = (70_100 * 12_000 + 69_900 * 8_000) / 20_000
    expected_gap = (microprice - 70_000) / 70_000 * 100
    assert abs(f["microprice_gap_pct"] - expected_gap) < 1e-9


def test_cross_sectional_rank_defaults_are_neutral_for_single_snapshot():
    f = _build()
    for key in (
        "xs_volume_ratio_rank",
        "xs_event_volume_zscore_rank",
        "xs_event_cumulative_value_rank",
        "xs_liquidity_event_rank",
        "xs_event_momentum_3m_rank",
        "xs_event_range_zscore_rank",
        "xs_fast_score_rank",
    ):
        assert f[key] == 0.5


def test_ma120_slope_features_present_with_neutral_default():
    """ma120_then 이 snap 에 없으면 slope=0 (flat), confluence_score 는 above 만 반영."""
    f = _build()
    # FakeSnap: price=70000 > ma120=60000 → above=1
    assert f["ma120_above"] == 1
    # ma120_then 없음 → slope=0 → flat
    assert f["ma120_slope_pct"] == 0.0
    assert f["ma120_quality_flat"] == 1
    assert f["ma120_quality_strong_up"] == 0
    # above only → confluence_score=1
    assert f["ma120_confluence_score"] == 1


def test_ma120_slope_strong_up_when_then_lower():
    @dataclass
    class TrendSnap:
        ticker: str = "005930.KS"
        current_price: float = 51_000.0
        ma5: float = 50_500.0
        ma20: float = 50_000.0
        ma120: float = 50_500.0
        ma120_then: float = 50_000.0   # +1.0% slope (strong_up)
        atr: float = 500.0
        rsi: float = 60.0
        volume_ratio: float = 1.8
        atr_pct: float = 1.0
        spread_pct: float = 0.2
        bid_price: float = 50_900.0
        ask_price: float = 51_100.0
        bid_qty: int = 12_000
        ask_qty: int = 8_000
        bid_ask_ratio: float = 1.5
        rise_1m_pct: float = 0.3
        rise_3m_pct: float = 0.6

    snap = TrendSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(snap, fast_score=fast, strategies_passed=3, strategies_required=3)
    assert abs(f["ma120_slope_pct"] - 1.0) < 1e-6
    assert f["ma120_quality_strong_up"] == 1
    assert f["ma120_above"] == 1
    # above + strong_up → confluence_score = 3
    assert f["ma120_confluence_score"] == 3
    # distance_atr: |51000 - 50500| / 500 = 1.0, signed positive (above)
    assert abs(f["ma120_distance_atr"] - 1.0) < 1e-6


def test_ma120_slope_below_strong_down():
    @dataclass
    class DownSnap:
        ticker: str = "005930.KS"
        current_price: float = 49_000.0
        ma5: float = 49_500.0
        ma20: float = 50_000.0
        ma120: float = 49_500.0
        ma120_then: float = 50_000.0   # -1.0% slope (strong_down)
        atr: float = 500.0
        rsi: float = 40.0
        volume_ratio: float = 1.0
        atr_pct: float = 1.0
        spread_pct: float = 0.2
        bid_price: float = 48_900.0
        ask_price: float = 49_100.0
        bid_qty: int = 5_000
        ask_qty: int = 5_000
        bid_ask_ratio: float = 1.0
        rise_1m_pct: float = -0.1
        rise_3m_pct: float = -0.2

    snap = DownSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=2, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(snap, fast_score=fast, strategies_passed=2, strategies_required=3)
    assert f["ma120_slope_pct"] < 0
    assert f["ma120_quality_strong_down"] == 1
    assert f["ma120_above"] == 0
    assert f["ma120_confluence_score"] == 0
    # distance signed negative (below)
    assert f["ma120_distance_atr"] < 0


def test_microstructure_presence_flag_set_when_quotes_observed():
    f = _build()
    # FakeSnap 는 bid_qty=12000, ask_qty=8000 → 호가 관측 있음 → flag=1
    assert f["has_microstructure_data"] == 1


def test_microstructure_presence_flag_zero_when_quotes_missing():
    @dataclass
    class EmptyMsSnap:
        ticker: str = "005930.KS"
        current_price: float = 70_000.0
        ma5: float = 69_500.0
        ma20: float = 68_000.0
        ma120: float = 60_000.0
        rsi: float = 60.0
        volume_ratio: float = 1.8
        atr_pct: float = 1.5
        spread_pct: float = 0.0
        bid_price: float = 0.0
        ask_price: float = 0.0
        bid_qty: int = 0
        ask_qty: int = 0
        bid_ask_ratio: float = 0.0
        rise_1m_pct: float = 0.3
        rise_3m_pct: float = 0.6

    snap = EmptyMsSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(snap, fast_score=fast, strategies_passed=3, strategies_required=3)
    assert f["has_microstructure_data"] == 0


def test_no_ai_verdict_returns_zeros():
    snap = FakeSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(
        snap, fast_score=fast, strategies_passed=3, strategies_required=3, ai_verdict=None,
    )
    assert f["ai_confidence"] == 0
    assert f["ai_is_executable"] is False
    assert f["ai_news_blocked"] is False


def test_no_sensitive_keys_present():
    f = _build()
    for forbidden in ("account_no", "token", "appkey", "secret", "authorization", "bearer", "password"):
        for k in f:
            assert forbidden not in k.lower(), f"sensitive key leaked: {k}"


def test_sensitive_value_in_string_field_stripped():
    """ticker 같은 string 값에 민감어 들어와도 strip 되어야 한다 (공격적 입력)."""
    snap = FakeSnap(ticker="account_no=12345")
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(snap, fast_score=fast, strategies_passed=3, strategies_required=3)
    # ticker key 자체는 정책상 string field — 민감어가 들어오면 dict 에서 제거된다.
    assert "ticker" not in f or "account_no" not in str(f.get("ticker", "")).lower()


def test_strategy_pass_ratio_calculated():
    snap = FakeSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=2, strategies_required=4, risk_config=_RISK)
    f = build_daytrade_entry_features(snap, fast_score=fast, strategies_passed=2, strategies_required=4)
    assert abs(f["strategy_pass_ratio"] - 0.5) < 1e-6


def test_market_context_minutes_passed():
    snap = FakeSnap()
    fast = score_daytrade_snapshot(snap, strategies_passed=3, strategies_required=3, risk_config=_RISK)
    f = build_daytrade_entry_features(
        snap, fast_score=fast, strategies_passed=3, strategies_required=3,
        market_context={"minutes_since_open": 45},
    )
    assert f["minutes_since_open"] == 45


if __name__ == "__main__":
    import inspect
    fns = [f for n, f in inspect.getmembers(sys.modules[__name__]) if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for f in fns:
        try:
            f()
            print(f"  ok   {f.__name__}")
        except AssertionError as e:
            print(f"  FAIL {f.__name__}: {e}")
            failed += 1
    sys.exit(0 if failed == 0 else 1)
