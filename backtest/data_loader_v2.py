"""
backtest/data_loader_v2.py — 다중 소스 데이터 로더 (API 우회 내장)

우선순위:
  1. yfinance          (Yahoo Finance — 기본)
  2. stooq             (무료, 한국 포함)
  3. pandas_datareader (FRED / Tiingo 등)
  4. 합성 데이터         (모든 소스 실패 시 최후 수단)

국내주식 KS 코드 → .KS 접미사 자동 처리
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).parent.parent / "db" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def to_tickers(code: str) -> dict[str, str]:
    """종목 코드 → 각 소스별 티커 매핑"""
    is_kr = code.isdigit()
    return {
        "yfinance": f"{code}.KS" if is_kr else code,
        "stooq":    f"{code}.KS" if is_kr else f"{code}.US",
        "synthetic": code,
    }


class RobustDataLoader:
    """
    여러 데이터 소스를 순차 시도하는 내성(Resilient) 데이터 로더.
    한 소스가 막히면 다음 소스로 자동 전환한다.
    """

    def __init__(self, use_cache: bool = True, verbose: bool = True) -> None:
        self._cache   = use_cache
        self._verbose = verbose
        # 소스별 성공/실패 카운터 (런타임 학습)
        self._source_score: dict[str, int] = {
            "yfinance": 0, "stooq": 0, "synthetic": 0
        }

    def load(
        self,
        ticker:   str,
        start:    str = "2020-01-01",
        end:      str = "2024-12-31",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        OHLCV + 기술지표 DataFrame 반환.
        캐시 → yfinance → stooq → 합성 데이터 순서로 시도.
        """
        cache_key  = f"v2_{ticker}_{start}_{end}.parquet"
        cache_path = CACHE_DIR / cache_key

        if self._cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            self._log(f"캐시 로드: {ticker} ({len(df)}행)")
            return self._add_indicators(df)

        # 소스 우선순위 (점수 높은 순)
        sources = sorted(
            ["yfinance", "stooq", "synthetic"],
            key=lambda s: -self._source_score[s]
        )

        df = None
        for source in sources:
            try:
                df = self._fetch(source, ticker, start, end, interval)
                if df is not None and len(df) >= 60:
                    self._source_score[source] += 1
                    self._log(f"[{source}] {ticker} 로드 성공: {len(df)}행")
                    break
                df = None
            except Exception as e:
                self._log(f"[{source}] {ticker} 실패: {e}")
                self._source_score[source] -= 1
                df = None

        if df is None or df.empty:
            self._log(f"모든 소스 실패 — 합성 데이터 사용: {ticker}")
            df = self._synthetic(ticker, start, end)

        if self._cache and not df.empty:
            df.to_parquet(cache_path)

        return self._add_indicators(df)

    def load_multiple(
        self, tickers: list[str],
        start: str = "2020-01-01", end: str = "2024-12-31"
    ) -> dict[str, pd.DataFrame]:
        result = {}
        for t in tickers:
            try:
                result[t] = self.load(t, start, end)
                time.sleep(0.3)
            except Exception as e:
                self._log(f"로드 실패 [{t}]: {e}")
        return result

    # ── 소스별 fetch ─────────────────────────

    def _fetch(self, source: str, ticker: str, start: str, end: str, interval: str) -> Optional[pd.DataFrame]:
        tmap = to_tickers(ticker)

        if source == "yfinance":
            return self._fetch_yfinance(tmap["yfinance"], start, end, interval)
        elif source == "stooq":
            return self._fetch_stooq(tmap["stooq"], start, end)
        return None

    @staticmethod
    def _fetch_yfinance(ticker: str, start: str, end: str, interval: str) -> Optional[pd.DataFrame]:
        import yfinance as yf
        raw = yf.download(ticker, start=start, end=end, interval=interval,
                          auto_adjust=True, progress=False)
        if raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index.name = "date"
        return df.dropna()

    @staticmethod
    def _fetch_stooq(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """
        stooq.com — 무료 OHLCV, 광고 없음.
        URL: https://stooq.com/q/d/l/?s={ticker}&d1=YYYYMMDD&d2=YYYYMMDD&i=d
        """
        import urllib.request, io
        s = start.replace("-", "")
        e = end.replace("-", "")
        url = f"https://stooq.com/q/d/l/?s={ticker.lower()}&d1={s}&d2={e}&i=d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        df = pd.read_csv(io.StringIO(content), parse_dates=["Date"])
        df = df.rename(columns={"Date":"date","Open":"open","High":"high",
                                 "Low":"low","Close":"close","Volume":"volume"})
        df = df.set_index("date").sort_index().dropna()
        if df.empty or len(df) < 10:
            return None
        return df[["open","high","low","close","volume"]]

    @staticmethod
    def _synthetic(ticker: str, start: str, end: str) -> pd.DataFrame:
        """실제 데이터 없을 때 통계적으로 타당한 합성 OHLCV 생성"""
        seed  = sum(ord(c) for c in ticker)
        rng   = np.random.default_rng(seed)
        dates = pd.bdate_range(start, end)
        n     = len(dates)

        # 종목별 특성 시뮬레이션 (한국 종목이면 원화 수준)
        is_kr    = ticker[:6].isdigit() if len(ticker) >= 6 else False
        base     = rng.uniform(50_000, 150_000) if is_kr else rng.uniform(50, 500)
        drift    = rng.uniform(0.0001, 0.0005)
        vol      = rng.uniform(0.012, 0.025)

        ret   = rng.normal(drift, vol, n)
        close = base * np.exp(np.cumsum(ret))
        high  = close * (1 + np.abs(rng.normal(0, 0.006, n)))
        low   = close * (1 - np.abs(rng.normal(0, 0.006, n)))
        open_ = np.roll(close, 1); open_[0] = close[0]
        vol_  = rng.lognormal(17 if is_kr else 14, 0.5, n).astype(int)

        df = pd.DataFrame({
            "open": open_, "high": high, "low": low,
            "close": close, "volume": vol_,
        }, index=dates)
        df.index.name = "date"
        return df

    # ── 기술지표 계산 ─────────────────────────

    @staticmethod
    def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """RSI / MACD / BB / MA / 스토캐스틱 / ATR / 거래량비율"""
        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        volume = df["volume"].astype(float)

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        loss  = (-delta).clip(lower=0).ewm(com=13, min_periods=14).mean()
        df["rsi"] = (100 - 100/(1 + gain/loss.replace(0, np.nan))).round(2)

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"]        = (ema12 - ema26).round(3)
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean().round(3)
        df["macd_hist"]   = (df["macd"] - df["macd_signal"]).round(3)
        df["macd_cross"]  = (df["macd_hist"].shift(1) < 0) & (df["macd_hist"] >= 0)

        # 볼린저밴드
        ma20 = close.rolling(20).mean()
        std  = close.rolling(20).std()
        df["bb_upper"] = (ma20 + 2*std).round(2)
        df["bb_lower"] = (ma20 - 2*std).round(2)
        df["bb_mid"]   = ma20.round(2)
        df["bb_pct"]   = ((close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])).round(4)

        # MA
        for n in [5, 10, 20, 60, 120]:
            df[f"ma{n}"] = close.rolling(n).mean().round(2)
        df["ma_cross"] = (df["ma5"].shift(1) < df["ma20"].shift(1)) & (df["ma5"] >= df["ma20"])

        # 스토캐스틱
        ll = low.rolling(14).min(); hh = high.rolling(14).max()
        stk = 100*(close-ll)/(hh-ll).replace(0, np.nan)
        df["stoch_k"] = stk.round(2)
        df["stoch_d"] = stk.rolling(3).mean().round(2)

        # ATR
        hl = high-low
        hc = (high - close.shift(1)).abs()
        lc = (low  - close.shift(1)).abs()
        df["atr"] = pd.concat([hl,hc,lc],axis=1).max(axis=1).ewm(span=14,adjust=False).mean().round(2)

        # 거래량
        df["vol_ma20"]  = volume.rolling(20).mean()
        df["vol_ratio"] = (volume / df["vol_ma20"]).round(2)

        return df.dropna(subset=["rsi","macd"])

    def _log(self, msg: str) -> None:
        if self._verbose:
            from loguru import logger
            logger.info(msg)
