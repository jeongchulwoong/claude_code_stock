"""
scripts/join_microstructure_to_dataset.py — event dataset 에 microstructure feature 추가.

키: (ticker, session_date, minute_offset).
정확 매칭 없으면 ± lookback window 내에서 가장 가까운 microstructure snapshot 사용.
못 찾으면 ms_* features = 0 + has_microstructure_data=0.

사용:
  python scripts/join_microstructure_to_dataset.py \\
    --dataset db/ml/<event_dataset>.csv \\
    --microstructure db/ml/microstructure_features_per_minute_*.csv \\
    --out db/ml/<event_dataset_with_ms>.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--microstructure", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--lookback-window", type=int, default=2,
                   help="정확 매칭 없을 때 검색할 ± 분 범위 (기본 2)")
    args = p.parse_args(argv)

    df = pd.read_csv(args.dataset)
    ms = pd.read_csv(args.microstructure)

    # minute_offset 컬럼 통일
    if "minute_offset" not in df.columns:
        if "minutes_since_open" in df.columns:
            df["minute_offset"] = pd.to_numeric(df["minutes_since_open"], errors="coerce").fillna(0).astype(int)
        else:
            df["minute_offset"] = 0
    df["minute_offset"] = pd.to_numeric(df["minute_offset"], errors="coerce").fillna(0).astype(int)
    ms["minute_offset"] = pd.to_numeric(ms["minute_offset"], errors="coerce").fillna(0).astype(int)
    df["ticker"] = df["ticker"].astype(str)
    ms["ticker"] = ms["ticker"].astype(str)
    df["session_date"] = df["session_date"].astype(str)
    ms["session_date"] = ms["session_date"].astype(str)

    # microstructure ticker 키 정규화: 종목코드만 (e.g. "055550" → "055550.KS" 매칭 시도)
    # event dataset 의 ticker 는 일반적으로 "005930.KS" 형태. ms 는 "055550" 형태.
    # 둘 다 시도해서 매칭.
    ms_lookup: dict[tuple[str, str, int], dict] = {}
    ms_cols = [c for c in ms.columns if c.startswith("ms_") or c in ("spread_pct",)]
    for _, row in ms.iterrows():
        key = (str(row["ticker"]), str(row["session_date"]), int(row["minute_offset"]))
        ms_lookup[key] = {c: row[c] for c in ms_cols}

    # 추가 컬럼 초기화
    for c in ms_cols:
        df[c] = 0.0
    df["has_microstructure_data"] = 0

    matched = 0
    matched_via_lookback = 0
    n = len(df)
    for i, row in df.iterrows():
        ticker_dataset = str(row["ticker"])
        # ticker 정규화 — "005930.KS" → "005930" 시도
        ticker_short = ticker_dataset.split(".")[0]
        session = str(row["session_date"])
        target_off = int(row["minute_offset"])

        # 정확 매칭 시도
        for t_try in (ticker_dataset, ticker_short, ticker_short.lstrip("0")):
            key = (t_try, session, target_off)
            if key in ms_lookup:
                for c in ms_cols:
                    df.at[i, c] = ms_lookup[key][c]
                df.at[i, "has_microstructure_data"] = 1
                matched += 1
                break
        else:
            # lookback ± window 내 가장 가까운 minute 매칭
            best = None
            best_dist = args.lookback_window + 1
            for t_try in (ticker_dataset, ticker_short, ticker_short.lstrip("0")):
                for d in range(-args.lookback_window, args.lookback_window + 1):
                    if d == 0:
                        continue
                    key = (t_try, session, target_off + d)
                    if key in ms_lookup:
                        if abs(d) < best_dist:
                            best = ms_lookup[key]
                            best_dist = abs(d)
                if best is not None:
                    break
            if best is not None:
                for c in ms_cols:
                    df.at[i, c] = best[c]
                df.at[i, "has_microstructure_data"] = 1
                matched_via_lookback += 1
    print(f"dataset rows: {n}, exact match: {matched}, lookback match: {matched_via_lookback}, "
          f"total ms coverage: {matched + matched_via_lookback} ({(matched + matched_via_lookback) / n * 100:.2f}%)")

    df.to_csv(args.out, index=False)
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
