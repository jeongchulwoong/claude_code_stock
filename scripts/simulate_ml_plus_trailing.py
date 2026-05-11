"""
scripts/simulate_ml_plus_trailing.py — ML 모델 ranking + 트레일링 스톱 결합 시뮬.

각 event 의 ML tp_prob 으로 daily_top_N 선택 → 그 events 에만 트레일링 스톱 적용 →
walk-forward 7세션 EV 측정.

paper-only — 운영 매니페스트 / broker 영향 0.

사용:
  python scripts/simulate_ml_plus_trailing.py \\
    --dataset db/ml/<event_dataset>.csv \\
    --rf 'models/research/iterZB_*.joblib' \\
    --minute-db db/kiwoom_minute_bars.db \\
    --top-n 2 --horizon-minutes 30 --cost-pct 0.4 \\
    --json-out reports/ml/research/ml_plus_trailing.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.trailing_stop import simulate_trade  # noqa: E402


def _resolve(obj):
    if isinstance(obj, dict):
        return obj.get("model") or obj.get("estimator"), obj.get("feature_columns")
    return obj, getattr(obj, "feature_columns", None)


def _tp_idx(estimator) -> int:
    cls = getattr(estimator, "classes_", None)
    if cls is None:
        return 1
    try:
        for i, c in enumerate(cls):
            if int(c) == 1:
                return i
    except Exception:
        pass
    return 1


def _build_X(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    out = np.zeros((len(df), len(cols)), dtype=float)
    for j, c in enumerate(cols):
        if c in df.columns:
            out[:, j] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).values
    return out


def _load_bars(db_path: Path, keys: list[tuple]) -> dict:
    conn = sqlite3.connect(str(db_path))
    lookup: dict = {}
    chunk = 200
    for i in range(0, len(keys), chunk):
        sub = keys[i:i + chunk]
        if not sub:
            continue
        placeholders = " OR ".join(["(ticker=? AND session_date=?)"] * len(sub))
        params = []
        for t, s in sub:
            params.extend([str(t), str(s)])
        cur = conn.execute(
            f"SELECT ticker, session_date, minute_offset, high, low, close FROM minute_bars "
            f"WHERE timeframe='1m' AND ({placeholders}) ORDER BY minute_offset ASC",
            params,
        )
        for row in cur:
            t, s = row[0], row[1]
            try:
                lookup.setdefault((str(t), str(s)), []).append({
                    "minute_offset": int(row[2]),
                    "high": float(row[3]) if row[3] is not None else 0.0,
                    "low":  float(row[4]) if row[4] is not None else 0.0,
                    "close": float(row[5]) if row[5] is not None else 0.0,
                })
            except (TypeError, ValueError):
                continue
    conn.close()
    return lookup


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--rf", required=True)
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--top-n", type=int, default=2)
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--cost-pct", type=float, default=0.4)
    p.add_argument("--steps", default="1.0,2.0,3.0")
    p.add_argument("--chandelier-atr", type=float, default=1.5)
    p.add_argument("--initial-atr-mult", type=float, default=1.5)
    p.add_argument("--oos-date-count", type=int, default=14,
                   help="0=전체 데이터셋. >0=마지막 N 세션만 OOS 평가.")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    steps = tuple(float(x) for x in args.steps.split(","))

    rf_paths = sorted(glob.glob(args.rf))
    if not rf_paths:
        print(f"[error] no RF model match: {args.rf}", file=sys.stderr)
        return 2
    rf_obj = joblib.load(rf_paths[0])
    rf_model, rf_cols = _resolve(rf_obj)
    print(f"[1/4] RF: {Path(rf_paths[0]).name} ({len(rf_cols)} cols)")

    df = pd.read_csv(args.dataset)
    print(f"[2/4] dataset rows: {len(df)}")
    df["ticker"] = df["ticker"].astype(str)
    df["session_date"] = df["session_date"].astype(str)
    df["minutes_since_open"] = pd.to_numeric(df.get("minutes_since_open", 0), errors="coerce").fillna(0).astype(int)
    df["atr_pct"] = pd.to_numeric(df.get("atr_pct", 0), errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df.get("price", 0), errors="coerce").fillna(0.0)
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0)
    df["strategies_passed"] = pd.to_numeric(df.get("strategies_passed", 0), errors="coerce").fillna(0)
    df["label_int"] = pd.to_numeric(df.get("label_int", -2), errors="coerce")

    df = df[(df["fast_score"] >= args.min_fast_score)
            & (df["strategies_passed"] >= args.min_strategies_passed)
            & (df["price"] > 0) & (df["atr_pct"] > 0)].reset_index(drop=True)

    # OOS split
    dates = sorted(df["session_date"].unique())
    if args.oos_date_count > 0 and len(dates) > args.oos_date_count:
        test_dates = dates[-args.oos_date_count:]
        df = df[df["session_date"].isin(test_dates)].reset_index(drop=True)
        print(f"  OOS filter: last {args.oos_date_count} sessions, {len(df)} events")

    # ML score
    print(f"[3/4] ML scoring...")
    X = _build_X(df, rf_cols)
    proba = rf_model.predict_proba(X)
    df["tp_prob"] = proba[:, _tp_idx(rf_model)]

    # daily top-N
    print(f"[3.5/4] selecting daily_top_{args.top_n} by RF tp_prob...")
    picks_list = []
    for _, g in df.groupby("session_date"):
        picks_list.append(g.nlargest(args.top_n, "tp_prob"))
    picks = pd.concat(picks_list, ignore_index=True) if picks_list else pd.DataFrame()
    print(f"  picked {len(picks)} events ({df['session_date'].nunique()} sessions)")

    # load bars
    print(f"[4/4] loading bars + simulating trailing stop...")
    keys = list({(r["ticker"], r["session_date"]) for _, r in picks.iterrows()})
    bars_lookup = _load_bars(Path(args.minute_db), keys)

    sim_rows = []
    for _, row in picks.iterrows():
        key = (row["ticker"], row["session_date"])
        bars = bars_lookup.get(key, [])
        entry_off = int(row["minutes_since_open"])
        future = [b for b in bars if b["minute_offset"] >= entry_off]
        if not future:
            continue
        atr_pct = float(row["atr_pct"])
        price = float(row["price"])
        atr_value = price * (atr_pct / 100.0)
        if atr_value <= 0:
            continue
        out = simulate_trade(
            entry_price=price, entry_atr_value=atr_value,
            future_bars=future[1:], horizon_minutes=args.horizon_minutes,
            steps=steps, chandelier_atr=args.chandelier_atr,
            initial_atr_mult=args.initial_atr_mult, cost_pct=args.cost_pct,
        )
        sim_rows.append({
            "ticker": row["ticker"], "session_date": row["session_date"],
            "tp_prob": float(row["tp_prob"]),
            "new_gross": out["gross_return_pct"],
            "new_net": out["net_return_pct"],
            "max_stage": out["max_stage"],
            "max_profit_atr": out["max_profit_atr"],
            "exit_reason": out["exit_reason"],
        })

    if not sim_rows:
        print("[error] no simulated rows", file=sys.stderr)
        return 2

    sdf = pd.DataFrame(sim_rows)
    n = len(sdf)
    avg_net = float(sdf["new_net"].mean())
    win_rate = float((sdf["new_net"] > 0).mean())
    cum = sdf["new_net"].cumsum()
    dd = float((cum - cum.cummax()).min())
    winners = int(sdf.loc[sdf["new_net"] > 0, "ticker"].nunique()) if "ticker" in sdf.columns else 0

    # walk-forward 7s
    per_s = sdf.groupby("session_date")["new_net"].mean().reset_index()
    per_s = per_s.sort_values("session_date")
    vals = per_s["new_net"].values
    windows = []
    for i in range(0, len(vals) - 6):
        windows.append(float(np.mean(vals[i:i + 7])))
    pos_w = sum(1 for w in windows if w > 0)
    avg_w = float(np.mean(windows)) if windows else None

    crit = {
        "daily_top_n_hit_rate":   {"value": round(win_rate, 4), "target": 0.40, "pass": win_rate >= 0.40},
        "daily_top_n_avg_net":    {"value": round(avg_net, 4), "target": 0.0, "pass": avg_net >= 0.0},
        "walk_forward_7s":        {"n_windows": len(windows), "positive_windows": pos_w,
                                    "avg": round(avg_w, 4) if avg_w is not None else None,
                                    "pass": (avg_w is not None and avg_w > 0)},
        "max_drawdown_pct":       {"value": round(dd, 4), "target": -5.0, "pass": dd >= -5.0},
        "unique_winning_tickers": {"value": winners, "target": 5, "pass": winners >= 5},
    }
    passes = sum(1 for v in crit.values() if v.get("pass"))

    stage_dist = sdf["max_stage"].value_counts().to_dict()
    avg_max_profit = float(sdf["max_profit_atr"].mean())
    median_max_profit = float(sdf["max_profit_atr"].median())

    print(f"\n{'='*70}\nML + TRAILING STOP — daily_top_{args.top_n} (RF ranked)\n{'='*70}")
    print(f"  picks: {n}, sessions: {sdf['session_date'].nunique()}, tickers: {sdf['ticker'].nunique()}")
    print(f"  avg_net_return_pct: {avg_net:.4f} (cost {args.cost_pct}%)")
    print(f"  win_rate (net>0): {win_rate:.4f}")
    print(f"  max_drawdown: {dd:.4f}")
    print(f"  walk_forward_7s: {pos_w}/{len(windows)} positive, avg={avg_w}")
    print(f"  stage_distribution: {stage_dist}")
    print(f"  avg / median max_profit_atr: {avg_max_profit:.3f} / {median_max_profit:.3f}")
    print(f"\n  phase_2: {passes}/5 criteria passed")
    for k, v in crit.items():
        print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dataset": args.dataset,
            "rf_model": rf_paths[0],
            "config": {
                "top_n": args.top_n,
                "horizon_minutes": args.horizon_minutes,
                "cost_pct": args.cost_pct,
                "steps": list(steps),
                "chandelier_atr": args.chandelier_atr,
                "initial_atr_mult": args.initial_atr_mult,
                "oos_date_count": args.oos_date_count,
                "min_fast_score": args.min_fast_score,
                "min_strategies_passed": args.min_strategies_passed,
            },
            "n_events": n,
            "sessions": int(sdf["session_date"].nunique()),
            "avg_net_return_pct": round(avg_net, 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown_pct": round(dd, 4),
            "walk_forward_7s": {"n": len(windows), "positive": pos_w, "avg": avg_w},
            "stage_distribution": {str(k): int(v) for k, v in stage_dist.items()},
            "phase_2": {"passes": passes, "total": 5, "criteria": crit, "phase_2_pass": passes == 5},
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
