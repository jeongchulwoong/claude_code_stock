"""
core/price_cache.py — 실시간 시세 공유 캐시

kiwoom_ws.py (WebSocket/REST 폴링) → 이 캐시에 가격을 기록
main_v2.py (트레이딩 로직)          → 이 캐시에서 가격을 읽음
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TickData:
    ticker: str
    price: float
    change_pct: float
    volume: int
    high: float
    low: float
    open_price: float
    time: str
    ts: float = field(default_factory=time.time)


class PriceCache:
    """
    thread-safe in-memory price cache.
    kiwoom_ws.py가 write하고, main_v2.py가 read한다.
    """
    _instance: Optional["PriceCache"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "PriceCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._prices: dict[str, TickData] = {}
        self._kospi: Optional[float] = None
        self._kospi_ts: float = 0.0

    # ── writer (kiwoom_ws.py 에서 호출) ──────

    def update(self, tick: dict) -> None:
        """WebSocket/REST 폴링에서 수신한 틱数据进行 업데이트"""
        ticker = tick.get("ticker", "")
        if not ticker:
            return
        with self._lock:
            self._prices[ticker] = TickData(
                ticker   = ticker,
                price    = float(tick.get("price", 0)),
                change_pct = float(tick.get("change_pct", 0)),
                volume   = int(tick.get("volume", 0)),
                high     = float(tick.get("high", 0)),
                low      = float(tick.get("low", 0)),
                open_price = float(tick.get("open", 0)),
                time     = tick.get("time", ""),
                ts       = time.time(),
            )

    def update_kospi(self, kospi: float) -> None:
        """KOSPI 지수 업데이트"""
        with self._lock:
            self._kospi = kospi
            self._kospi_ts = time.time()

    # ── reader (main_v2.py 에서 호출) ─────────

    def get(self, ticker: str) -> Optional[TickData]:
        """현재가 조회 — 없으면 None"""
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> Optional[float]:
        """현재가만 반환"""
        tick = self.get(ticker)
        return tick.price if tick else None

    def get_all(self) -> dict[str, TickData]:
        """전체 캐시 복사본 반환"""
        with self._lock:
            return dict(self._prices)

    def get_kospi(self) -> Optional[float]:
        """KOSPI 현재값 반환"""
        with self._lock:
            return self._kospi

    def is_stale(self, ticker: str, max_age_sec: float = 30.0) -> bool:
        """캐시 데이터가 너무 오래되었는지 검사"""
        tick = self.get(ticker)
        if not tick:
            return True
        return (time.time() - tick.ts) > max_age_sec

    def clear(self) -> None:
        """캐시 초기화"""
        with self._lock:
            self._prices.clear()
            self._kospi = None


# ── 편의 함수 ─────────────────────────────────

_cache: Optional[PriceCache] = None

def get_cache() -> PriceCache:
    global _cache
    if _cache is None:
        _cache = PriceCache()
    return _cache
