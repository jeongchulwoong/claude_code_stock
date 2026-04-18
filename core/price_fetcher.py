"""
core/price_fetcher.py — Google Finance 내장 JSON 기반 정확한 현재가 조회

Google Finance 페이지(https://www.google.com/finance/quote/TICKER:EXCHANGE)에
AF_initDataCallback으로 내장된 JSON 구조에서 직접 파싱한다.

JSON 구조:
  [entity_id, [ticker, exchange], name, ?, currency,
   [price, change, change_pct, ...], ?, prev_close, ...]

사용:
    from core.price_fetcher import get_current_price, get_quote
    price = get_current_price("NKE")       # 46.03
    q     = get_quote("NKE")               # {'price': 46.03, 'change': 0.33, ...}
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import requests
from loguru import logger

# ── 거래소 매핑 ──────────────────────────────────

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
    "Accept-Language": "en-US,en;q=0.9",
}


def _ticker_to_gf(ticker: str) -> str:
    """ticker → Google Finance 'TICKER:EXCHANGE' 형식"""
    if ticker.endswith(".KS"):
        return f"{ticker[:-3]}:KRX"
    if ticker.endswith(".KQ"):
        return f"{ticker[:-3]}:KOSDAQ"
    exchange = _EXCHANGE_MAP.get(ticker, "NYSE")
    return f"{ticker}:{exchange}"


def _parse_embedded_json(html: str, ticker: str) -> Optional[dict]:
    """
    AF_initDataCallback 내장 JSON에서 가격 데이터를 추출한다.

    구조: [entity_id, [ticker, exchange], name, ?, currency,
            [price, change, change_pct, ...], ?, prev_close, ...]
    """
    # ticker 부분 (예: "NKE" from "NKE:NYSE")
    base_ticker = ticker.split(":")[0]

    # 내장 JSON 블록 전체 추출
    pattern = (
        r'AF_initDataCallback\(\{[^}]*data:\[.*?'
        r'\["' + re.escape(base_ticker) + r'","[A-Z]+"\]'
        r'.*?\}\)'
    )
    # 간단한 패턴: 티커 배열 + 앞뒤 컨텍스트
    # entity ID는 /m/... 또는 /g/... 형식 모두 허용
    ctx_pattern = (
        r'"/[a-z]/[^"]+",\["' + re.escape(base_ticker) + r'","([A-Z]+)"\],'
        r'"([^"]+)",\d+,"([A-Z]+)",\[([-\d.,]+)\],(?:null|-?\d+),([\d.]+)'
    )
    m = re.search(ctx_pattern, html)
    if not m:
        return None

    exchange   = m.group(1)
    name       = m.group(2)
    currency   = m.group(3)
    price_arr  = m.group(4).split(",")   # [price, change, change_pct, ...]
    prev_close = float(m.group(5))

    try:
        price      = float(price_arr[0])
        change     = float(price_arr[1]) if len(price_arr) > 1 else 0.0
        change_pct = float(price_arr[2]) if len(price_arr) > 2 else 0.0
    except (ValueError, IndexError):
        return None

    if price <= 0:
        return None

    return {
        "ticker":     base_ticker,
        "exchange":   exchange,
        "name":       name,
        "currency":   currency,
        "price":      price,
        "change":     round(change, 4),
        "change_pct": round(change_pct, 4),
        "prev_close": prev_close,
    }


def _parse_fallback(html: str) -> Optional[float]:
    """data-last-price 속성으로 fallback"""
    m = re.search(r'data-last-price="([\d.]+)"', html)
    if m:
        return float(m.group(1))
    m = re.search(r'class="[^"]*YMlKec[^"]*"[^>]*>\$?([\d,]+\.?\d*)<', html)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def get_quote(ticker: str, timeout: int = 8) -> Optional[dict]:
    """
    Google Finance에서 종목 시세 전체(가격·변동·환율 등)를 반환한다.
    실패 시 None 반환.
    """
    gf_ticker = _ticker_to_gf(ticker)
    url = f"https://www.google.com/finance/quote/{gf_ticker}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("GF HTTP {} [{}]", resp.status_code, ticker)
            return None
        html = resp.text

        # 1순위: 내장 JSON 파싱
        quote = _parse_embedded_json(html, gf_ticker)
        if quote:
            logger.debug("GF JSON [{}]: {} {}", ticker, quote["price"], quote["currency"])
            return quote

        # 2순위: data-last-price 속성 fallback
        price = _parse_fallback(html)
        if price and price > 0:
            logger.debug("GF attr fallback [{}]: {}", ticker, price)
            return {"ticker": ticker, "price": price, "currency": "USD",
                    "change": 0.0, "change_pct": 0.0, "prev_close": 0.0}

        logger.warning("GF 파싱 실패 [{}]", ticker)
    except Exception as e:
        logger.warning("GF 요청 실패 [{}]: {}", ticker, e)

    return None


def get_current_price(ticker: str, timeout: int = 8) -> Optional[float]:
    """
    Google Finance → yfinance fast_info 순서로 현재가를 반환한다.
    """
    quote = get_quote(ticker, timeout)
    if quote:
        return quote["price"]

    # 최종 fallback: yfinance
    try:
        import yfinance as yf
        lp = yf.Ticker(ticker).fast_info.last_price
        if lp and lp > 0:
            logger.debug("yfinance fallback [{}]: {}", ticker, lp)
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
