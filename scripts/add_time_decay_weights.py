"""
scripts/add_time_decay_weights.py — dataset 에 시간 감쇠 sample_weight 추가.

규칙: 가장 최근 session 의 weight=1.0, 과거로 갈수록 exp(-lambda * days_ago) 감쇠.
기본 lambda=0.05 (반감기 약 14일).

학습 시 --use-sample-weight 와 함께 사용. balance-sessions 옵션과 곱해진다.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    p.add_argument("--out", dest="out_path", required=True)
    p.add_argument("--lambda-per-day", type=float, default=0.05)
    args = p.parse_args(argv)

    df = pd.read_csv(args.in_path)
    if "session_date" not in df.columns:
        print("[error] missing session_date column", file=sys.stderr)
        return 2

    df["session_date"] = pd.to_datetime(df["session_date"], errors="coerce")
    max_date = df["session_date"].max()
    if pd.isna(max_date):
        print("[error] no valid session_date", file=sys.stderr)
        return 2

    days_ago = (max_date - df["session_date"]).dt.days.astype(float).fillna(0.0)
    decay = (-args.lambda_per_day * days_ago).apply(math.exp)
    df["sample_weight"] = decay.clip(lower=0.05)   # 너무 작아지지 않도록 floor

    df["session_date"] = df["session_date"].dt.strftime("%Y-%m-%d")
    df.to_csv(args.out_path, index=False)
    print(f"wrote {len(df)} rows -> {args.out_path}")
    print(f"sample_weight stats: min={df['sample_weight'].min():.4f} max={df['sample_weight'].max():.4f} mean={df['sample_weight'].mean():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
