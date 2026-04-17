"""
foreign/api_client.py — Finnhub + Alpha Vantage API 클라이언트

Finnhub    → 실시간 시세 + 뉴스 감성
Alpha Vantage → 기술지표 (RSI, MACD 등)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

FINNHUB_KEY       = os.getenv("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")


# ── 데이터 구조 ───────────────────────────────

@dataclass
class ForeignSnapshot:
    """해외주식 스냅샷 — AI 판단 엔진 입력값"""
    ticker:        str
    name:          str
    current_price: float
    open_price:    float
    high_price:    float
    low_price:     float
    prev_close:    float
    change_pct:    float          # 전일 대비 등락률 (%)
    volume:        int
    # 기술지표
    rsi:           float = 0.0
    macd:          float = 0.0
    macd_signal:   float = 0.0
    macd_cross:    bool  = False
    bb_upper:      float = 0.0
    bb_lower:      float = 0.0
    bb_position:   str   = "middle"
    # 뉴스 감성
    news_sentiment:float = 0.0    # -1.0 ~ +1.0
    news_count:    int   = 0
    news_summary:  str   = ""
    # 메타
    fetched_at:    str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class NewsItem:
    headline:  str
    summary:   str
    sentiment: float   # -1 ~ +1
    datetime:  str
    source:    str


# ── Finnhub 클라이언트 ────────────────────────

class FinnhubClient:
    """
    Finnhub REST API 클라이언트.
    무료 티어: 60 req/분
    """

    BASE = "https://finnhub.io/api/v1"
    _DELAY = 1.1   # 초당 1회 제한 준수

    def __init__(self, api_key: str = "") -> None:
        self._key  = api_key or FINNHUB_KEY
        self._mock = not bool(self._key)
        if self._mock:
            logger.warning("FINNHUB_API_KEY 없음 — Mock 모드")

    def get_quote(self, ticker: str) -> dict:
        """실시간 시세 조회"""
        if self._mock:
            return self._mock_quote(ticker)
        try:
            r = requests.get(
                f"{self.BASE}/quote",
                params={"symbol": ticker, "token": self._key},
                timeout=8,
            )
            r.raise_for_status()
            d = r.json()
            time.sleep(self._DELAY)
            return {
                "current": d.get("c", 0),
                "open":    d.get("o", 0),
                "high":    d.get("h", 0),
                "low":     d.get("l", 0),
                "prev":    d.get("pc", 0),
                "change_pct": round((d.get("c",0) - d.get("pc",1)) / max(d.get("pc",1),0.01) * 100, 2),
                "volume":  d.get("v", 0),
            }
        except Exception as e:
            logger.error("Finnhub 시세 오류 [{}]: {}", ticker, e)
            return self._mock_quote(ticker)

    def get_news_sentiment(self, ticker: str, days: int = 7) -> list[NewsItem]:
        """뉴스 감성 분석 (최근 N일)"""
        if self._mock:
            return self._mock_news(ticker)
        try:
            from_dt = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            to_dt   = datetime.now().strftime("%Y-%m-%d")
            r = requests.get(
                f"{self.BASE}/company-news",
                params={"symbol": ticker, "from": from_dt, "to": to_dt, "token": self._key},
                timeout=8,
            )
            r.raise_for_status()
            items = r.json()[:20]   # 최대 20개
            time.sleep(self._DELAY)

            results = []
            for item in items:
                sentiment = self._simple_sentiment(item.get("headline",""))
                results.append(NewsItem(
                    headline  = item.get("headline",""),
                    summary   = item.get("summary","")[:200],
                    sentiment = sentiment,
                    datetime  = str(item.get("datetime","")),
                    source    = item.get("source",""),
                ))
            return results
        except Exception as e:
            logger.error("Finnhub 뉴스 오류 [{}]: {}", ticker, e)
            return self._mock_news(ticker)

    def get_company_profile(self, ticker: str) -> dict:
        """기업 기본 정보"""
        if self._mock:
            return {"name": f"{ticker} Corp", "industry": "Technology"}
        try:
            r = requests.get(
                f"{self.BASE}/stock/profile2",
                params={"symbol": ticker, "token": self._key},
                timeout=8,
            )
            r.raise_for_status()
            d = r.json()
            time.sleep(self._DELAY)
            return {"name": d.get("name", ticker), "industry": d.get("finnhubIndustry", "")}
        except Exception as e:
            logger.error("Finnhub 프로필 오류 [{}]: {}", ticker, e)
            return {"name": ticker, "industry": ""}

    # ── 감성 분석 (단순 키워드) ───────────────
    _POSITIVE = {"beat","surge","rally","gain","record","strong","growth",
                 "profit","up","rise","positive","buy","upgrade","outperform"}
    _NEGATIVE = {"miss","drop","fall","decline","loss","weak","cut","down",
                 "sell","downgrade","underperform","concern","warning","risk"}

    def _simple_sentiment(self, text: str) -> float:
        words = set(text.lower().split())
        pos   = len(words & self._POSITIVE)
        neg   = len(words & self._NEGATIVE)
        total = pos + neg
        if total == 0:
            return 0.0
        return round((pos - neg) / total, 3)

    # ── Mock 데이터 ───────────────────────────
    @staticmethod
    def _mock_quote(ticker: str) -> dict:
        import random
        base  = {"AAPL":215,"MSFT":420,"GOOGL":175,"TSLA":245,"NVDA":875}.get(ticker, 100)
        price = base * (1 + random.uniform(-0.03, 0.03))
        prev  = base * (1 + random.uniform(-0.02, 0.02))
        return {
            "current":    round(price, 2),
            "open":       round(prev * 1.002, 2),
            "high":       round(price * 1.015, 2),
            "low":        round(price * 0.985, 2),
            "prev":       round(prev, 2),
            "change_pct": round((price - prev) / prev * 100, 2),
            "volume":     random.randint(5_000_000, 80_000_000),
        }

    @staticmethod
    def _mock_news(ticker: str) -> list[NewsItem]:
        return [
            NewsItem(f"{ticker} beats earnings estimates",
                     "Strong quarterly results driven by AI demand",
                     0.7, datetime.now().isoformat(), "Reuters"),
            NewsItem(f"{ticker} faces regulatory scrutiny",
                     "Antitrust concerns raised by EU regulators",
                     -0.4, datetime.now().isoformat(), "Bloomberg"),
            NewsItem(f"{ticker} announces new product launch",
                     "Innovation pipeline remains strong",
                     0.5, datetime.now().isoformat(), "WSJ"),
        ]


# ── Alpha Vantage 클라이언트 ─────────────────

class AlphaVantageClient:
    """
    Alpha Vantage REST API 클라이언트.
    무료 티어: 25 req/일 (느리므로 캐시 활용 권장)
    """

    BASE  = "https://www.alphavantage.co/query"
    _DELAY= 12.5   # 무료 티어: 5 req/분 → 12초 간격

    def __init__(self, api_key: str = "") -> None:
        self._key  = api_key or ALPHA_VANTAGE_KEY
        self._mock = not bool(self._key)
        if self._mock:
            logger.warning("ALPHA_VANTAGE_API_KEY 없음 — Mock 모드")

    def get_rsi(self, ticker: str, period: int = 14) -> Optional[float]:
        if self._mock:
            import random; return round(random.uniform(25, 75), 2)
        try:
            r = requests.get(self.BASE, params={
                "function": "RSI", "symbol": ticker,
                "interval": "daily", "time_period": period,
                "series_type": "close", "apikey": self._key,
            }, timeout=15)
            data = r.json()
            vals = data.get("Technical Analysis: RSI", {})
            if vals:
                latest = list(vals.values())[0]
                time.sleep(self._DELAY)
                return float(latest["RSI"])
        except Exception as e:
            logger.error("AV RSI 오류 [{}]: {}", ticker, e)
        return None

    def get_macd(self, ticker: str) -> Optional[dict]:
        if self._mock:
            import random
            macd   = random.uniform(-3, 3)
            signal = macd + random.uniform(-1, 1)
            return {"macd": round(macd,3), "signal": round(signal,3),
                    "hist": round(macd-signal,3), "cross": macd > signal}
        try:
            r = requests.get(self.BASE, params={
                "function": "MACD", "symbol": ticker,
                "interval": "daily", "apikey": self._key,
            }, timeout=15)
            data = r.json()
            vals = data.get("Technical Analysis: MACD", {})
            if vals:
                dates  = sorted(vals.keys(), reverse=True)
                latest = vals[dates[0]]
                prev   = vals[dates[1]] if len(dates) > 1 else latest
                macd   = float(latest["MACD"])
                signal = float(latest["MACD_Signal"])
                p_macd = float(prev["MACD"])
                p_sig  = float(prev["MACD_Signal"])
                time.sleep(self._DELAY)
                return {
                    "macd":   round(macd, 3),
                    "signal": round(signal, 3),
                    "hist":   round(float(latest["MACD_Hist"]), 3),
                    "cross":  (p_macd < p_sig) and (macd >= signal),
                }
        except Exception as e:
            logger.error("AV MACD 오류 [{}]: {}", ticker, e)
        return None

    def get_bbands(self, ticker: str) -> Optional[dict]:
        if self._mock:
            import random
            mid   = random.uniform(100, 500)
            std   = mid * 0.03
            price = mid + random.uniform(-std*2, std*2)
            upper, lower = mid + 2*std, mid - 2*std
            pos = "upper" if price >= upper else "lower" if price <= lower else "middle"
            return {"upper": round(upper,2), "lower": round(lower,2),
                    "mid": round(mid,2), "position": pos}
        try:
            r = requests.get(self.BASE, params={
                "function": "BBANDS", "symbol": ticker,
                "interval": "daily", "time_period": 20,
                "series_type": "close", "apikey": self._key,
            }, timeout=15)
            data = r.json()
            vals = data.get("Technical Analysis: BBANDS", {})
            if vals:
                latest = list(vals.values())[0]
                upper  = float(latest["Real Upper Band"])
                lower  = float(latest["Real Lower Band"])
                mid    = float(latest["Real Middle Band"])
                time.sleep(self._DELAY)
                return {"upper": round(upper,2), "lower": round(lower,2), "mid": round(mid,2)}
        except Exception as e:
            logger.error("AV BBands 오류 [{}]: {}", ticker, e)
        return None


# ── 통합 데이터 수집기 ────────────────────────

class ForeignDataCollector:
    """
    Finnhub + AlphaVantage를 조합하여 ForeignSnapshot을 생성한다.
    """

    def __init__(self) -> None:
        self._fh = FinnhubClient()
        self._av = AlphaVantageClient()

    def get_snapshot(self, ticker: str) -> ForeignSnapshot:
        logger.info("해외주식 스냅샷 수집: {}", ticker)

        profile   = self._fh.get_company_profile(ticker)
        quote     = self._fh.get_quote(ticker)
        news_list = self._fh.get_news_sentiment(ticker)
        rsi       = self._av.get_rsi(ticker) or 50.0
        macd_d    = self._av.get_macd(ticker) or {}
        bb        = self._av.get_bbands(ticker) or {}

        # 뉴스 감성 평균
        sentiment = (
            sum(n.sentiment for n in news_list) / len(news_list)
            if news_list else 0.0
        )
        news_summary = news_list[0].headline if news_list else ""

        # 볼린저 포지션
        price = quote["current"]
        if bb:
            if price >= bb["upper"]:
                bb_pos = "upper"
            elif price <= bb["lower"]:
                bb_pos = "lower"
            else:
                bb_pos = "middle"
        else:
            bb_pos = "middle"

        snap = ForeignSnapshot(
            ticker        = ticker,
            name          = profile.get("name", ticker),
            current_price = quote["current"],
            open_price    = quote["open"],
            high_price    = quote["high"],
            low_price     = quote["low"],
            prev_close    = quote["prev"],
            change_pct    = quote["change_pct"],
            volume        = quote["volume"],
            rsi           = rsi,
            macd          = macd_d.get("macd", 0.0),
            macd_signal   = macd_d.get("signal", 0.0),
            macd_cross    = macd_d.get("cross", False),
            bb_upper      = bb.get("upper", 0.0),
            bb_lower      = bb.get("lower", 0.0),
            bb_position   = bb_pos,
            news_sentiment= round(sentiment, 3),
            news_count    = len(news_list),
            news_summary  = news_summary,
        )

        logger.info(
            "스냅샷 완료: {} ${:.2f} | RSI={:.1f} | 감성={:.2f}",
            ticker, snap.current_price, snap.rsi, snap.news_sentiment,
        )
        return snap

    def get_snapshots(self, tickers: list[str]) -> list[ForeignSnapshot]:
        results = []
        for ticker in tickers:
            try:
                results.append(self.get_snapshot(ticker))
            except Exception as e:
                logger.error("스냅샷 실패 [{}]: {}", ticker, e)
        return results
