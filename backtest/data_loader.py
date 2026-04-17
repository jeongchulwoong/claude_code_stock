"""
backtest/data_loader.py — 백테스팅용 과거 데이터 로더

yfinance를 통해 국내/해외 주식 OHLCV를 받고
기술지표를 계산하여 DataFrame으로 반환한다.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger

# 캐시 디렉토리
CACHE_DIR = Path(__file__).parent.parent / "db" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── 종목코드 변환 ─────────────────────────────

def to_yahoo_ticker(code: str) -> str:
    """
    키움 종목코드 → Yahoo Finance 티커 변환
    005930 → 005930.KS
    AAPL   → AAPL (그대로)
    """
    if code.isdigit():
        return f"{code}.KS"
    return code


# ── 데이터 로더 ───────────────────────────────

class BacktestDataLoader:
    """
    yfinance 기반 OHLCV + 기술지표 데이터 로더.
    로컬 캐시를 활용해 API 호출을 최소화한다.
    """

    def __init__(self, use_cache: bool = True) -> None:
        self._use_cache = use_cache

    def load(
        self,
        ticker: str,
        start: str = "2020-01-01",
        end: str   = "2024-12-31",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        OHLCV + 기술지표 DataFrame 반환.
        ticker: 키움 코드(005930) 또는 Yahoo 티커(AAPL)
        """
        yahoo_ticker = to_yahoo_ticker(ticker)
        cache_key    = f"{yahoo_ticker}_{start}_{end}_{interval}.parquet"
        cache_path   = CACHE_DIR / cache_key

        # 캐시 확인
        if self._use_cache and cache_path.exists():
            logger.debug("캐시 로드: {}", cache_key)
            df = pd.read_parquet(cache_path)
        else:
            logger.info("yfinance 다운로드: {} ({} ~ {})", yahoo_ticker, start, end)
            raw = yf.download(
                yahoo_ticker,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
                progress=False,
            )
            if raw.empty:
                raise ValueError(f"데이터 없음: {yahoo_ticker} ({start}~{end})")

            # MultiIndex 컬럼 평탄화
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index.name = "date"
            df = df.dropna()

            if self._use_cache:
                df.to_parquet(cache_path)

        df = self._add_indicators(df)
        logger.info(
            "데이터 로드 완료: {} | {}행 | {} ~ {}",
            ticker, len(df),
            df.index[0].strftime("%Y-%m-%d"),
            df.index[-1].strftime("%Y-%m-%d"),
        )
        return df

    def load_multiple(
        self,
        tickers: list[str],
        start: str = "2020-01-01",
        end:   str = "2024-12-31",
    ) -> dict[str, pd.DataFrame]:
        """여러 종목을 한 번에 로드한다."""
        result = {}
        for ticker in tickers:
            try:
                result[ticker] = self.load(ticker, start, end)
                time.sleep(0.3)   # API 딜레이
            except Exception as e:
                logger.warning("로드 실패 [{}]: {}", ticker, e)
        return result

    # ── 기술지표 계산 ─────────────────────────

    @staticmethod
    def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        RSI / MACD / 볼린저밴드 / 이동평균 / 스토캐스틱 /
        ATR / 거래량 이동평균을 추가한다.
        """
        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        volume = df["volume"].astype(float)

        # ── RSI (14) ──────────────────────────
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        rs       = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = (100 - 100 / (1 + rs)).round(2)

        # ── MACD (12, 26, 9) ─────────────────
        ema12         = close.ewm(span=12, adjust=False).mean()
        ema26         = close.ewm(span=26, adjust=False).mean()
        df["macd"]        = (ema12 - ema26).round(2)
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean().round(2)
        df["macd_hist"]   = (df["macd"] - df["macd_signal"]).round(2)
        # 골든크로스: 전일 음수 → 당일 양수
        df["macd_cross"] = (
            (df["macd_hist"].shift(1) < 0) & (df["macd_hist"] >= 0)
        )

        # ── 볼린저밴드 (20, 2σ) ───────────────
        ma20            = close.rolling(20).mean()
        std20           = close.rolling(20).std()
        df["bb_upper"]  = (ma20 + 2 * std20).round(0)
        df["bb_lower"]  = (ma20 - 2 * std20).round(0)
        df["bb_mid"]    = ma20.round(0)
        df["bb_pct"]    = ((close - df["bb_lower"]) /
                           (df["bb_upper"] - df["bb_lower"])).round(4)

        # ── 이동평균 ─────────────────────────
        for n in [5, 10, 20, 60, 120]:
            df[f"ma{n}"] = close.rolling(n).mean().round(0)

        # MA5 골든크로스 (MA5 > MA20 전환)
        df["ma_cross"] = (
            (df["ma5"].shift(1) < df["ma20"].shift(1)) &
            (df["ma5"] >= df["ma20"])
        )

        # ── 스토캐스틱 (14, 3) ───────────────
        lowest  = low.rolling(14).min()
        highest = high.rolling(14).max()
        stoch_k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
        df["stoch_k"] = stoch_k.round(2)
        df["stoch_d"] = stoch_k.rolling(3).mean().round(2)

        # ── ATR (14) — 변동성 기반 손절 계산용 ──
        hl    = high - low
        hc    = (high - close.shift(1)).abs()
        lc    = (low  - close.shift(1)).abs()
        tr    = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["atr"] = tr.ewm(span=14, adjust=False).mean().round(2)

        # ── 거래량 지표 ───────────────────────
        df["vol_ma20"]    = volume.rolling(20).mean()
        df["vol_ratio"]   = (volume / df["vol_ma20"]).round(2)  # 평균 대비 배수

        return df.dropna(subset=["rsi", "macd"])
