"""
scripts/simulate_trailing_stop.py — 기존 event/forward paper 데이터에 ATR 단계별
트레일링 스톱을 적용했을 때의 EV 변화 측정.

각 event 의 entry 시점 이후 분봉 시퀀스 (minute_bars DB) 를 가져와
TrailingStop 시뮬레이션 → 새 청산 가격 + return 산출 → 현재 fixed barrier 대비 비교.

paper-only: 운영 매니페스트 변경 0, broker 영향 0.

사용:
  python scripts/simulate_trailing_stop.py \\
    --events 'db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv' \\
    --minute-db db/kiwoom_minute_bars.db \\
    --horizon-minutes 30 --cost-pct 0.4 \\
    --steps 1.0,2.0,3.0 --chandelier-atr 1.5 --initial-atr-mult 1.5 \\
    --json-out reports/ml/research/trailing_stop_sim.json
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.trailing_stop import simulate_trade  # noqa: E402


def _load_events(pattern: str) -> pd.DataFrame:
    paths = sorted(glob.glob(pattern))
    paths = [p for p in paths if not p.endswith("_today.csv")]
    if not paths:
        return pd.DataFrame()
    dfs = [pd.read_csv(p) for p in paths]
    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return df


def _load_future_bars_lookup(db_path: Path, df: pd.DataFrame) -> dict:
    """모든 (ticker, session_date) 의 분봉을 한 번에 load."""
    conn = sqlite3.connect(str(db_path))
    keys = list({(str(r["ticker"]), str(r["session_date"])) for _, r in df.iterrows()})
    lookup: dict = {}
    chunk = 200
    print(f"  loading bars for {len(keys)} (ticker,session) pairs...", file=sys.stderr)
    for i in range(0, len(keys), chunk):
        sub = keys[i:i + chunk]
        if not sub:
            continue
        placeholders = " OR ".join(["(ticker=? AND session_date=?)"] * len(sub))
        params = []
        for t, s in sub:
            params.extend([t, s])
        cur = conn.execute(
            f"SELECT ticker, session_date, minute_offset, high, low, close "
            f"FROM minute_bars WHERE timeframe='1m' AND ({placeholders}) "
            f"ORDER BY minute_offset ASC",
            params,
        )
        for row in cur:
            t, s = row[0], row[1]
            try:
                bar = {
                    "minute_offset": int(row[2]),
                    "high": float(row[3]) if row[3] is not None else 0.0,
                    "low":  float(row[4]) if row[4] is not None else 0.0,
                    "close": float(row[5]) if row[5] is not None else 0.0,
                }
            except (TypeError, ValueError):
                continue
            lookup.setdefault((str(t), str(s)), []).append(bar)
    conn.close()
    return lookup


def _summarize(rows: list[dict], cost_pct: float) -> dict:
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    n = len(df)
    avg_gross = float(df["new_gross"].mean())
    avg_net = float(df["new_net"].mean())
    win_rate = float((df["new_net"] > 0).mean())
    decided_count = int((df["new_label_int"].isin([0, 1])).sum())
    decided_wins = int(((df["new_label_int"] == 1) | ((df["new_label_int"] == -1) & (df["new_gross"] > 0))).sum())
    cum = df["new_net"].cumsum()
    dd = float((cum - cum.cummax()).min())
    stages = df["max_stage"].value_counts().to_dict()
    exit_reasons = df["exit_reason"].value_counts().to_dict()
    return {
        "n": n,
        "avg_gross_return_pct": round(avg_gross, 4),
        "avg_net_return_pct": round(avg_net, 4),
        "win_rate_net_positive": round(win_rate, 4),
        "decided_count": decided_count,
        "max_drawdown_pct": round(dd, 4),
        "stage_distribution": {str(k): int(v) for k, v in stages.items()},
        "exit_reason_distribution": {str(k): int(v) for k, v in exit_reasons.items()},
        "stage3_rate": float((df["max_stage"] == 3).mean()),
        "stage2_or_higher_rate": float((df["max_stage"] >= 2).mean()),
        "stage1_or_higher_rate": float((df["max_stage"] >= 1).mean()),
        "avg_max_profit_atr": round(float(df["max_profit_atr"].mean()), 3),
        "median_max_profit_atr": round(float(df["max_profit_atr"].median()), 3),
        "p95_max_profit_atr": round(float(df["max_profit_atr"].quantile(0.95)), 3),
    }


def _evaluate_phase2(rows: list[dict]) -> dict:
    """daily_top_2 + walk-forward 7s 측정 (모델 점수 없으니 fast_score rank 사용)."""
    if not rows:
        return {"error": "no rows"}
    df = pd.DataFrame(rows)
    if "session_date" not in df.columns or "fast_score" not in df.columns:
        return {"error": "missing columns"}
    # daily top-2 by fast_score
    picks = []
    for _, g in df.groupby("session_date"):
        picks.append(g.nlargest(2, "fast_score"))
    tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
    if len(tdf) == 0:
        return {"daily_top_2_n": 0}
    hit = float((tdf["new_net"] > 0).mean())
    avg = float(tdf["new_net"].mean())
    cum = tdf["new_net"].cumsum()
    dd = float((cum - cum.cummax()).min())
    winners = int(tdf.loc[tdf["new_net"] > 0, "ticker"].nunique()) if "ticker" in tdf.columns else 0
    # walk-forward 7s
    per_s = []
    for sd, g in df.groupby("session_date"):
        top = g.nlargest(2, "fast_score")
        if len(top):
            per_s.append((str(sd), float(top["new_net"].mean())))
    per_s.sort(key=lambda x: x[0])
    windows = []
    for i in range(0, len(per_s) - 6):
        windows.append(sum(v for _, v in per_s[i:i + 7]) / 7.0)
    pos_w = sum(1 for w in windows if w > 0) if windows else 0
    avg_w = sum(windows) / len(windows) if windows else None
    crit = {
        "daily_top_2_hit_rate": {"value": round(hit, 4), "target": 0.40, "pass": hit >= 0.40},
        "daily_top_2_avg_net":  {"value": round(avg, 4), "target": 0.0, "pass": avg >= 0.0},
        "walk_forward_7s":      {"n_windows": len(windows), "positive_windows": pos_w,
                                  "avg": round(avg_w, 4) if avg_w is not None else None,
                                  "pass": (avg_w is not None and avg_w > 0)},
        "max_drawdown_pct":     {"value": round(dd, 4), "target": -5.0, "pass": dd >= -5.0},
        "unique_winning_tickers": {"value": winners, "target": 5, "pass": winners >= 5},
    }
    passes = sum(1 for v in crit.values() if v.get("pass"))
    return {
        "daily_top_2_n": int(len(tdf)),
        "sessions": int(df["session_date"].nunique()),
        "criteria": crit,
        "passes": passes,
        "total": 5,
        "phase_2_pass": passes == 5,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True, help="event CSV glob")
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--cost-pct", type=float, default=0.4)
    p.add_argument("--steps", default="1.0,2.0,3.0",
                   help="ATR multiples for BE/+1ATR/chandelier (e.g., 1.0,2.0,3.0)")
    p.add_argument("--chandelier-atr", type=float, default=1.5)
    p.add_argument("--initial-atr-mult", type=float, default=1.5)
    p.add_argument("--min-fast-score", type=float, default=0.0)
    p.add_argument("--min-strategies-passed", type=int, default=0)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    steps = tuple(float(x) for x in args.steps.split(","))
    if len(steps) != 3:
        print(f"[error] --steps must be 3 floats (got {steps})", file=sys.stderr)
        return 2

    df = _load_events(args.events)
    if df.empty:
        print(f"[error] no events match: {args.events}", file=sys.stderr)
        return 2
    print(f"[1/4] loaded {len(df)} events from {args.events}")

    # 정규화
    df["ticker"] = df["ticker"].astype(str)
    df["session_date"] = df["session_date"].astype(str)
    df["minutes_since_open"] = pd.to_numeric(df.get("minutes_since_open", 0), errors="coerce").fillna(0).astype(int)
    df["atr_pct"] = pd.to_numeric(df.get("atr_pct", 0), errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df.get("price", 0), errors="coerce").fillna(0.0)
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0)
    df["strategies_passed"] = pd.to_numeric(df.get("strategies_passed", 0), errors="coerce").fillna(0)
    df["label_int"] = pd.to_numeric(df.get("label_int", -2), errors="coerce")
    df["return_pct"] = pd.to_numeric(df.get("return_pct", 0), errors="coerce").fillna(0.0)
    if "gross_return_pct" in df.columns:
        df["gross_return_pct"] = pd.to_numeric(df["gross_return_pct"], errors="coerce").fillna(0.0)

    # candidate filter
    if args.min_fast_score > 0:
        df = df[df["fast_score"] >= args.min_fast_score]
    if args.min_strategies_passed > 0:
        df = df[df["strategies_passed"] >= args.min_strategies_passed]
    df = df[(df["price"] > 0) & (df["atr_pct"] > 0)].reset_index(drop=True)
    print(f"[2/4] after filter (fs≥{args.min_fast_score} strat≥{args.min_strategies_passed} valid price/atr): {len(df)}")
    if df.empty:
        print("[error] no events after filter", file=sys.stderr)
        return 2

    print(f"[3/4] loading minute bars...")
    bars_lookup = _load_future_bars_lookup(Path(args.minute_db), df)
    print(f"  loaded {len(bars_lookup)} (ticker,session) groups")

    print(f"[4/4] simulating trailing stop (steps={steps}, chand={args.chandelier_atr}, "
          f"init={args.initial_atr_mult}, horizon={args.horizon_minutes}m, cost={args.cost_pct}%) ...")
    sim_rows = []
    n_no_bars = 0
    for _, row in df.iterrows():
        key = (str(row["ticker"]), str(row["session_date"]))
        bars = bars_lookup.get(key, [])
        entry_off = int(row["minutes_since_open"])
        future = [b for b in bars if b["minute_offset"] >= entry_off]
        if not future:
            n_no_bars += 1
            continue
        atr_pct = float(row["atr_pct"])
        price = float(row["price"])
        atr_value = price * (atr_pct / 100.0)
        if atr_value <= 0:
            continue
        out = simulate_trade(
            entry_price=price, entry_atr_value=atr_value,
            future_bars=future[1:],   # entry 이후 봉부터
            is_long=True, horizon_minutes=args.horizon_minutes,
            steps=steps, chandelier_atr=args.chandelier_atr,
            initial_atr_mult=args.initial_atr_mult, cost_pct=args.cost_pct,
        )
        sim_rows.append({
            "ticker": row["ticker"], "session_date": row["session_date"],
            "minutes_since_open": entry_off, "fast_score": row["fast_score"],
            "strategies_passed": row["strategies_passed"],
            "orig_label_int": int(row["label_int"]) if not pd.isna(row["label_int"]) else -2,
            "orig_return_pct": float(row["return_pct"]),
            "orig_gross": float(row.get("gross_return_pct", row["return_pct"] + 0.24)),
            "new_gross": out["gross_return_pct"],
            "new_net": out["net_return_pct"],
            "new_label_int": 1 if out["gross_return_pct"] > 0 else (0 if out["gross_return_pct"] < 0 else -1),
            "max_stage": out["max_stage"],
            "max_profit_atr": out["max_profit_atr"],
            "exit_reason": out["exit_reason"],
            "bars_held": out["bars_held"],
        })
    print(f"  simulated {len(sim_rows)} events ({n_no_bars} skipped: no future bars)")

    summary = _summarize(sim_rows, args.cost_pct)
    phase2 = _evaluate_phase2(sim_rows)

    # 원본 (fixed barrier) 대비 비교
    if sim_rows:
        sdf = pd.DataFrame(sim_rows)
        orig_avg_gross = float(sdf["orig_gross"].mean())
        orig_avg_net = orig_avg_gross - args.cost_pct
        delta_gross = summary["avg_gross_return_pct"] - orig_avg_gross
        delta_net = summary["avg_net_return_pct"] - orig_avg_net
        comparison = {
            "orig_avg_gross_return_pct": round(orig_avg_gross, 4),
            "orig_avg_net_return_pct": round(orig_avg_net, 4),
            "trailing_avg_gross_return_pct": summary["avg_gross_return_pct"],
            "trailing_avg_net_return_pct": summary["avg_net_return_pct"],
            "delta_gross_return_pct": round(delta_gross, 4),
            "delta_net_return_pct": round(delta_net, 4),
        }
    else:
        comparison = {}

    print()
    print("=" * 70)
    print(f"  TRAILING STOP SIMULATION RESULT")
    print("=" * 70)
    for k, v in (comparison or {}).items():
        print(f"  {k:<45} {v}")
    print()
    print(f"  summary:")
    for k, v in summary.items():
        if isinstance(v, dict):
            print(f"    {k}:")
            for kk, vv in v.items():
                print(f"      {kk}: {vv}")
        else:
            print(f"    {k}: {v}")
    print()
    print(f"  phase_2: {phase2.get('passes', 0)}/{phase2.get('total', 5)} criteria passed")
    for k, v in phase2.get("criteria", {}).items():
        print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "events_glob": args.events,
            "events_total": int(len(df)),
            "simulated_count": len(sim_rows),
            "config": {
                "horizon_minutes": args.horizon_minutes,
                "cost_pct": args.cost_pct,
                "steps": list(steps),
                "chandelier_atr": args.chandelier_atr,
                "initial_atr_mult": args.initial_atr_mult,
                "min_fast_score": args.min_fast_score,
                "min_strategies_passed": args.min_strategies_passed,
            },
            "comparison": comparison,
            "summary": summary,
            "phase_2": phase2,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
