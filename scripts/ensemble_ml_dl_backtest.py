"""
scripts/ensemble_ml_dl_backtest.py — ML(RF) + DL(MLP) + AE filter 앙상블 백테스트.

매니페스트 phase_4 정신을 살려:
  - RF 와 DL 의 tp_prob 평균 (weights 가변)
  - AE 가 outlier 판정한 row 는 advisory 로 점수 페널티 (또는 제외)
  - paper-only 측정 — 진입 결정 영향 0

평가 데이터:
  - 학습 dataset (in-distribution OOS): training dataset 의 OOS 14세션
  - **forward paper events** (out-of-distribution 실 데이터): 5/9 기록된 1242건

phase_2 게이트 측정:
  - daily_top_2 hit_rate ≥ 0.40
  - daily_top_2 cost-adjusted avg_net ≥ 0
  - walk-forward 7-session EV positive
  - max_drawdown ≤ 5%
  - unique_winning_tickers ≥ 5

사용:
  python scripts/ensemble_ml_dl_backtest.py \\
    --rf models/research/iterZB_*.joblib \\
    --dl models/research/dl_deep_mlp_v1.joblib \\
    --ae models/research/dl_autoencoder_v1.joblib \\
    --train-dataset db/ml/daytrade_event_dataset_atr15x06_cost04_20260511.csv \\
    --forward-events 'db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv' \\
    --rf-weight 0.5 --dl-weight 0.5 \\
    --json-out reports/ml/research/ml_dl_ensemble_backtest.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _resolve(obj):
    if isinstance(obj, dict):
        m = obj.get("model") or obj.get("estimator")
        return m, obj.get("feature_columns")
    return obj, getattr(obj, "feature_columns", None)


def _tp_idx(estimator) -> int:
    classes = getattr(estimator, "classes_", None)
    if classes is None:
        return 1
    try:
        for i, c in enumerate(classes):
            if int(c) == 1:
                return i
    except Exception:
        pass
    return 1


def _build_X(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    out = np.zeros((len(df), len(cols)), dtype=float)
    for j, c in enumerate(cols):
        if c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce").fillna(0.0).values
            out[:, j] = v
    return out


def _metrics_for_picks(picks: pd.DataFrame, ret_col: str) -> dict:
    if len(picks) == 0:
        return {"n": 0, "hit_rate": None, "avg_return_pct": None,
                "max_drawdown_pct": None, "unique_winning_tickers": 0}
    wins = int((picks[ret_col] > 0).sum())
    losses = int((picks[ret_col] <= 0).sum())
    hit = wins / len(picks)
    avg = float(picks[ret_col].mean())
    cum = picks[ret_col].cumsum()
    dd = float((cum - cum.cummax()).min())
    uw = 0
    if "ticker" in picks.columns:
        winners = picks.loc[picks[ret_col] > 0, "ticker"]
        uw = int(winners.nunique())
    return {
        "n": len(picks), "hit_rate": round(hit, 4),
        "avg_return_pct": round(avg, 4),
        "max_drawdown_pct": round(dd, 4),
        "wins": wins, "losses": losses,
        "unique_winning_tickers": uw,
    }


def _score_dataset(
    df: pd.DataFrame,
    rf_model, rf_cols: list[str],
    dl_model, dl_cols: list[str],
    ae_artifact: dict,
    rf_weight: float, dl_weight: float,
    ae_penalty: float = 0.0,
) -> pd.DataFrame:
    """RF + DL tp_prob 평균 (weighted). AE outlier 에 점수 페널티."""
    df = df.reset_index(drop=True).copy()
    X_rf = _build_X(df, rf_cols)
    X_dl = _build_X(df, dl_cols)
    rf_proba = rf_model.predict_proba(X_rf)
    dl_proba = dl_model.predict_proba(X_dl)
    rf_p = rf_proba[:, _tp_idx(rf_model)]
    dl_p = dl_proba[:, _tp_idx(dl_model)]

    # AE outlier flag
    is_outlier = np.zeros(len(df), dtype=bool)
    if ae_artifact:
        ae = ae_artifact["ae"]
        scaler = ae.pipeline.named_steps["scaler"]
        mlp = ae.pipeline.named_steps["ae"]
        X_ae = _build_X(df, ae.feature_columns)
        X_scaled = scaler.transform(X_ae)
        X_recon = mlp.predict(X_scaled)
        errors = np.mean((X_scaled - X_recon) ** 2, axis=1)
        is_outlier = errors > ae.anomaly_threshold
        df["ae_recon_error"] = errors
        df["ae_is_outlier"] = is_outlier.astype(int)

    w = rf_weight + dl_weight
    if w <= 0:
        w = 1.0
    ensemble_p = (rf_weight * rf_p + dl_weight * dl_p) / w
    # AE outlier 에 페널티 (0.0 = 영향 없음 / 0.1 = 확률 -0.1 / 1.0 = 사실상 제외)
    if ae_penalty > 0:
        ensemble_p = np.where(is_outlier, np.maximum(0.0, ensemble_p - ae_penalty), ensemble_p)
    df["rf_tp_prob"] = rf_p
    df["dl_tp_prob"] = dl_p
    df["ensemble_tp_prob"] = ensemble_p
    return df


def _evaluate_phase_2(df: pd.DataFrame, ret_col: str, sessions: list[str]) -> dict:
    """매니페스트 phase_2 5개 기준 측정."""
    if "session_date" not in df.columns:
        return {}
    # daily top-2 picks
    picks = []
    for _, g in df.groupby("session_date"):
        picks.append(g.nlargest(2, "ensemble_tp_prob"))
    tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
    if len(tdf) == 0 or ret_col not in tdf.columns:
        return {"daily_top_2": None}

    # phase_2 기준 평가
    hit_rate = float((tdf[ret_col] > 0).mean())
    avg_net = float(tdf[ret_col].mean())
    cum = tdf[ret_col].cumsum()
    dd = float((cum - cum.cummax()).min())
    winners = tdf.loc[tdf[ret_col] > 0, "ticker"].nunique() if "ticker" in tdf.columns else 0

    # walk-forward 7-session
    per_session = []
    for sd, g in df.groupby("session_date"):
        top = g.nlargest(2, "ensemble_tp_prob")
        if len(top) and ret_col in top.columns:
            per_session.append((str(sd), float(top[ret_col].mean())))
    per_session.sort(key=lambda t: t[0])
    windows = []
    for i in range(0, len(per_session) - 6):
        chunk = per_session[i:i + 7]
        windows.append(sum(v for _, v in chunk) / 7.0)
    pos_w = sum(1 for w in windows if w > 0) if windows else 0
    avg_w = (sum(windows) / len(windows)) if windows else None

    criteria = {
        "daily_top_2_hit_rate": {"value": round(hit_rate, 4), "target": 0.40, "pass": hit_rate >= 0.40},
        "daily_top_2_avg_net":  {"value": round(avg_net, 4), "target": 0.0, "pass": avg_net >= 0.0},
        "walk_forward_7s":      {"windows": len(windows), "positive_windows": pos_w,
                                  "avg": round(avg_w, 4) if avg_w is not None else None,
                                  "pass": (avg_w is not None and avg_w > 0)},
        "max_drawdown_pct":     {"value": round(dd, 4), "target": -5.0, "pass": dd >= -5.0},
        "unique_winning_tickers": {"value": int(winners), "target": 5, "pass": winners >= 5},
    }
    passes = sum(1 for c in criteria.values() if c.get("pass"))
    return {
        "criteria": criteria,
        "passes": passes,
        "total": len(criteria),
        "phase_2_pass": passes == len(criteria),
        "daily_top_2_n": len(tdf),
    }


def _load_forward_events(pattern: str, cost_pct: float) -> pd.DataFrame:
    paths = sorted(glob.glob(pattern))
    paths = [p for p in paths if not p.endswith("_today.csv")]
    if not paths:
        return pd.DataFrame()
    dfs = [pd.read_csv(p) for p in paths]
    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    if "label_int" in df.columns:
        df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
        df = df[df["label_int"].isin([-1, 0, 1])].copy()
    # net 비용 차감
    if "return_pct" in df.columns:
        df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce").fillna(0.0)
        if "gross_return_pct" in df.columns:
            df["gross_return_pct"] = pd.to_numeric(df["gross_return_pct"], errors="coerce").fillna(0.0)
            df["net_return_pct"] = df["gross_return_pct"] - cost_pct
        else:
            df["net_return_pct"] = df["return_pct"] - cost_pct
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rf", required=True)
    p.add_argument("--dl", required=True)
    p.add_argument("--ae", default=None)
    p.add_argument("--train-dataset", required=True,
                   help="ML 학습에 사용한 dataset (in-distribution OOS 평가용)")
    p.add_argument("--forward-events", default="db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv")
    p.add_argument("--oos-date-count", type=int, default=14)
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--rf-weight", type=float, default=0.5)
    p.add_argument("--dl-weight", type=float, default=0.5)
    p.add_argument("--ae-penalty", type=float, default=0.1,
                   help="AE outlier 에 확률 페널티 (0=영향 없음, 0.1=conservative)")
    p.add_argument("--cost-pct", type=float, default=0.4)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    # Resolve RF (glob 패턴이면 첫 매칭)
    rf_paths = sorted(glob.glob(args.rf))
    if not rf_paths:
        print(f"[error] no RF match: {args.rf}", file=sys.stderr)
        return 2
    rf_obj = joblib.load(rf_paths[0])
    rf_model, rf_cols = _resolve(rf_obj)
    print(f"RF: {Path(rf_paths[0]).name} ({len(rf_cols) if rf_cols else 0} cols)")

    dl_obj = joblib.load(args.dl)
    dl_model, dl_cols = _resolve(dl_obj)
    print(f"DL: {Path(args.dl).name} ({len(dl_cols) if dl_cols else 0} cols)")

    ae_obj = None
    if args.ae:
        ae_obj = joblib.load(args.ae)
        print(f"AE: {Path(args.ae).name} (threshold={ae_obj['ae'].anomaly_threshold:.6f})")

    # ── 1) IN-DISTRIBUTION OOS (training dataset 의 마지막 14세션)
    print(f"\n{'='*70}\n=== 1) IN-DISTRIBUTION OOS (training dataset 14세션) ===\n{'='*70}")
    df_train = pd.read_csv(args.train_dataset)
    df_train["fast_score"] = pd.to_numeric(df_train.get("fast_score", 0), errors="coerce").fillna(0.0)
    df_train["strategies_passed"] = pd.to_numeric(df_train.get("strategies_passed", 0), errors="coerce").fillna(0.0)
    df_train["session_date"] = df_train["session_date"].astype(str)
    df_train = df_train[(df_train["fast_score"] >= args.min_fast_score)
                        & (df_train["strategies_passed"] >= args.min_strategies_passed)]
    df_train["label_int"] = pd.to_numeric(df_train["label_int"], errors="coerce")
    df_train = df_train[df_train["label_int"].isin([0, 1, -1])].copy()
    # OOS split
    dates = sorted(df_train["session_date"].unique())
    test_dates = dates[-args.oos_date_count:]
    train_oos = df_train[df_train["session_date"].isin(test_dates)].copy()
    if "net_return_pct" not in train_oos.columns:
        if "gross_return_pct" in train_oos.columns:
            train_oos["net_return_pct"] = pd.to_numeric(train_oos["gross_return_pct"], errors="coerce") - args.cost_pct
        else:
            train_oos["net_return_pct"] = pd.to_numeric(train_oos["return_pct"], errors="coerce") - args.cost_pct
    print(f"  test rows: {len(train_oos)}, sessions: {len(test_dates)}, tickers: {train_oos['ticker'].nunique() if 'ticker' in train_oos else 0}")

    train_oos_scored = _score_dataset(
        train_oos, rf_model, rf_cols, dl_model, dl_cols, ae_obj,
        args.rf_weight, args.dl_weight, args.ae_penalty,
    )
    phase2_in = _evaluate_phase_2(train_oos_scored, "net_return_pct", test_dates)
    print(f"  phase_2: {phase2_in.get('passes')}/{phase2_in.get('total')} criteria passed")
    for k, v in phase2_in.get("criteria", {}).items():
        print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    # ── 2) FORWARD PAPER (실제 라이브 분포)
    print(f"\n{'='*70}\n=== 2) FORWARD PAPER (실 라이브 분포) ===\n{'='*70}")
    fp = _load_forward_events(args.forward_events, args.cost_pct)
    if len(fp) == 0:
        print("  [warn] no forward events", file=sys.stderr)
        phase2_fp = {}
    else:
        if "fast_score" in fp.columns:
            fp["fast_score"] = pd.to_numeric(fp["fast_score"], errors="coerce").fillna(0.0)
        if "strategies_passed" in fp.columns:
            fp["strategies_passed"] = pd.to_numeric(fp["strategies_passed"], errors="coerce").fillna(0.0)
        # 같은 후보 필터 적용해서 학습-운영 분포 비교
        fp_filtered = fp[(fp.get("fast_score", 0) >= args.min_fast_score)
                         & (fp.get("strategies_passed", 0) >= args.min_strategies_passed)].copy()
        print(f"  full forward events: {len(fp)}, after filter (fs≥{args.min_fast_score} strat≥{args.min_strategies_passed}): {len(fp_filtered)}")
        sessions_fp = sorted(fp_filtered.get("session_date", pd.Series([])).astype(str).unique())
        fp_scored = _score_dataset(
            fp_filtered, rf_model, rf_cols, dl_model, dl_cols, ae_obj,
            args.rf_weight, args.dl_weight, args.ae_penalty,
        )
        phase2_fp = _evaluate_phase_2(fp_scored, "net_return_pct", sessions_fp)
        print(f"  sessions: {len(sessions_fp)}, tickers: {fp_filtered['ticker'].nunique() if 'ticker' in fp_filtered else 0}")
        print(f"  phase_2: {phase2_fp.get('passes')}/{phase2_fp.get('total')} criteria passed")
        for k, v in phase2_fp.get("criteria", {}).items():
            print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    # ── 3) FORWARD PAPER + AE filter 강하게 (penalty=1.0 ≈ outlier 제외)
    print(f"\n{'='*70}\n=== 3) FORWARD PAPER + AE strict filter (outlier 제외) ===\n{'='*70}")
    phase2_fp_strict = {}
    if len(fp) > 0:
        fp_strict_scored = _score_dataset(
            fp_filtered, rf_model, rf_cols, dl_model, dl_cols, ae_obj,
            args.rf_weight, args.dl_weight, ae_penalty=1.0,
        )
        phase2_fp_strict = _evaluate_phase_2(fp_strict_scored, "net_return_pct", sessions_fp)
        print(f"  phase_2: {phase2_fp_strict.get('passes')}/{phase2_fp_strict.get('total')} criteria passed")
        for k, v in phase2_fp_strict.get("criteria", {}).items():
            print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "rf_model": rf_paths[0],
            "dl_model": args.dl,
            "ae_model": args.ae,
            "rf_weight": args.rf_weight,
            "dl_weight": args.dl_weight,
            "ae_penalty": args.ae_penalty,
            "cost_pct": args.cost_pct,
            "min_fast_score": args.min_fast_score,
            "min_strategies_passed": args.min_strategies_passed,
            "in_distribution_oos": phase2_in,
            "forward_paper": phase2_fp,
            "forward_paper_strict_ae": phase2_fp_strict,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
