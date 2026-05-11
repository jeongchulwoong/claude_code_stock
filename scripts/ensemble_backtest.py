"""
scripts/ensemble_backtest.py — 여러 model artifact 의 확률을 평균해 백테스트.

사용:
  python scripts/ensemble_backtest.py \\
    --models 'models/research/iterJ_*.joblib,models/research/iterQ_*.joblib' \\
    --dataset db/ml/<augmented_dataset>.csv \\
    --oos-date-count 14 --cost-pct 0.4
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

COST_PCT_DEFAULT = 0.4


def _resolve_estimator(obj):
    if isinstance(obj, dict):
        return obj.get("model") or obj.get("estimator"), obj.get("feature_columns")
    return obj, getattr(obj, "feature_columns", None)


def _tp_class_index(estimator) -> int:
    classes = getattr(estimator, "classes_", None)
    if classes is None:
        return 1
    try:
        for i, c in enumerate(list(classes)):
            if int(c) == 1:
                return i
    except Exception:
        pass
    return 1


def _build_X(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in feature_columns:
        if col in df.columns:
            out[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            out[col] = 0.0
    return out


def _metrics(picks: pd.DataFrame, ret_col: str) -> dict:
    if len(picks) == 0:
        return {"n": 0, "hit_rate": None, "avg_return_pct": None, "max_drawdown_pct": None,
                "wins": 0, "losses": 0}
    wins = int((picks[ret_col] > 0).sum())
    losses = int((picks[ret_col] <= 0).sum())
    avg = float(picks[ret_col].mean())
    cum = picks[ret_col].cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(picks), "hit_rate": round(wins / len(picks), 4),
            "avg_return_pct": round(avg, 4), "max_drawdown_pct": round(dd, 4),
            "wins": wins, "losses": losses}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--models", required=True, help="comma-separated glob patterns")
    p.add_argument("--dataset", required=True)
    p.add_argument("--oos-date-count", type=int, default=14)
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--cost-pct", type=float, default=COST_PCT_DEFAULT)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    # 모델 로드
    model_paths: list[str] = []
    for pat in args.models.split(","):
        model_paths.extend(sorted(glob.glob(pat.strip())))
    if not model_paths:
        print(f"[error] no models match: {args.models}", file=sys.stderr)
        return 2
    print(f"loading {len(model_paths)} models:")
    for mp in model_paths:
        print(f"  {Path(mp).name}")

    models = []
    for mp in model_paths:
        obj = joblib.load(mp)
        est, cols = _resolve_estimator(obj)
        if est is None or not cols:
            print(f"[skip] no model/cols in {mp}", file=sys.stderr)
            continue
        models.append((mp, est, list(cols)))
    if not models:
        print("[error] no usable models", file=sys.stderr)
        return 2

    df = pd.read_csv(args.dataset)
    if "session_date" not in df.columns or "label_int" not in df.columns or "return_pct" not in df.columns:
        print("[error] dataset missing required columns", file=sys.stderr)
        return 2

    df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
    df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce").fillna(0.0)
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0.0)
    df["strategies_passed"] = pd.to_numeric(df.get("strategies_passed", 0), errors="coerce").fillna(0.0)
    # gross_return_pct 가 있으면 그쪽이 비용 차감 전 수익. cost_pct 인자로 새로 차감.
    # 없으면 return_pct 가 이미 net 으로 간주 (이전 빌더의 0.24% 비용 정책).
    if "gross_return_pct" in df.columns:
        df["gross_return_pct"] = pd.to_numeric(df["gross_return_pct"], errors="coerce").fillna(0.0)
    df = df[df["label_int"].isin([-1, 0, 1])].copy()

    # OOS split — 마지막 N session_date
    dates = sorted(df["session_date"].astype(str).unique())
    if len(dates) < args.oos_date_count + 1:
        print(f"[error] only {len(dates)} dates, need > {args.oos_date_count}", file=sys.stderr)
        return 2
    test_dates = set(dates[-args.oos_date_count:])
    test = df[df["session_date"].astype(str).isin(test_dates)].copy()
    # candidate filter
    test = test[(test["fast_score"] >= args.min_fast_score)
                & (test["strategies_passed"] >= args.min_strategies_passed)].copy()
    print(f"test rows after filter: {len(test)} (sessions={len(test_dates)})")

    if len(test) == 0:
        print("[error] empty test set", file=sys.stderr)
        return 2

    # 확률 누적
    test = test.reset_index(drop=True)
    # 비용 차감 net return — gross_return_pct 가 있으면 거기서 cost 빼고, 없으면 return_pct 그대로 사용.
    if "gross_return_pct" in test.columns:
        test["net_return_pct"] = test["gross_return_pct"] - args.cost_pct
    else:
        test["net_return_pct"] = test["return_pct"] - args.cost_pct
    sum_prob = np.zeros(len(test))
    valid_models = 0
    for mp, est, cols in models:
        X = _build_X(test, cols)
        proba = est.predict_proba(X)
        tp_idx = _tp_class_index(est)
        sum_prob += proba[:, tp_idx]
        valid_models += 1
    test["ensemble_prob"] = sum_prob / max(1, valid_models)

    print(f"\n=== ENSEMBLE BACKTEST ({valid_models} models, cost={args.cost_pct}%) ===")
    print(f"test events: {len(test)} | sessions: {len(test_dates)}")
    print()

    # daily top-N
    print("=== daily top-N (rank by ensemble_prob) ===")
    by_topn = {}
    for n in (1, 2, 3, 5):
        picks = []
        for _, g in test.groupby("session_date"):
            top = g.nlargest(n, "ensemble_prob")
            picks.append(top)
        tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
        m_all = _metrics(tdf, "net_return_pct")
        tdf_dec = tdf[tdf["label_int"].isin([0, 1])] if len(tdf) else tdf
        m_dec = _metrics(tdf_dec, "net_return_pct")
        unique_winners = (
            int(tdf[(tdf["label_int"] == 1) & ("ticker" in tdf.columns)]["ticker"].nunique())
            if len(tdf) and "ticker" in tdf.columns else 0
        )
        by_topn[n] = {"all": m_all, "decided": m_dec, "unique_winners": unique_winners}
        print(f"  daily_top_{n}  all_n={m_all['n']:>4} pos_rate={m_all['hit_rate']!s:>6} "
              f"avg_net={m_all['avg_return_pct']!s:>9} dd={m_all['max_drawdown_pct']!s:>9} | "
              f"decided_n={m_dec['n']:>4} hit={m_dec['hit_rate']!s:>6} | wins_tickers={unique_winners}")
    print()

    # threshold sweep
    print("=== ensemble threshold sweep ===")
    by_thr = {}
    for thr in (0.30, 0.40, 0.45, 0.50, 0.55, 0.60):
        sub = test[test["ensemble_prob"] >= thr]
        m_all = _metrics(sub, "net_return_pct")
        sub_dec = sub[sub["label_int"].isin([0, 1])]
        m_dec = _metrics(sub_dec, "net_return_pct")
        by_thr[thr] = {"all": m_all, "decided": m_dec}
        print(f"  thr={thr:.2f}  n={m_all['n']:>5} pos_rate={m_all['hit_rate']!s:>6} avg_net={m_all['avg_return_pct']!s:>9} | "
              f"decided_n={m_dec['n']:>5} hit={m_dec['hit_rate']!s:>6}")
    print()

    # top-pct
    print("=== top-pct (rank by ensemble_prob) ===")
    by_pct = {}
    for pct in (0.01, 0.05, 0.10, 0.20):
        k = max(1, int(len(test) * pct))
        sub = test.nlargest(k, "ensemble_prob")
        m_all = _metrics(sub, "net_return_pct")
        sub_dec = sub[sub["label_int"].isin([0, 1])]
        m_dec = _metrics(sub_dec, "net_return_pct")
        by_pct[pct] = {"all": m_all, "decided": m_dec}
        print(f"  top_{pct*100:>4.1f}% n={m_all['n']:>5} pos_rate={m_all['hit_rate']!s:>6} avg_net={m_all['avg_return_pct']!s:>9} | "
              f"decided_n={m_dec['n']:>5} hit={m_dec['hit_rate']!s:>6}")
    print()

    # ── walk-forward 7-session top-2 EV
    print("=== walk-forward 7-session daily_top_2 EV ===")
    per_session = []
    for sd, g in test.groupby("session_date"):
        top = g.nlargest(2, "ensemble_prob")
        if len(top) == 0:
            continue
        per_session.append((str(sd), float(top["net_return_pct"].mean())))
    per_session.sort(key=lambda x: x[0])
    windows = []
    for i in range(0, len(per_session) - 6):
        chunk = per_session[i:i + 7]
        windows.append(sum(v for _, v in chunk) / 7.0)
    if windows:
        positives = sum(1 for w in windows if w > 0)
        print(f"  windows={len(windows)} positive_windows={positives} avg={sum(windows)/len(windows):.4f}")
    else:
        print("  not enough sessions for 7-day rolling window")
    print()

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "models": [Path(m).name for m, _, _ in models],
            "n_models": valid_models,
            "test_rows": len(test),
            "test_sessions": sorted(test_dates),
            "cost_pct": args.cost_pct,
            "by_daily_top_n": by_topn,
            "by_threshold": by_thr,
            "by_top_pct": by_pct,
            "walk_forward_7s": {
                "n_windows": len(windows),
                "positive_windows": sum(1 for w in windows if w > 0) if windows else 0,
                "avg": (sum(windows) / len(windows)) if windows else None,
            },
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
