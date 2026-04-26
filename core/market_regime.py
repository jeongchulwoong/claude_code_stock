"""
core/market_regime.py — 시장 국면 분류 (KOSPI 기반)

30년차 퀀트 표준: 같은 RSI 30 도 시장 국면에 따라 의미가 다름.
- 추세장(BULL): 모멘텀·돌파 전략 유효, 평균회귀 위험
- 약세장(BEAR): 모든 신규 매수 가중치 ↓, 청산 우선
- 횡보장(RANGE): 평균회귀·반등 전략 유효
- 변동성 폭발(HIGH_VOL): 전 진입 가중치 ↓ (추격매매 위험)

5분 캐시로 매 호출마다 yfinance 안 부르도록 함.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from loguru import logger


@dataclass
class MarketRegime:
    state:        str    # "BULL" | "BEAR" | "RANGE" | "HIGH_VOL" | "UNKNOWN"
    kospi_pct:    float  # 전일 대비 %
    above_ma20:   bool   # KOSPI > 20일선
    above_ma60:   bool   # KOSPI > 60일선
    vol_pctile:   float  # 최근 60일 변동성 분위 (0~100, 높을수록 변동성 큼)
    confidence:   float  # 분류 신뢰도 (0~1)
    description:  str    # 사람용 설명


class MarketRegimeAnalyzer:
    """KOSPI 또는 SP500 데이터로 시장 국면을 분류한다. 5분 캐시."""

    CACHE_TTL = 5 * 60  # 5분
    INDEX_TICKERS = {"KR": "^KS11", "US": "^GSPC"}   # KOSPI / S&P 500

    def __init__(self, market: str = "KR"):
        """market: 'KR' (KOSPI) 또는 'US' (SP500)"""
        self.market = market.upper() if market.upper() in self.INDEX_TICKERS else "KR"
        self._cache: tuple[float, MarketRegime] | None = None

    def get(self) -> MarketRegime:
        now = time.time()
        if self._cache and now - self._cache[0] < self.CACHE_TTL:
            return self._cache[1]
        regime = self._classify()
        self._cache = (now, regime)
        return regime

    def _classify(self) -> MarketRegime:
        try:
            import yfinance as yf
            import pandas as pd
            ticker = self.INDEX_TICKERS[self.market]
            df = yf.download(ticker, period="6mo", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return self._unknown(f"{ticker} 데이터 없음")
            if hasattr(df.columns, "levels"):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]

            close  = df["close"].astype(float)
            cur    = float(close.iloc[-1])
            prev   = float(close.iloc[-2])
            ma20   = float(close.rolling(20).mean().iloc[-1])
            ma60   = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
            kospi_chg = (cur - prev) / prev * 100 if prev else 0.0

            # 변동성 분위: 최근 20일 일중 변동률의 60일 분위
            high  = df["high"].astype(float)
            low   = df["low"].astype(float)
            daily_range_pct = ((high - low) / close * 100).tail(60)
            cur_range = float(daily_range_pct.iloc[-1]) if len(daily_range_pct) else 0.0
            if len(daily_range_pct) >= 30:
                vol_pctile = float((daily_range_pct < cur_range).sum() / len(daily_range_pct) * 100)
            else:
                vol_pctile = 50.0

            above_ma20 = cur > ma20
            above_ma60 = cur > ma60

            # 분류 로직 (우선순위)
            # 1. 변동성 폭발 (분위 80+) → HIGH_VOL
            # 2. KOSPI < MA20 + MA60 → BEAR
            # 3. KOSPI > MA20 + MA60 → BULL
            # 4. 그 외 → RANGE
            mkt_label = "KOSPI" if self.market == "KR" else "S&P500"
            if vol_pctile >= 80:
                state = "HIGH_VOL"
                desc = f"{mkt_label} 변동성 폭발 ({vol_pctile:.0f}분위) — 추격매매 위험"
            elif (not above_ma20) and (not above_ma60):
                state = "BEAR"
                desc = f"{mkt_label} 약세장 ({cur:,.0f} < MA20 {ma20:,.0f}) — 매수 가중치 ↓"
            elif above_ma20 and above_ma60:
                state = "BULL"
                desc = f"{mkt_label} 추세장 ({cur:,.0f} > MA20·MA60) — 모멘텀 가중치 ↑"
            else:
                state = "RANGE"
                desc = f"{mkt_label} 횡보장 ({cur:,.0f}, MA20 {ma20:,.0f}) — 평균회귀 가중치 ↑"

            return MarketRegime(
                state=state, kospi_pct=round(kospi_chg, 2),
                above_ma20=above_ma20, above_ma60=above_ma60,
                vol_pctile=round(vol_pctile, 1),
                confidence=0.85,  # 단일 지수 기반 — 정밀 모델 아님
                description=desc,
            )
        except Exception as e:
            logger.warning("시장 국면 분류 실패: {}", e)
            return self._unknown(str(e))

    @staticmethod
    def _unknown(why: str) -> MarketRegime:
        return MarketRegime(
            state="UNKNOWN", kospi_pct=0.0,
            above_ma20=True, above_ma60=True, vol_pctile=50.0,
            confidence=0.0, description=f"분류 실패 — {why}",
        )

    @staticmethod
    def weight_multiplier(regime: MarketRegime, signal_type: str) -> float:
        """
        시장 국면에 따라 시그널 타입별 가중치 조정.
        signal_type: "trend" | "momentum" | "mean_rev" | "breakout"
        """
        weights = {
            "BULL":     {"trend": 1.25, "momentum": 1.15, "mean_rev": 0.80, "breakout": 1.20},
            "BEAR":     {"trend": 0.50, "momentum": 0.60, "mean_rev": 0.70, "breakout": 0.40},
            "RANGE":    {"trend": 0.85, "momentum": 0.90, "mean_rev": 1.20, "breakout": 0.85},
            "HIGH_VOL": {"trend": 0.70, "momentum": 0.70, "mean_rev": 0.80, "breakout": 0.60},
            "UNKNOWN":  {"trend": 1.00, "momentum": 1.00, "mean_rev": 1.00, "breakout": 1.00},
        }
        return weights.get(regime.state, weights["UNKNOWN"]).get(signal_type, 1.0)
