"""
strategies/momentum.py — 모멘텀 전략

진입 조건 (모두 만족 시 AI 판단으로 넘어감):
  - RSI < 35 (과매도 회복 초기)
  - 거래량 비율 >= 2.0 (평균 대비 2배 이상)
  - MA5 > MA20 (단기 상승 추세)
  - MACD 골든크로스 발생

종료 조건:
  - RSI > 70 (과매수)
  - AI SELL 신호
  - 손절·익절 (RiskManager가 처리)
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict
from strategies.base_strategy import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(
        self,
        rsi_entry: float = 35.0,
        volume_ratio_min: float = 2.0,
    ) -> None:
        self._rsi_entry = rsi_entry
        self._vol_min   = volume_ratio_min

    def should_enter(self, snap: StockSnapshot) -> bool:
        conditions = {
            "RSI 과매도(<35)":    snap.rsi < self._rsi_entry,
            "거래량 급등(2배+)":  snap.volume_ratio >= self._vol_min,
            "MA5 > MA20":         snap.ma5 > snap.ma20,
            "MACD 골든크로스":    snap.macd_cross,
        }
        passed = all(conditions.values())

        if not passed:
            failed = [k for k, v in conditions.items() if not v]
            logger.debug("모멘텀 진입 불가 [{}]: {}", snap.ticker, ", ".join(failed))

        return passed

    def should_exit(self, snap: StockSnapshot, verdict: Optional[AIVerdict] = None) -> bool:
        if snap.rsi > 70:
            logger.info("모멘텀 종료 조건: RSI 과매수({:.1f}) [{}]", snap.rsi, snap.ticker)
            return True
        if verdict and verdict.action == "SELL":
            return True
        return False
