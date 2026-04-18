"""
strategies/volume_surge.py — 거래량 급등 전략

진입 조건:
  - 거래량이 20일 평균의 3배 이상 급등
  - RSI < 60 (과매수 아님)
  - 당일 양봉 (종가 > 시가)
  - 볼린저밴드 하단~중단 구간 (상단은 제외)

근거:
  거래량 폭발은 기관·외인의 대량 매수 또는
  강한 뉴스 호재를 반영하는 경우가 많다.
  단, 과매수 구간에서의 급등은 단기 고점일 수 있어 필터링한다.

청산 조건:
  - 거래량 정상화 (1일 평균 이하로 복귀) + RSI > 65
  - 음봉 3일 연속
  - AI SELL 신호
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict
from strategies.base_strategy import BaseStrategy


class VolumeSurgeStrategy(BaseStrategy):
    """거래량 급등 추종 전략"""

    name = "volume_surge"

    def __init__(
        self,
        vol_multiplier: float = 3.0,   # 거래량 최소 배수
        rsi_max:        float = 60.0,  # RSI 최댓값
    ) -> None:
        self._vol_mult = vol_multiplier
        self._rsi_max  = rsi_max
        self._neg_candle_count: dict[str, int] = {}   # 음봉 연속 카운트

    def should_enter(self, snap: StockSnapshot) -> bool:
        df = snap.daily_df
        if df.empty or len(df) < 2:
            return False

        # 당일 양봉 여부
        is_bull_candle = float(df["close"].iloc[-1]) > float(df["open"].iloc[-1])

        conditions = {
            f"거래량 {self._vol_mult}배+":  snap.volume_ratio >= self._vol_mult,
            f"RSI < {self._rsi_max}":       snap.rsi < self._rsi_max,
            "양봉":                         is_bull_candle,
            "BB 하단·중단":                 snap.bollinger_position in ("lower","middle"),
        }

        passed = all(conditions.values())
        if not passed:
            failed = [k for k, v in conditions.items() if not v]
            logger.debug("거래량급등 진입 불가 [{}]: {}", snap.ticker, ", ".join(failed))

        if passed:
            logger.info(
                "거래량급등 진입 조건 [{}]: {:.1f}배 | RSI:{:.1f}",
                snap.ticker, snap.volume_ratio, snap.rsi,
            )
        return passed

    def should_exit(
        self,
        snap: StockSnapshot,
        verdict: Optional[AIVerdict] = None,
    ) -> bool:
        df = snap.daily_df
        ticker = snap.ticker

        # 음봉 연속 카운트
        if not df.empty and len(df) >= 1:
            last_close = float(df["close"].iloc[-1])
            last_open  = float(df["open"].iloc[-1])
            if last_close < last_open:   # 음봉
                self._neg_candle_count[ticker] = self._neg_candle_count.get(ticker, 0) + 1
            else:
                self._neg_candle_count[ticker] = 0

        if self._neg_candle_count.get(ticker, 0) >= 3:
            logger.info("거래량급등 청산: 음봉 3일 연속 [{}]", ticker)
            self._neg_candle_count[ticker] = 0
            return True

        # 거래량 정상화 + RSI 상승
        if snap.volume_ratio < 1.0 and snap.rsi > 65:
            logger.info(
                "거래량급등 청산: 거래량 정상화({:.1f}배) + RSI상승({:.1f}) [{}]",
                snap.volume_ratio, snap.rsi, ticker,
            )
            return True

        if verdict and verdict.action == "SELL":
            return True

        return False
