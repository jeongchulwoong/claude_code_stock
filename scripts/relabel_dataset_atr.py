"""
scripts/relabel_dataset_atr.py — 기존 event dataset 의 라벨만 ATR-multiple 정책으로 재계산.

기존 row 의 feature 는 그대로 두고, label_int / return_pct / gross_return_pct 만
새 TP/SL 비율 + cost 로 다시 계산한다. minute_bars DB 에서 row.timestamp 이후
horizon_minutes 분의 분봉 시퀀스를 읽어 triple-barrier 적용.

label policy:
  - upper_pct = atr_pct/100 × tp_atr_mult (clamped [0.2%, 5%])
  - lower_pct = atr_pct/100 × sl_atr_mult
  - horizon_minutes default 30
  - cost_pct default 0.4 (KOSPI retail realistic)
  - same-bar TP/SL → SL priority (conservative)

사용:
  python scripts/relabel_dataset_atr.py \\
    --in  db/ml/daytrade_event_dataset_ma120slope_min3_20260510.csv \\
    --out db/ml/daytrade_event_dataset_atr25x10_cost04_20260511.csv \\
    --minute-db db/kiwoom_minute_bars.db \\
    --tp-atr-mult 2.5 --sl-atr-mult 1.0 \\
    --horizon-minutes 30 --cost-pct 0.4
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.daytrade_labeling import TripleBarrierError, label_triple_barrier_atr  # noqa: E402


def _fetch_future_bars(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    session_date: str,
    start_minute_offset: int,
    horizon_minutes: int,
) -> list[dict]:
    """minute_bars 에서 (ticker, session_date) 의 minute_offset ∈ [start, start+horizon] 분봉.

    sorted by minute_offset ASC. label_triple_barrier_atr 가 row[0] 을 entry 로 사용하므로
    row 들이 entry 시점부터 horizon 까지 포함되어야 한다.
    """
    cur = conn.execute(
        "SELECT minute_offset, high, low, close "
        "FROM minute_bars "
        "WHERE timeframe='1m' AND ticker=? AND session_date=? "
        "AND minute_offset >= ? AND minute_offset <= ? "
        "ORDER BY minute_offset ASC",
        (str(ticker), str(session_date),
         int(start_minute_offset), int(start_minute_offset + horizon_minutes + 2)),
    )
    out = []
    for off, h, low, c in cur:
        try:
            out.append({
                "minute_offset": int(off),
                "high": float(h) if h is not None else 0.0,
                "low":  float(low) if low is not None else 0.0,
                "close": float(c) if c is not None else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    p.add_argument("--out", dest="out_path", required=True)
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--tp-atr-mult", type=float, default=2.5)
    p.add_argument("--sl-atr-mult", type=float, default=1.0)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--cost-pct", type=float, default=0.4)
    args = p.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"[error] not found: {in_path}", file=sys.stderr)
        return 2
    minute_db = Path(args.minute_db)
    if not minute_db.exists():
        print(f"[error] minute_db not found: {minute_db}", file=sys.stderr)
        return 2

    df = pd.read_csv(in_path)
    # 필요한 컬럼: ticker, session_date, minutes_since_open (entry minute_offset), atr_pct
    if "minutes_since_open" not in df.columns:
        print("[error] dataset missing minutes_since_open column", file=sys.stderr)
        return 2
    df["minutes_since_open"] = pd.to_numeric(df["minutes_since_open"], errors="coerce").fillna(0).astype(int)
    df["atr_pct"] = pd.to_numeric(df.get("atr_pct", 0), errors="coerce").fillna(0.0)
    df["ticker"] = df["ticker"].astype(str)
    df["session_date"] = df["session_date"].astype(str)

    conn = sqlite3.connect(str(minute_db))
    # 그룹 기반 → 같은 (ticker, session_date) 의 분봉을 한 번에 load → 메모리 캐시
    grouped = df.groupby(["ticker", "session_date"], sort=False)

    new_label_int = []
    new_label_hit = []
    new_return_pct = []
    new_gross_return_pct = []
    new_net_return_pct = []
    new_upper_pct = []
    new_lower_pct = []
    new_cost_pct = []
    new_holding_min = []

    n_processed = 0
    n_skipped = 0
    n_relabeled_pos = 0
    n_relabeled_neg = 0
    n_relabeled_timeout = 0

    for (ticker, session), idx in grouped.groups.items():
        sub = df.loc[idx]
        # 이 (ticker, session) 의 분봉 전체 로드 (entry 시점 별로 future 30분 검색)
        cur = conn.execute(
            "SELECT minute_offset, high, low, close FROM minute_bars "
            "WHERE timeframe='1m' AND ticker=? AND session_date=? ORDER BY minute_offset ASC",
            (str(ticker), str(session)),
        )
        bars = []
        for off, h, low, c in cur:
            try:
                bars.append({
                    "minute_offset": int(off),
                    "high": float(h) if h is not None else 0.0,
                    "low":  float(low) if low is not None else 0.0,
                    "close": float(c) if c is not None else 0.0,
                })
            except (TypeError, ValueError):
                continue
        # 빠른 lookup 을 위해 dict
        bar_by_off = {b["minute_offset"]: b for b in bars}

        for _, row in sub.iterrows():
            entry_off = int(row["minutes_since_open"])
            atr_pct = float(row["atr_pct"])
            # 미래 분봉 시퀀스 — entry_off 이상, entry_off + horizon + 2
            future_offs = [o for o in bar_by_off if o >= entry_off and o <= entry_off + args.horizon_minutes + 2]
            future_offs.sort()
            seq = [bar_by_off[o] for o in future_offs]
            if not seq or atr_pct <= 0:
                new_label_int.append(-1)
                new_label_hit.append("no_data")
                new_return_pct.append(0.0)
                new_gross_return_pct.append(0.0)
                new_net_return_pct.append(0.0)
                new_upper_pct.append(0.0)
                new_lower_pct.append(0.0)
                new_cost_pct.append(args.cost_pct)
                new_holding_min.append(0)
                n_skipped += 1
                continue
            try:
                # seq[0] 의 close 가 entry_price
                lbl = label_triple_barrier_atr(
                    seq, entry_index=0,
                    atr_pct=atr_pct,
                    tp_atr_mult=args.tp_atr_mult,
                    sl_atr_mult=args.sl_atr_mult,
                    horizon_minutes=args.horizon_minutes,
                    cost_pct=args.cost_pct,
                )
            except TripleBarrierError:
                new_label_int.append(-1)
                new_label_hit.append("invalid")
                new_return_pct.append(0.0)
                new_gross_return_pct.append(0.0)
                new_net_return_pct.append(0.0)
                new_upper_pct.append(0.0)
                new_lower_pct.append(0.0)
                new_cost_pct.append(args.cost_pct)
                new_holding_min.append(0)
                n_skipped += 1
                continue
            new_label_int.append(lbl.label)
            new_label_hit.append(lbl.hit)
            new_return_pct.append(round(lbl.net_return_pct, 4))   # net 으로 채워서 dataset 호환
            new_gross_return_pct.append(round(lbl.return_pct, 4))
            new_net_return_pct.append(round(lbl.net_return_pct, 4))
            new_upper_pct.append(round(lbl.upper_pct, 6))
            new_lower_pct.append(round(lbl.lower_pct, 6))
            new_cost_pct.append(round(lbl.cost_pct, 4))
            new_holding_min.append(lbl.holding_minutes)
            n_processed += 1
            if lbl.label == 1:
                n_relabeled_pos += 1
            elif lbl.label == 0:
                n_relabeled_neg += 1
            else:
                n_relabeled_timeout += 1
    conn.close()

    df["label_int"] = new_label_int
    df["label_hit"] = new_label_hit
    df["return_pct"] = new_return_pct       # net for compatibility with downstream
    df["gross_return_pct"] = new_gross_return_pct
    df["net_return_pct"] = new_net_return_pct
    df["event_barrier_upper_pct"] = new_upper_pct
    df["event_barrier_lower_pct"] = new_lower_pct
    df["event_cost_pct"] = new_cost_pct
    df["holding_minutes"] = new_holding_min

    print(f"processed: {n_processed}, skipped (no data/invalid): {n_skipped}")
    print(f"label distribution: TP={n_relabeled_pos} SL={n_relabeled_neg} timeout={n_relabeled_timeout}")
    if n_processed:
        pos_rate = n_relabeled_pos / n_processed
        print(f"positive_rate: {pos_rate:.4f}")
        avg_net = sum(new_net_return_pct) / len(new_net_return_pct)
        avg_gross = sum(new_gross_return_pct) / len(new_gross_return_pct)
        print(f"avg net_return_pct: {avg_net:.4f} | avg gross: {avg_gross:.4f}")

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"wrote -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
