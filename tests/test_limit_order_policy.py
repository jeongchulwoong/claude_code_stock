"""tests/test_limit_order_policy.py — limit order policy 단위 테스트.

broker / order_manager / risk_manager 를 import 하지 않는다.
정책 정의 + 비용 모델 + fill 확률 만 검증.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.limit_order_policy import (   # noqa: E402
    CostModel,
    FillProbabilityModel,
    expected_realized_cost_pct,
    limit_order_cost,
    market_order_cost,
    policy_aggressive_market,
    policy_conservative_limit,
    policy_hybrid_limit_market,
)


def test_market_cost_around_0_38_percent():
    m = market_order_cost()
    # 0.18 (tax) + 0.03 (commission × 2) + 0.18 (slippage) = ~0.39
    assert 0.35 < m.total_pct < 0.45


def test_limit_cost_below_market():
    m = market_order_cost()
    l = limit_order_cost()
    assert l.total_pct < m.total_pct
    # limit 은 ~0.21% 근처 (slippage 0)
    assert 0.18 < l.total_pct < 0.30


def test_fill_probability_baseline():
    fp = FillProbabilityModel(base_fill_rate=0.80)
    # 정상 ATR (0.5%)
    assert abs(fp.estimate_fill_rate(0.5) - 0.80) < 1e-9


def test_fill_probability_low_vol_penalty():
    fp = FillProbabilityModel(base_fill_rate=0.80, low_vol_penalty=0.15)
    # ATR 0.1% 는 low vol → 페널티 적용
    assert abs(fp.estimate_fill_rate(0.1) - 0.65) < 1e-9


def test_fill_probability_high_vol_penalty():
    fp = FillProbabilityModel(base_fill_rate=0.80, high_vol_penalty=0.10)
    # ATR 2.0% 는 high vol
    assert abs(fp.estimate_fill_rate(2.0) - 0.70) < 1e-9


def test_fill_probability_clamped_to_unit():
    fp = FillProbabilityModel(base_fill_rate=1.5)
    assert fp.estimate_fill_rate(0.5) == 1.0   # clamp upper
    fp2 = FillProbabilityModel(base_fill_rate=-0.5)
    assert fp2.estimate_fill_rate(0.5) == 0.0   # clamp lower


def test_policies_have_distinct_names():
    names = {policy_aggressive_market().name, policy_conservative_limit().name,
             policy_hybrid_limit_market().name}
    assert len(names) == 3


def test_expected_realized_cost_includes_atr_level():
    pol = policy_conservative_limit()
    out = expected_realized_cost_pct(pol, atr_pct=0.5)
    assert "cost_per_filled_trade_pct" in out
    assert "fill_rate" in out
    assert 0.0 <= out["fill_rate"] <= 1.0
    assert out["cost_per_filled_trade_pct"] > 0


def test_market_policy_has_full_fill_rate():
    pol = policy_aggressive_market()
    out = expected_realized_cost_pct(pol, atr_pct=0.5)
    assert out["fill_rate"] == 1.0


def test_conservative_limit_lower_cost_than_market():
    a = expected_realized_cost_pct(policy_aggressive_market(), atr_pct=0.5)
    c = expected_realized_cost_pct(policy_conservative_limit(), atr_pct=0.5)
    assert c["cost_per_filled_trade_pct"] < a["cost_per_filled_trade_pct"]
