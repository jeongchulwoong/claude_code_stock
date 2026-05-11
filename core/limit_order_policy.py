"""
core/limit_order_policy.py — 한도가 (limit) 주문 정책 (paper-only research module).

KOSPI 실현 비용 모델:
  ──────────────────────────────────────────────────────────
  market order:
    - entry: 매도호가 1차 (현재가 + 0~0.1% slippage)
    - exit:  매수호가 1차 (현재가 - 0~0.1% slippage)
    - 거래세 (0.18% 매도) + 위탁 수수료 (0.015% × 2) ≈ **0.21%**
    - slippage 가산: 양방향 합 ~0.15~0.20%
    - **총 round-trip cost ≈ 0.35~0.40%**

  limit order (entry at bid+1tick, exit at ask-1tick):
    - 1 호가 buffer 로 fill 확률 ↑, slippage ↓
    - 거래세 + 수수료 동일 ≈ 0.21%
    - 미체결 시 trade 자체 발생 X → cost 0
    - 체결 분포: ~70~85% (시장 변동성 따라)
    - **체결된 trade 의 round-trip cost ≈ 0.21~0.26%**

핵심 trade-off:
  - 시장 주문: 100% 체결, 비용 0.40%
  - limit 주문: 평균 75% 체결, 비용 0.25% (체결분만)

본 모듈은 정책 정의 + 비용 모델 + dry-run 시뮬용 helper 만 제공한다.
**broker 호출 / 주문 코드 변경 0건.** 매니페스트 phase_2 통과 + 운영자 명시 결정 후
별도 작업으로 order_manager.py 가 호출하도록 wire.

reference:
  - Almgren-Chriss 2000 "Optimal execution of portfolio transactions"
  - Hasbrouck 2007 "Empirical Market Microstructure" Ch. 11
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# KOSPI 표준 cost 구성 요소 (단위: %, round-trip 기준)
KOSPI_SELL_TAX_PCT       = 0.18    # 거래세 (매도 시)
KOSPI_COMMISSION_PCT     = 0.015   # 수수료 (편도)
DEFAULT_MARKET_SLIPPAGE_PCT = 0.18 # 시장가 양방향 합 슬리피지 추정
DEFAULT_LIMIT_SLIPPAGE_PCT  = 0.0  # limit 주문은 가격 확정


@dataclass(frozen=True)
class CostModel:
    """주문 정책별 round-trip 비용 구성."""
    sell_tax_pct: float
    commission_round_trip_pct: float
    slippage_pct: float

    @property
    def total_pct(self) -> float:
        return self.sell_tax_pct + self.commission_round_trip_pct + self.slippage_pct


def market_order_cost(slippage_pct: float = DEFAULT_MARKET_SLIPPAGE_PCT) -> CostModel:
    """시장가 round-trip 비용 (slippage 포함)."""
    return CostModel(
        sell_tax_pct=KOSPI_SELL_TAX_PCT,
        commission_round_trip_pct=KOSPI_COMMISSION_PCT * 2,   # 매수+매도
        slippage_pct=float(slippage_pct),
    )


def limit_order_cost(slippage_pct: float = DEFAULT_LIMIT_SLIPPAGE_PCT) -> CostModel:
    """한도가 round-trip 비용 (slippage 0 가정 — 가격 확정)."""
    return CostModel(
        sell_tax_pct=KOSPI_SELL_TAX_PCT,
        commission_round_trip_pct=KOSPI_COMMISSION_PCT * 2,
        slippage_pct=float(slippage_pct),
    )


@dataclass(frozen=True)
class FillProbabilityModel:
    """체결 확률 모델 — limit 주문이 horizon 안에 체결될 확률.

    base_fill_rate: 표준 조건에서의 기대 체결률 (0~1)
    high_vol_penalty: 변동성 큰 종목 (atr_pct > threshold) 시 차감
    low_vol_penalty:  변동성 작은 종목 (atr_pct < threshold) 시 차감
    """
    base_fill_rate: float = 0.80
    high_vol_threshold_atr_pct: float = 1.0
    high_vol_penalty: float = 0.10
    low_vol_threshold_atr_pct: float = 0.3
    low_vol_penalty: float = 0.15

    def estimate_fill_rate(self, atr_pct: float) -> float:
        """주어진 ATR pct 에서의 체결률 추정."""
        rate = self.base_fill_rate
        if atr_pct > self.high_vol_threshold_atr_pct:
            rate -= self.high_vol_penalty
        elif atr_pct < self.low_vol_threshold_atr_pct:
            rate -= self.low_vol_penalty
        return max(0.0, min(1.0, rate))


@dataclass(frozen=True)
class OrderPolicy:
    """주문 정책 (paper-only spec — 운영 wire 는 별도 PR)."""
    name: str
    entry_type: str    # 'market' or 'limit'
    exit_type:  str    # 'market' or 'limit'
    entry_tick_offset: int   # limit 주문 시 호가 buffer (양수 = 보수적)
    exit_tick_offset:  int
    cost_model: CostModel
    fill_model: FillProbabilityModel | None


def policy_aggressive_market() -> OrderPolicy:
    """현재 baseline — 양방향 시장가."""
    return OrderPolicy(
        name="aggressive_market",
        entry_type="market", exit_type="market",
        entry_tick_offset=0, exit_tick_offset=0,
        cost_model=market_order_cost(),
        fill_model=None,
    )


def policy_conservative_limit() -> OrderPolicy:
    """보수 — 양방향 limit (bid+1tick 매수, ask-1tick 매도)."""
    return OrderPolicy(
        name="conservative_limit",
        entry_type="limit", exit_type="limit",
        entry_tick_offset=1, exit_tick_offset=-1,
        cost_model=limit_order_cost(),
        fill_model=FillProbabilityModel(base_fill_rate=0.75),
    )


def policy_hybrid_limit_market() -> OrderPolicy:
    """진입 limit (선택적) + 청산 시장가 (확실한 청산 우선)."""
    return OrderPolicy(
        name="hybrid_limit_entry_market_exit",
        entry_type="limit", exit_type="market",
        entry_tick_offset=1, exit_tick_offset=0,
        # 비용: entry limit (slippage 0) + exit market (slippage ~0.1%)
        cost_model=CostModel(
            sell_tax_pct=KOSPI_SELL_TAX_PCT,
            commission_round_trip_pct=KOSPI_COMMISSION_PCT * 2,
            slippage_pct=DEFAULT_MARKET_SLIPPAGE_PCT / 2,   # 한쪽만
        ),
        fill_model=FillProbabilityModel(base_fill_rate=0.80),
    )


def expected_realized_cost_pct(policy: OrderPolicy, atr_pct: float = 0.5) -> dict:
    """policy + 종목 변동성 → 실현 비용 모델 (체결 분포 가중).

    Returns:
      dict with:
        gross_cost_per_filled_trade_pct: 체결된 trade 의 round-trip cost
        fill_rate: 체결률 (0~1)
        unfilled_rate: 미체결률 (slippage 회피)
        effective_avg_cost_pct: 100 개 시도 시 평균 실현 비용 (체결 가중)
        notes: 해석용 메모
    """
    fill_rate = policy.fill_model.estimate_fill_rate(atr_pct) if policy.fill_model else 1.0
    unfilled_rate = 1.0 - fill_rate
    cost = policy.cost_model.total_pct

    # 100 개 시도 가정:
    # - 체결된 fill_rate × 100 건은 비용 cost 부담
    # - 미체결 unfilled_rate × 100 건은 trade 자체 없음 (비용 0)
    # 평균 = fill_rate * cost  ← per-attempt
    # 단, "per filled trade" 기준 비용은 cost 자체.
    return {
        "policy_name": policy.name,
        "entry_type": policy.entry_type,
        "exit_type": policy.exit_type,
        "cost_per_filled_trade_pct": round(cost, 4),
        "fill_rate": round(fill_rate, 4),
        "unfilled_rate": round(unfilled_rate, 4),
        "per_filled_breakdown": {
            "sell_tax_pct": policy.cost_model.sell_tax_pct,
            "commission_round_trip_pct": policy.cost_model.commission_round_trip_pct,
            "slippage_pct": policy.cost_model.slippage_pct,
        },
        "atr_pct_used": atr_pct,
    }


def compare_policies(policies: list[OrderPolicy], atr_pct_levels: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0)) -> list[dict]:
    """다중 ATR 레벨에서 정책 비교 — 운영자 의사결정 보조."""
    rows = []
    for policy in policies:
        for atr in atr_pct_levels:
            r = expected_realized_cost_pct(policy, atr_pct=atr)
            r["atr_level"] = atr
            rows.append(r)
    return rows
