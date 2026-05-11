"""
core/candle_aggregator.py — 1분봉 → 3/5분봉 실시간 집계 (paper-only research module).

사용자 spec section 4.1 구현.

설계 원칙:
  - 순수 함수 + 명시적 state. broker/order 영향 0.
  - close 시점 명확 (예: 3분봉은 minute_offset % 3 == 2 인 봉 직후).
  - 동시호가 (15:20+) / 점심 (11:30~13:00) 은 호출자 책임 — 본 모듈은 분 단위 입력만 처리.
  - look-ahead 방지: in_progress 봉은 모니터링 only, 의사결정에 사용 금지.

KOSPI 가정:
  - 정규장 09:00~15:30 (minute_offset 0~390)
  - minute_offset 0 = 09:00 시가
  - 3분봉 close 시점: minute_offset 2, 5, 8, ... (즉 09:02, 09:05, ...)
  - 5분봉 close 시점: minute_offset 4, 9, 14, ... (즉 09:04, 09:09, ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Candle:
    """OHLCV 캔들 (immutable).

    minute_offset: 09:00 부터의 분 오프셋 (0~389).
    closed: True 면 완성된 봉, False 면 진행 중 (in-progress).
    """
    minute_offset: int
    timeframe_minutes: int    # 1 / 3 / 5
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: str = ""
    closed: bool = True


@dataclass(frozen=True)
class CandleEvent:
    """집계 결과 — 한 timeframe 의 close 발생 여부 + in-progress 캔들."""
    timeframe_minutes: int
    closed_candle: Candle | None     # 새로 close 된 봉 (없으면 None)
    in_progress: Candle | None       # 진행 중 봉 (close 전)


class CandleAggregator:
    """1분봉 stream → 다중 timeframe 집계.

    내부 state:
      - 마지막으로 본 minute_offset (단조 증가 가정)
      - 진행 중인 3분봉 / 5분봉 partial OHLCV 누적

    재시작 안전:
      - state 는 in-memory only. main 재시작 시 초기화. session 시작 (09:00) 가정.
      - reset() 으로 새 세션 시작 시 명시적 초기화.
    """

    def __init__(self, timeframes: tuple[int, ...] = (1, 3, 5)):
        self._timeframes = tuple(int(t) for t in timeframes if int(t) > 0)
        self._partial: dict[int, dict[str, Any]] = {}
        self._last_minute_offset: int = -1
        self.reset()

    def reset(self) -> None:
        """새 세션 시작 시 호출."""
        self._partial = {tf: self._empty_partial() for tf in self._timeframes}
        self._last_minute_offset = -1

    @staticmethod
    def _empty_partial() -> dict[str, Any]:
        return {
            "start_minute_offset": -1,
            "open": 0.0, "high": 0.0, "low": float("inf"), "close": 0.0,
            "volume": 0.0, "count": 0, "timestamp": "",
        }

    def _is_close_minute(self, timeframe_minutes: int, minute_offset: int) -> bool:
        """이 minute_offset 이 해당 timeframe 의 마지막 봉이면 True.

        3분봉: minute_offset % 3 == 2 (09:02, 09:05, ...)
        5분봉: minute_offset % 5 == 4 (09:04, 09:09, ...)
        1분봉: 항상 True
        """
        if timeframe_minutes <= 1:
            return True
        return (minute_offset % timeframe_minutes) == (timeframe_minutes - 1)

    def on_1min_candle(self, candle: Candle) -> dict[int, CandleEvent]:
        """1분봉 입력 → 각 timeframe 의 CandleEvent dict.

        Args:
          candle: timeframe_minutes=1, closed=True 인 분봉.

        Returns:
          {1: CandleEvent, 3: CandleEvent, 5: CandleEvent}
          - 1분봉은 항상 closed_candle 채워짐
          - 3/5분봉은 close 시점에만 closed_candle, 그 외엔 None
          - in_progress 는 모든 timeframe 의 현재 누적 partial

        규칙:
          - 같은 minute_offset 재입력 → 기존 partial 덮어쓰지 않음 (idempotent skip)
          - 과거 minute_offset 입력 → 무시 (단조 증가만 허용)
        """
        if candle.timeframe_minutes != 1:
            # 1분봉만 입력 받음
            return {}
        if candle.minute_offset <= self._last_minute_offset:
            # 동일/과거 분봉 — 무시
            return {tf: CandleEvent(tf, None, self._snapshot_partial(tf)) for tf in self._timeframes}

        result: dict[int, CandleEvent] = {}
        for tf in self._timeframes:
            partial = self._partial[tf]

            # 첫 봉 또는 새 봉 시작 → partial 초기화
            if partial["start_minute_offset"] < 0 or partial["count"] == 0:
                partial = self._empty_partial()
                partial["start_minute_offset"] = candle.minute_offset
                partial["open"] = candle.open
                partial["timestamp"] = candle.timestamp

            # 누적
            partial["high"] = max(partial["high"], candle.high) if partial["count"] > 0 else candle.high
            partial["low"] = min(partial["low"], candle.low) if partial["count"] > 0 else candle.low
            partial["close"] = candle.close
            partial["volume"] += candle.volume
            partial["count"] += 1

            self._partial[tf] = partial

            # close 시점인가?
            closed_candle = None
            if self._is_close_minute(tf, candle.minute_offset):
                closed_candle = Candle(
                    minute_offset=candle.minute_offset,
                    timeframe_minutes=tf,
                    open=partial["open"],
                    high=partial["high"],
                    low=partial["low"],
                    close=partial["close"],
                    volume=partial["volume"],
                    timestamp=candle.timestamp,
                    closed=True,
                )
                # close 후 partial 리셋 (다음 봉 시작 위해)
                self._partial[tf] = self._empty_partial()
                in_progress = None
            else:
                in_progress = self._snapshot_partial(tf)

            result[tf] = CandleEvent(
                timeframe_minutes=tf,
                closed_candle=closed_candle,
                in_progress=in_progress,
            )

        self._last_minute_offset = candle.minute_offset
        return result

    def _snapshot_partial(self, tf: int) -> Candle | None:
        partial = self._partial.get(tf)
        if not partial or partial["count"] == 0 or partial["start_minute_offset"] < 0:
            return None
        return Candle(
            minute_offset=partial["start_minute_offset"],
            timeframe_minutes=tf,
            open=partial["open"],
            high=partial["high"],
            low=partial["low"] if partial["low"] != float("inf") else partial["open"],
            close=partial["close"],
            volume=partial["volume"],
            timestamp=partial["timestamp"],
            closed=False,
        )

    @property
    def last_minute_offset(self) -> int:
        return self._last_minute_offset

    def state_summary(self) -> dict[str, Any]:
        return {
            "timeframes": list(self._timeframes),
            "last_minute_offset": self._last_minute_offset,
            "partials": {
                str(tf): {"count": p["count"], "start": p["start_minute_offset"]}
                for tf, p in self._partial.items()
            },
        }
