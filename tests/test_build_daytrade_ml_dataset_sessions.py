"""tests/test_build_daytrade_ml_dataset_sessions.py — session-safe labeling regression."""

from __future__ import annotations

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_daytrade_ml_dataset import build_dataset  # noqa: E402


def test_build_dataset_does_not_label_across_session_dates(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    fields = ["ticker", "timestamp", "session_date", "minute_offset", "open", "high", "low", "close", "volume"]
    with src.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "ticker": "005930.KS",
            "timestamp": "2026-04-29T14:59:00+09:00",
            "session_date": "2026-04-29",
            "minute_offset": 359,
            "open": 100,
            "high": 100,
            "low": 100,
            "close": 100,
            "volume": 1000,
        })
        w.writerow({
            "ticker": "005930.KS",
            "timestamp": "2026-04-30T09:00:00+09:00",
            "session_date": "2026-04-30",
            "minute_offset": 0,
            "open": 110,
            "high": 200,
            "low": 110,
            "close": 110,
            "volume": 1000,
        })

    n = build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=30)

    assert n == 2
    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert rows[0]["label_int"] == "-1"
    assert rows[0]["label_hit"] == "horizon"


def test_build_dataset_writes_global_timestamp_order(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    fields = ["ticker", "timestamp", "session_date", "minute_offset", "open", "high", "low", "close", "volume"]
    with src.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for ticker in ("B", "A"):
            w.writerow({
                "ticker": ticker,
                "timestamp": "2026-04-30T09:01:00+09:00",
                "session_date": "2026-04-30",
                "minute_offset": 1,
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1000,
            })
            w.writerow({
                "ticker": ticker,
                "timestamp": "2026-04-30T09:00:00+09:00",
                "session_date": "2026-04-30",
                "minute_offset": 0,
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1000,
            })

    build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=30)

    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert [(r["timestamp"], r["ticker"]) for r in rows] == [
        ("2026-04-30T09:00:00+09:00", "A"),
        ("2026-04-30T09:00:00+09:00", "B"),
        ("2026-04-30T09:01:00+09:00", "A"),
        ("2026-04-30T09:01:00+09:00", "B"),
    ]


