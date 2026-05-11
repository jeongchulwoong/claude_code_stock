"""tests/test_candle_aggregator.py — CandleAggregator 단위 테스트.

broker / order_manager / risk_manager 를 import 하지 않는다.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.candle_aggregator import Candle, CandleAggregator, CandleEvent  # noqa: E402


def _bar(offset, o, h, low, c, v=1000):
    return Candle(minute_offset=offset, timeframe_minutes=1,
                   open=o, high=h, low=low, close=c, volume=v,
                   timestamp=f"2026-05-11T09:{offset:02d}:00+09:00")


def test_1min_always_closes_immediately():
    agg = CandleAggregator(timeframes=(1, 3, 5))
    ev = agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5))
    # 1분봉은 매 분 close
    assert ev[1].closed_candle is not None
    assert ev[1].closed_candle.timeframe_minutes == 1
    assert ev[1].closed_candle.close == 100.5


def test_3min_closes_at_minute_offset_2():
    agg = CandleAggregator(timeframes=(1, 3, 5))
    agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5))
    ev = agg.on_1min_candle(_bar(1, 100.5, 102, 100, 101.5))
    # offset 1 — 3분봉 아직 close 아님
    assert ev[3].closed_candle is None
    assert ev[3].in_progress is not None

    ev2 = agg.on_1min_candle(_bar(2, 101.5, 103, 101, 102.5))
    # offset 2 — 3분봉 close (offset % 3 == 2)
    assert ev2[3].closed_candle is not None
    assert ev2[3].closed_candle.minute_offset == 2
    # OHLC 누적: open=100, high=103 (max of 101, 102, 103), low=99 (min of 99, 100, 101), close=102.5
    c3 = ev2[3].closed_candle
    assert c3.open == 100
    assert c3.high == 103
    assert c3.low == 99
    assert c3.close == 102.5


def test_5min_closes_at_minute_offset_4():
    agg = CandleAggregator(timeframes=(1, 3, 5))
    for i in range(5):
        ev = agg.on_1min_candle(_bar(i, 100, 100 + i, 100 - i, 100 + i * 0.5))
    # offset 4 — 5분봉 close
    assert ev[5].closed_candle is not None
    c5 = ev[5].closed_candle
    assert c5.minute_offset == 4
    assert c5.open == 100
    assert c5.high == 104   # max
    assert c5.low == 96     # min (100-4)
    assert c5.close == 102  # 100 + 4*0.5


def test_volume_accumulates_within_window():
    agg = CandleAggregator(timeframes=(1, 3))
    for i in range(3):
        ev = agg.on_1min_candle(_bar(i, 100, 101, 99, 100, v=500))
    c3 = ev[3].closed_candle
    assert c3.volume == 1500   # 500 × 3


def test_in_progress_returned_during_partial_window():
    agg = CandleAggregator(timeframes=(1, 3, 5))
    agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5))
    ev = agg.on_1min_candle(_bar(1, 100.5, 102, 100, 101.5))
    # 3분봉/5분봉 모두 in_progress
    assert ev[3].closed_candle is None
    assert ev[3].in_progress is not None
    assert ev[3].in_progress.closed is False
    assert ev[5].closed_candle is None
    assert ev[5].in_progress is not None


def test_duplicate_minute_offset_does_not_double_count():
    agg = CandleAggregator(timeframes=(3,))
    agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5, v=500))
    agg.on_1min_candle(_bar(1, 100.5, 102, 100, 101.5, v=500))
    # 같은 offset 재입력
    ev = agg.on_1min_candle(_bar(1, 100.5, 200, 50, 101.5, v=99999))
    # 변경 없어야 함 (단조 증가 위반 무시)
    assert ev[3].in_progress is not None
    assert ev[3].in_progress.high == 102   # 200 무시
    assert ev[3].in_progress.volume == 1000   # 99999 무시


def test_reset_clears_state():
    agg = CandleAggregator(timeframes=(3,))
    agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5))
    agg.on_1min_candle(_bar(1, 100.5, 102, 100, 101.5))
    agg.reset()
    ev = agg.on_1min_candle(_bar(0, 50, 51, 49, 50.5))   # 새 세션 0
    assert ev[3].in_progress is not None
    assert ev[3].in_progress.open == 50
    assert ev[3].in_progress.count if hasattr(ev[3].in_progress, "count") else True   # smoke


def test_consecutive_3min_bars_close_correctly():
    agg = CandleAggregator(timeframes=(3,))
    # 3분봉 2개 연속: offset 0~2 / 3~5
    for i in range(6):
        ev = agg.on_1min_candle(_bar(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i + 0.5))
    # 두 번째 3분봉 close (offset 5)
    assert ev[3].closed_candle is not None
    assert ev[3].closed_candle.minute_offset == 5
    assert ev[3].closed_candle.open == 103   # offset 3 의 open


def test_state_summary_returns_dict():
    agg = CandleAggregator(timeframes=(1, 3, 5))
    agg.on_1min_candle(_bar(0, 100, 101, 99, 100.5))
    s = agg.state_summary()
    assert "timeframes" in s
    assert "last_minute_offset" in s
    assert s["last_minute_offset"] == 0
