"""
core/price_fetcher.py — Google Finance 기반 현재가 조회

사용:
    from core.price_fetcher import get_current_price
    price = get_current_price("NKE")       # 미국 주식
    price = get_current_price("005930.KS") # 한국 주식
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Optional

import requests
from loguru import logger

# ── 거래소 매핑 ──────────────────────────────

_EXCHANGE_MAP: dict[str, str] = {
    # NASDAQ
    "AAPL": "NASDAQ", "MSFT": "NASDAQ", "NVDA": "NASDAQ", "GOOGL": "NASDAQ",
    "GOOG": "NASDAQ", "AMZN": "NASDAQ", "META": "NASDAQ", "TSLA": "NASDAQ",
    "AMD":  "NASDAQ", "INTC": "NASDAQ", "QCOM": "NASDAQ", "TXN":  "NASDAQ",
    "MU":   "NASDAQ", "AMAT": "NASDAQ", "LRCX": "NASDAQ", "ASML": "NASDAQ",
    "KLAC": "NASDAQ", "AVGO": "NASDAQ", "SBUX": "NASDAQ", "AMGN": "NASDAQ",
    "COST": "NASDAQ",
    # NYSE
    "NKE":  "NYSE",   "JPM":  "NYSE",   "BAC":  "NYSE",   "GS":   "NYSE",
    "MS":   "NYSE",   "V":    "NYSE",   "MA":   "NYSE",   "BRK-B":"NYSE",
    "BLK":  "NYSE",   "JNJ":  "NYSE",   "UNH":  "NYSE",   "LLY":  "NYSE",
    "PFE":  "NYSE",   "ABBV": "NYSE",   "MRK":  "NYSE",   "TMO":  "NYSE",
    "WMT":  "NYSE",   "HD":   "NYSE",   "MCD":  "NYSE",   "XOM":  "NYSE",
    "CVX":  "NYSE",   "COP":  "NYSE",   "NEE":  "NYSE",   "CAT":  "NYSE",
    "BA":   "NYSE",   "GE":   "NYSE",   "RTX":  "NYSE",   "TSM":  "NYSE",
    "SAP":  "NYSE",
    # Tokyo
    "9984.T": "TYO",  "7203.T": "TYO",  "6758.T": "TYO",
    "6861.T": "TYO",  "7974.T": "TYO",  "9432.T": "TYO",
    # Taiwan
    "2330.TW": "TPE", "2454.TW": "TPE",
    # HK
    "0700.HK": "HKEX", "9988.HK": "HKEX", "3690.HK": "HKEX",
    # Swiss
    "NESN.SW": "SWX", "NOVN.SW": "SWX", "ROG.SW": "SWX",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


def _ticker_to_gf(ticker: str) -> str:
    """ticker → Google Finance 'TICKER:EXCHANGE' 형식 변환"""
    if ticker.endswith(".KS"):
        return f"{ticker[:-3]}:KRX"
    if ticker.endswith(".KQ"):
        return f"{ticker[:-3]}:KOSDAQ"
    exchange = _EXCHANGE_MAP.get(ticker, "NYSE")
    return f"{ticker}:{exchange}"


def _parse_price(html: str) -> Optional[float]:
    """Google Finance HTML에서 현재가 추출"""
    # 방법 1: data-last-price attribute
    m = re.search(r'data-last-price="([\d.]+)"', html)
    if m:
        return float(m.group(1))

    # 방법 2: YMlKec 클래스 span (Google Finance 가격 span)
    m = re.search(r'class="[^"]*YMlKec[^"]*"[^>]*>([\d,]+\.?\d*)<', html)
    if m:
        return float(m.group(1).replace(",", ""))

    # 방법 3: JSON 데이터 내 가격
    m = re.search(r'"([\d]+\.?\d+)",\s*(?:null|"[^"]*"),\s*"(?:USD|KRW|EUR|JPY|TWD|HKD|CHF)"', html)
    if m:
        return float(m.group(1).replace(",", ""))

    # 방법 4: 일반 숫자 패턴 (fallback)
    m = re.search(r'itemprop="price"\s+content="([\d.]+)"', html)
    if m:
        return float(m.group(1))

    return None


def get_current_price(ticker: str, timeout: int = 8) -> Optional[float]:
    """
    Google Finance에서 현재가를 가져온다.
    실패 시 yfinance fast_info로 fallback.
    """
    gf_ticker = _ticker_to_gf(ticker)
    url = f"https://www.google.com/finance/quote/{gf_ticker}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            price = _parse_price(resp.text)
            if price and price > 0:
                logger.debug("GF 가격 [{}]: {}", ticker, price)
                return price
            logger.warning("GF 파싱 실패 [{}] — yfinance fallback", ticker)
    except Exception as e:
        logger.warning("GF 요청 실패 [{}]: {} — yfinance fallback", ticker, e)

    # fallback: yfinance
    try:
        import yfinance as yf
        lp = yf.Ticker(ticker).fast_info.last_price
        if lp and lp > 0:
            return float(lp)
    except Exception:
        pass

    return None


def get_prices_bulk(tickers: list[str], delay: float = 0.3) -> dict[str, float]:
    """여러 종목 현재가를 순차 조회하여 dict 반환"""
    result: dict[str, float] = {}
    for ticker in tickers:
        price = get_current_price(ticker)
        if price:
            result[ticker] = price
        time.sleep(delay)
    return result
