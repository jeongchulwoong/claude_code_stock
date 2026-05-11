"""
scripts/simulate_limit_order_cost.py — Forward paper / training events 에 다른 주문 정책
를 적용했을 때의 실현 cost + EV 변화 측정.

체결률 모델:
  - market: 100% 체결
  - limit: ATR 별 추정 fill_rate (e.g., atr_pct 0.5%: ~75%)
  - 미체결 시 trade 발생 X → cost 0, 손익 0

paper-only — broker 호출 0건.

사용:
  python scripts/simulate_limit_order_cost.py \\
    --events 'db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv' \\
    --json-out reports/ml/research/limit_order_sim.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.limit_order_policy import (  # noqa: E402
    expected_realized_cost_pct,
    policy_aggressive_market,
    policy_conservative_limit,
    policy_hybrid_limit_market,
)


def _load_events(pattern: str) -> pd.DataFrame:
    paths = sorted(glob.glob(pattern))
    paths = [p for p in paths if not p.endswith("_today.csv")]
    if not paths:
        return pd.DataFrame()
    dfs = [pd.read_csv(p) for p in paths]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _simulate_policy(df: pd.DataFrame, policy, seed: int = 42) -> dict:
    """policy 별 실현 비용 + EV 시뮬.

    체결률 모델:
      - market: 항상 체결
      - limit: ATR 의존 확률로 체결 (uniform random vs fill_rate)
      - 미체결 시 trade 발생 X (gross_return = 0, cost = 0)
    """
    n = len(df)
    rng = np.random.default_rng(seed)
    rolls = rng.uniform(0.0, 1.0, size=n)

    filled = np.zeros(n, dtype=bool)
    realized_gross = np.zeros(n, dtype=float)
    realized_cost  = np.zeros(n, dtype=float)
    realized_net   = np.zeros(n, dtype=float)

    for i, (_, row) in enumerate(df.iterrows()):
        atr_pct = float(row.get("atr_pct", 0.5))
        gross = float(row.get("gross_return_pct", row.get("return_pct", 0.0) + 0.24))
        if policy.fill_model is None:
            fill_rate = 1.0
        else:
            fill_rate = policy.fill_model.estimate_fill_rate(atr_pct)
        is_filled = rolls[i] < fill_rate
        filled[i] = is_filled
        if is_filled:
            cost = policy.cost_model.total_pct
            realized_gross[i] = gross
            realized_cost[i]  = cost
            realized_net[i]   = gross - cost
        # else: 미체결 → 모두 0

    n_filled = int(filled.sum())
    avg_gross_filled = float(realized_gross[filled].mean()) if n_filled > 0 else 0.0
    avg_cost_filled  = float(realized_cost[filled].mean()) if n_filled > 0 else 0.0
    avg_net_filled   = float(realized_net[filled].mean()) if n_filled > 0 else 0.0
    avg_net_per_attempt = float(realized_net.mean())   # 미체결 0 포함
    win_rate_filled = float((realized_net[filled] > 0).mean()) if n_filled > 0 else 0.0

    return {
        "policy": policy.name,
        "entry_type": policy.entry_type,
        "exit_type": policy.exit_type,
        "n_attempts": n,
        "n_filled": n_filled,
        "fill_rate_overall": round(n_filled / n if n > 0 else 0.0, 4),
        "avg_gross_filled_pct": round(avg_gross_filled, 4),
        "avg_cost_filled_pct": round(avg_cost_filled, 4),
        "avg_net_filled_pct": round(avg_net_filled, 4),
        "avg_net_per_attempt_pct": round(avg_net_per_attempt, 4),
        "win_rate_filled": round(win_rate_filled, 4),
        "filled_indices_sum": int(filled.sum()),
    }


def _simulate_daily_top_n(df: pd.DataFrame, policy, n_per_day: int = 2, seed: int = 42) -> dict:
    """daily_top_N (fast_score 정렬) + 정책별 실현. walk-forward 7s 측정 포함."""
    if df.empty or "session_date" not in df.columns:
        return {}
    df = df.copy()
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0)

    picks = []
    for _, g in df.groupby("session_date"):
        picks.append(g.nlargest(n_per_day, "fast_score"))
    pdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
    if pdf.empty:
        return {}

    rng = np.random.default_rng(seed)
    rolls = rng.uniform(0.0, 1.0, size=len(pdf))
    nets = []
    grosses = []
    n_filled = 0
    per_session: dict[str, list[float]] = {}
    for i, (_, row) in enumerate(pdf.iterrows()):
        atr_pct = float(row.get("atr_pct", 0.5))
        gross = float(row.get("gross_return_pct", row.get("return_pct", 0.0) + 0.24))
        if policy.fill_model is None:
            fill_rate = 1.0
        else:
            fill_rate = policy.fill_model.estimate_fill_rate(atr_pct)
        is_filled = rolls[i] < fill_rate
        if is_filled:
            net = gross - policy.cost_model.total_pct
            nets.append(net); grosses.append(gross); n_filled += 1
            per_session.setdefault(str(row["session_date"]), []).append(net)
        else:
            # 미체결 — 이 자리에 trade 없음. session 평균에서 제외.
            pass
    if not nets:
        return {"policy": policy.name, "no_filled": True}

    arr = np.array(nets)
    cum = arr.cumsum()
    dd = float((cum - np.maximum.accumulate(cum)).min())

    sessions_sorted = sorted(per_session.keys())
    sess_avg = [np.mean(per_session[s]) for s in sessions_sorted]
    windows = []
    for i in range(0, len(sess_avg) - 6):
        windows.append(float(np.mean(sess_avg[i:i + 7])))
    pos_w = sum(1 for w in windows if w > 0)
    avg_w = float(np.mean(windows)) if windows else None

    return {
        "policy": policy.name,
        "n_picks": int(len(pdf)),
        "n_filled": int(n_filled),
        "fill_rate_picks": round(n_filled / len(pdf) if len(pdf) > 0 else 0.0, 4),
        "avg_gross_filled_pct": round(float(np.mean(grosses)), 4),
        "avg_net_filled_pct": round(float(np.mean(nets)), 4),
        "win_rate_filled": round(float((arr > 0).mean()), 4),
        "max_drawdown_pct": round(dd, 4),
        "n_sessions_with_fill": len(sessions_sorted),
        "walk_forward_7s": {
            "n_windows": len(windows), "positive_windows": pos_w,
            "avg": round(avg_w, 4) if avg_w is not None else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True)
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--top-n", type=int, default=2)
    p.add_argument("--json-out", default=None)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    df = _load_events(args.events)
    if df.empty:
        print(f"[error] no events: {args.events}", file=sys.stderr)
        return 2

    # 컬럼 정규화
    if "label_int" in df.columns:
        df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
        df = df[df["label_int"].isin([-1, 0, 1])]
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0)
    df["strategies_passed"] = pd.to_numeric(df.get("strategies_passed", 0), errors="coerce").fillna(0)
    df["atr_pct"] = pd.to_numeric(df.get("atr_pct", 0.5), errors="coerce").fillna(0.5)

    if args.min_fast_score > 0:
        df = df[df["fast_score"] >= args.min_fast_score]
    if args.min_strategies_passed > 0:
        df = df[df["strategies_passed"] >= args.min_strategies_passed]
    df = df.reset_index(drop=True)
    print(f"  events after filter: {len(df)}, sessions: {df['session_date'].nunique() if 'session_date' in df else 0}")

    policies = [
        policy_aggressive_market(),
        policy_conservative_limit(),
        policy_hybrid_limit_market(),
    ]

    # 1. 전체 풀에서 정책별 실현 비용
    print(f"\n{'='*90}\n  POLICY COST MODEL (theoretical, atr=0.5%):\n{'='*90}")
    print(f"  {'policy':<40} {'entry':<8} {'exit':<8} {'cost%':>7} {'fill_rate':>10}")
    print(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*7} {'-'*10}")
    cost_summary = []
    for pol in policies:
        em = expected_realized_cost_pct(pol, atr_pct=0.5)
        print(f"  {pol.name:<40} {em['entry_type']:<8} {em['exit_type']:<8} "
              f"{em['cost_per_filled_trade_pct']:>7.4f} {em['fill_rate']:>10.4f}")
        cost_summary.append(em)

    print(f"\n{'='*90}\n  REALIZED EV — 전체 필터 통과 events ({len(df)}):\n{'='*90}")
    print(f"  {'policy':<40} {'n_filled':>9} {'fill_rate':>10} {'avg_gross':>10} {'avg_net':>9}")
    print(f"  {'-'*40} {'-'*9} {'-'*10} {'-'*10} {'-'*9}")
    pool_results = []
    for pol in policies:
        r = _simulate_policy(df, pol, seed=args.seed)
        pool_results.append(r)
        print(f"  {pol.name:<40} {r['n_filled']:>9} {r['fill_rate_overall']:>10.4f} "
              f"{r['avg_gross_filled_pct']:>10.4f} {r['avg_net_filled_pct']:>9.4f}")

    print(f"\n{'='*90}\n  DAILY TOP-{args.top_n} EV + WALK-FORWARD:\n{'='*90}")
    print(f"  {'policy':<40} {'picks':>6} {'filled':>7} {'avg_net':>8} {'win':>5} {'dd':>8} {'wf_avg':>9} {'wf_pos':>8}")
    print(f"  {'-'*40} {'-'*6} {'-'*7} {'-'*8} {'-'*5} {'-'*8} {'-'*9} {'-'*8}")
    top_results = []
    for pol in policies:
        r = _simulate_daily_top_n(df, pol, n_per_day=args.top_n, seed=args.seed)
        top_results.append(r)
        if r.get("no_filled"):
            print(f"  {pol.name:<40} {'no_filled':>40}")
            continue
        wf = r.get("walk_forward_7s", {})
        print(f"  {pol.name:<40} {r['n_picks']:>6} {r['n_filled']:>7} "
              f"{r['avg_net_filled_pct']:>8.4f} {r['win_rate_filled']:>5.2f} "
              f"{r['max_drawdown_pct']:>8.4f} {wf.get('avg', '-')!s:>9} "
              f"{wf.get('positive_windows','?')}/{wf.get('n_windows','?'):<2}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "events_glob": args.events,
            "events_total": int(len(df)),
            "min_fast_score": args.min_fast_score,
            "min_strategies_passed": args.min_strategies_passed,
            "top_n": args.top_n,
            "cost_summary": cost_summary,
            "pool_results": pool_results,
            "top_results": top_results,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
