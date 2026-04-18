"""
strategies/breakout.py — 박스권 돌파 전략

진입 조건:
  - 최근 20일 고점을 당일 종가가 돌파
  - 거래량이 평균의 1.5배 이상 (돌파 신뢰성 확인)
  - RSI 45~70 (과매수 아닌 건전한 상승)
  - MACD > 0 (추세 방향 확인)

청산 조건:
  - 10일 최저가 하회 (추세 전환)
  - RSI > 75 (과매수)
  - AI SELL 신호
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict
from strategies.base_strategy import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """박스권 상단 돌파 전략"""

    name = "breakout"

    def __init__(
        self,
        lookback_days:   int   = 20,    # 박스권 관찰 기간
        volume_min:      float = 1.5,   # 최소 거래량 배수
        rsi_min:         float = 45.0,  # RSI 최솟값
        rsi_max:         float = 70.0,  # RSI 최댓값
    ) -> None:
        self._lookback   = lookback_days
        self._vol_min    = volume_min
        self._rsi_min    = rsi_min
        self._rsi_max    = rsi_max

    def should_enter(self, snap: StockSnapshot) -> bool:
        """20일 고점 돌파 + 거래량 확인"""
        df = snap.daily_df
        if df.empty or len(df) < self._lookback + 1:
            return False

        # 최근 lookback일 고점 (오늘 제외)
        recent_high = float(df["high"].iloc[-(self._lookback + 1):-1].max())
        today_close = float(df["close"].iloc[-1])

        conditions = {
            "고점 돌파":         today_close > recent_high,
            f"거래량 {self._vol_min}배+": snap.volume_ratio >= self._vol_min,
            f"RSI {self._rsi_min}~{self._rsi_max}": self._rsi_min <= snap.rsi <= self._rsi_max,
            "MACD 양수":         snap.macd > 0,
        }

        passed = all(conditions.values())
        if not passed:
            failed = [k for k, v in conditions.items() if not v]
            logger.debug("돌파 진입 불가 [{}]: {}", snap.ticker, ", ".join(failed))

        if passed:
            logger.info(
                "돌파 진입 조건 충족 [{}]: 현재가:{:,} > 20일고점:{:,}",
                snap.ticker, int(today_close), int(recent_high),
            )
        return passed

    def should_exit(
        self,
        snap: StockSnapshot,
        verdict: Optional[AIVerdict] = None,
    ) -> bool:
        df = snap.daily_df
        if not df.empty and len(df) >= 10:
            recent_low = float(df["low"].iloc[-10:].min())
            if snap.current_price < recent_low:
                logger.info("돌파 청산: 10일 저점 하회 [{}]", snap.ticker)
                return True

        if snap.rsi > 75:
            logger.info("돌파 청산: RSI 과매수({:.1f}) [{}]", snap.rsi, snap.ticker)
            return True

        if verdict and verdict.action == "SELL":
            return True

        return False
