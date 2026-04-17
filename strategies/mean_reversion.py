"""
strategies/mean_reversion.py — 평균 회귀 전략

진입 조건:
  - 볼린저밴드 하단 터치 (lower)
  - RSI < 30 (강한 과매도)
  - 스토캐스틱K < 20

종료 조건:
  - 볼린저밴드 중단 이상 (middle/upper)
  - RSI > 55
  - AI SELL 신호
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict
from strategies.base_strategy import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def should_enter(self, snap: StockSnapshot) -> bool:
        conditions = {
            "볼린저 하단 터치":    snap.bollinger_position == "lower",
            "RSI 강한 과매도(<30)": snap.rsi < 30,
            "스토캐스틱 과매도(<20)": snap.stochastic_k < 20,
        }
        passed = all(conditions.values())

        if not passed:
            failed = [k for k, v in conditions.items() if not v]
            logger.debug("평균회귀 진입 불가 [{}]: {}", snap.ticker, ", ".join(failed))

        return passed

    def should_exit(self, snap: StockSnapshot, verdict: Optional[AIVerdict] = None) -> bool:
        if snap.bollinger_position in ("middle", "upper") and snap.rsi > 55:
            logger.info("평균회귀 종료: 볼린저 중단+RSI 회복 [{}]", snap.ticker)
            return True
        if verdict and verdict.action == "SELL":
            return True
        return False