def _write_minute_csv(path, rows):
    fields = [
        "ticker", "timestamp", "session_date", "minute_offset",
        "open", "high", "low", "close", "volume",
        "atr_pct", "bid_qty", "ask_qty", "queue_imbalance", "microprice_gap_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            base = {k: 0 for k in fields}
            base.update(r)
            w.writerow(base)


def _bar(ticker, ts, session, off, *, close, atr_pct=0.0, bid_qty=0, ask_qty=0, qi=0.0, mg=0.0,
         high=None, low=None):
    return {
        "ticker": ticker, "timestamp": ts, "session_date": session, "minute_offset": off,
        "open": close, "high": high if high is not None else close,
        "low":  low  if low  is not None else close, "close": close, "volume": 1000,
        "atr_pct": atr_pct, "bid_qty": bid_qty, "ask_qty": ask_qty,
        "queue_imbalance": qi, "microprice_gap_pct": mg,
    }


def test_dataset_includes_has_microstructure_flag(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    _write_minute_csv(src, [
        # ticker WITH microstructure observations
        _bar("A", "2026-05-06T09:00:00+09:00", "2026-05-06", 0, close=100, bid_qty=1000, ask_qty=800, qi=0.1, mg=0.02),
        _bar("A", "2026-05-06T09:01:00+09:00", "2026-05-06", 1, close=100.5, bid_qty=900, ask_qty=900, qi=0.0, mg=0.01),
        # ticker WITHOUT microstructure
        _bar("B", "2026-05-06T09:00:00+09:00", "2026-05-06", 0, close=100),
        _bar("B", "2026-05-06T09:01:00+09:00", "2026-05-06", 1, close=100.5),
    ])
    n = build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=10)
    assert n == 4
    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    a_rows = [r for r in rows if r["ticker"] == "A"]
    b_rows = [r for r in rows if r["ticker"] == "B"]
    assert all(r["has_microstructure_data"] == "1" for r in a_rows)
    assert all(r["has_microstructure_data"] == "0" for r in b_rows)


def test_mstruct_track_drops_groups_below_coverage(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    # ticker A: 4/4 rows have ms (coverage 1.0 → keep)
    # ticker B: 0/4 rows have ms (coverage 0.0 → drop)
    rows = []
    for off in range(4):
        rows.append(_bar("A", f"2026-05-06T09:0{off}:00+09:00", "2026-05-06", off,
                         close=100 + off * 0.1, bid_qty=1000, ask_qty=800, qi=0.1))
        rows.append(_bar("B", f"2026-05-06T09:0{off}:00+09:00", "2026-05-06", off,
                         close=100 + off * 0.1))
    _write_minute_csv(src, rows)
    build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=5,
                  track="mstruct", from_date="2026-05-06", mstruct_coverage_min=0.5)
    out_rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    tickers = {r["ticker"] for r in out_rows}
    assert tickers == {"A"}, f"mstruct track should drop B; got {tickers}"


def test_mstruct_track_filters_by_from_date(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    rows = [
        _bar("A", "2026-05-05T09:00:00+09:00", "2026-05-05", 0, close=100, bid_qty=1000, ask_qty=800, qi=0.1),
        _bar("A", "2026-05-05T09:01:00+09:00", "2026-05-05", 1, close=100.5, bid_qty=1000, ask_qty=800, qi=0.1),
        _bar("A", "2026-05-06T09:00:00+09:00", "2026-05-06", 0, close=100, bid_qty=1000, ask_qty=800, qi=0.1),
        _bar("A", "2026-05-06T09:01:00+09:00", "2026-05-06", 1, close=100.5, bid_qty=1000, ask_qty=800, qi=0.1),
    ]
    _write_minute_csv(src, rows)
    build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=5,
                  track="mstruct", from_date="2026-05-06", mstruct_coverage_min=0.5)
    out_rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    sessions = {r["session_date"] for r in out_rows}
    assert sessions == {"2026-05-06"}


def test_ma120_slope_features_emitted_with_lookback(tmp_path):
    """ma120 lookback 5분 → row[5] 에서 row[0] 의 ma120 을 then 으로 사용."""
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    fields = [
        "ticker", "timestamp", "session_date", "minute_offset",
        "open", "high", "low", "close", "volume", "ma120", "atr_pct",
    ]
    with src.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        # 6 분봉. ma120: 50000 → 50100 → 50200 → 50300 → 50400 → 50500
        # i=5 의 lookback 5분 → row[0] 의 ma120=50000 → slope = (50500-50000)/50000*100 = 1.0%
        for i in range(6):
            w.writerow({
                "ticker": "T", "timestamp": f"2026-05-06T09:0{i}:00+09:00",
                "session_date": "2026-05-06", "minute_offset": i,
                "open": 50500, "high": 50600, "low": 50400, "close": 50500,
                "volume": 1000, "ma120": 50000 + i * 100, "atr_pct": 1.0,
            })

    build_dataset(src, out, upper_pct=0.008, lower_pct=0.005, horizon_minutes=10,
                  ma120_slope_lookback_minutes=5)

    rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert rows
    last = rows[-1]   # i=5 row
    assert abs(float(last["ma120_slope_pct"]) - 1.0) < 1e-3
    assert last["ma120_quality_strong_up"] == "1"
    # close=50500 > ma120=50500 (rounded equal) — depends on raw above check
    # confluence_score should reflect strong_up signal regardless
    assert int(last["ma120_confluence_score"]) >= 1


def test_atr_label_policy_writes_cost_and_atr_bucket(tmp_path):
    src = tmp_path / "minutes.csv"
    out = tmp_path / "dataset.csv"
    # ATR 1.0% × tp_mult 1.5 = +1.5% TP. high 1.6% in next bar.
    rows = [
        _bar("A", "2026-05-06T09:00:00+09:00", "2026-05-06", 0, close=100,
             atr_pct=1.0, bid_qty=1000, ask_qty=800, qi=0.1),
        _bar("A", "2026-05-06T09:01:00+09:00", "2026-05-06", 1, close=100,
             atr_pct=1.0, bid_qty=1000, ask_qty=800, qi=0.1, high=101.6, low=99.5),
    ]
    _write_minute_csv(src, rows)
    build_dataset(
        src, out, upper_pct=0.0, lower_pct=0.0, horizon_minutes=5,
        track="base", label_policy="atr",
        tp_atr_mult=1.5, sl_atr_mult=1.0, cost_pct=0.4,
    )
    out_rows = list(csv.DictReader(out.open("r", encoding="utf-8")))
    assert out_rows, "expected ≥1 labeled row"
    first = out_rows[0]
    assert first["label_policy"] == "atr"
    assert first["atr_bucket"] == "high"   # 1.0% → 'high'
    assert abs(float(first["upper_pct_used"]) - 0.015) < 1e-9
    assert abs(float(first["lower_pct_used"]) - 0.010) < 1e-9
    assert abs(float(first["cost_pct"]) - 0.4) < 1e-9
    # entry row 의 label 은 TP 먼저 (label=1), raw return 1.5%, net 1.1% (after 0.4 cost)
    assert first["label_int"] == "1"
    assert abs(float(first["return_pct"]) - 1.5) < 1e-3
    assert abs(float(first["net_return_pct"]) - 1.1) < 1e-3
