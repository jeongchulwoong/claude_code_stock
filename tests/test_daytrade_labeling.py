"""tests/test_daytrade_labeling.py — triple-barrier labeling 단위 테스트."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.daytrade_labeling import (   # noqa: E402
    TripleBarrierError,
    atr_bucket,
    label_triple_barrier,
    label_triple_barrier_atr,
)


def _row(close: float, *, high: float | None = None, low: float | None = None,
         minute_offset: int = 0) -> dict:
    return {
        "close": close,
        "high":  high if high is not None else close,
        "low":   low  if low  is not None else close,
        "minute_offset": minute_offset,
    }


def test_tp_first_label_1():
    rows = [
        _row(100, minute_offset=0),
        _row(101, high=102, low=101, minute_offset=1),
        _row(103, high=103, low=102, minute_offset=2),   # +3% 도달 (upper=2%)
    ]
    out = label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)
    assert out.label == 1
    assert out.hit == "tp"
    assert out.exit_index == 1   # 101 의 high=102 가 1.02배 도달.


def test_sl_first_label_0():
    rows = [
        _row(100, minute_offset=0),
        _row(99,  high=99,  low=98, minute_offset=1),    # -2% 도달 (lower=2%)
        _row(99,  high=99,  low=98, minute_offset=2),
    ]
    out = label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)
    assert out.label == 0
    assert out.hit == "sl"


def test_horizon_label_minus_1():
    # 가격 박스권 — 둘 다 미도달.
    rows = [_row(100, high=100.5, low=99.5, minute_offset=i) for i in range(0, 11)]
    out = label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=5)
    assert out.label == -1
    assert out.hit == "horizon"
    assert out.exit_index == 5
    assert out.holding_minutes == 5


def test_same_bar_tp_sl_sl_priority():
    rows = [
        _row(100, minute_offset=0),
        _row(100, high=103, low=97, minute_offset=1),   # 같은 봉 TP/SL 동시 도달
    ]
    out = label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)
    assert out.label == 0
    assert "sl_priority" in out.hit


def test_invalid_entry_index_raises():
    rows = [_row(100, minute_offset=i) for i in range(3)]
    with pytest.raises(TripleBarrierError):
        label_triple_barrier(rows, entry_index=-1, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)
    with pytest.raises(TripleBarrierError):
        label_triple_barrier(rows, entry_index=99, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)


def test_empty_rows_raises():
    with pytest.raises(TripleBarrierError):
        label_triple_barrier([], entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)


def test_negative_barriers_raise():
    rows = [_row(100, minute_offset=i) for i in range(3)]
    with pytest.raises(TripleBarrierError):
        label_triple_barrier(rows, entry_index=0, upper_pct=-0.01, lower_pct=0.02, horizon_minutes=10)
    with pytest.raises(TripleBarrierError):
        label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.0, horizon_minutes=10)


def test_zero_horizon_raises():
    rows = [_row(100, minute_offset=i) for i in range(3)]
    with pytest.raises(TripleBarrierError):
        label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=0)


def test_return_pct_sign():
    rows = [
        _row(100, minute_offset=0),
        _row(100, high=103, low=99, minute_offset=1),
    ]
    out = label_triple_barrier(rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10)
    assert out.return_pct > 0   # TP 먼저 → 양수 수익.


def test_cost_pct_threaded_into_net_return():
    rows = [
        _row(100, minute_offset=0),
        _row(100, high=103, low=99, minute_offset=1),
    ]
    out = label_triple_barrier(
        rows, entry_index=0, upper_pct=0.02, lower_pct=0.02, horizon_minutes=10, cost_pct=0.4,
    )
    # TP +2% raw → +1.6% net after 0.4% cost.
    assert abs(out.return_pct - 2.0) < 1e-9
    assert abs(out.net_return_pct - 1.6) < 1e-9
    assert out.cost_pct == 0.4


def test_atr_label_tp_first_with_cost():
    # ATR 1.0% × tp_mult 1.5 = barrier +1.5%.
    rows = [
        _row(100, minute_offset=0),
        _row(100, high=101.6, low=99.5, minute_offset=1),  # +1.6% high → TP first
    ]
    out = label_triple_barrier_atr(
        rows, entry_index=0, atr_pct=1.0,
        tp_atr_mult=1.5, sl_atr_mult=1.0, horizon_minutes=10, cost_pct=0.4,
    )
    assert out.label == 1
    assert out.hit == "tp"
    assert abs(out.upper_pct - 0.015) < 1e-9
    assert abs(out.lower_pct - 0.010) < 1e-9
    # raw +1.5% → net +1.1% after 0.4% cost
    assert abs(out.return_pct - 1.5) < 1e-9
    assert abs(out.net_return_pct - 1.1) < 1e-9


def test_atr_label_invalid_atr_raises():
    rows = [_row(100, minute_offset=i) for i in range(3)]
    with pytest.raises(TripleBarrierError):
        label_triple_barrier_atr(rows, entry_index=0, atr_pct=0.0)
    with pytest.raises(TripleBarrierError):
        label_triple_barrier_atr(rows, entry_index=0, atr_pct=-1.0)
    with pytest.raises(TripleBarrierError):
        label_triple_barrier_atr(rows, entry_index=0, atr_pct=1.0, tp_atr_mult=0.0)


def test_atr_label_clamped_to_min_barrier():
    # ATR 0.05% × 1.5 = 0.075% — min_barrier 0.2% 로 clamp 되어야.
    rows = [_row(100, high=100.5, low=99.5, minute_offset=i) for i in range(0, 6)]
    out = label_triple_barrier_atr(
        rows, entry_index=0, atr_pct=0.05, horizon_minutes=5, cost_pct=0.0,
    )
    assert out.upper_pct >= 0.002 - 1e-12
    assert out.lower_pct >= 0.002 - 1e-12


def test_atr_bucket_categories():
    assert atr_bucket(0.3) == "low"
    assert atr_bucket(0.7) == "medium"
    assert atr_bucket(1.5) == "high"
    assert atr_bucket(0.0) == "unknown"
    assert atr_bucket(-1.0) == "unknown"
    assert atr_bucket(None) == "unknown"
    assert atr_bucket(float("nan")) == "unknown"


if __name__ == "__main__":
    import inspect
    fns = [f for n, f in inspect.getmembers(sys.modules[__name__]) if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for f in fns:
        try:
            f()
            print(f"  ok   {f.__name__}")
        except (AssertionError, Exception) as e:
            print(f"  FAIL {f.__name__}: {e}")
            failed += 1
    sys.exit(0 if failed == 0 else 1)
