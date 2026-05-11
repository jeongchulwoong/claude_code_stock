"""
scripts/full_ensemble_backtest.py — RF + MLP + LSTM + AE 4-model 앙상블 백테스트.

평가 지점:
  1. In-distribution OOS (training dataset 마지막 14세션)
  2. Forward paper (실 라이브 분포, 1242건 → 필터 후)
  3. Forward paper + AE 동적 필터 (forward 분포에 재학습한 AE)

매니페스트 phase_2 5개 기준 + 자본 영향 시뮬레이션.

사용:
  python scripts/full_ensemble_backtest.py \\
    --rf 'models/research/iterZB_*.joblib' \\
    --mlp models/research/dl_deep_mlp_v1.joblib \\
    --lstm models/research/dl_lstm_v1_meta.joblib \\
    --ae models/research/dl_autoencoder_v1.joblib \\
    --train-dataset db/ml/daytrade_event_dataset_atr15x06_cost04_20260511.csv \\
    --forward-events 'db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv' \\
    --minute-db db/kiwoom_minute_bars.db \\
    --weights '0.5,0.3,0.2' \\
    --json-out reports/ml/research/full_ensemble.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.daytrade_dl_autoencoder import AutoEncoderConfig, fit_autoencoder  # noqa: E402
from core.daytrade_dl_lstm import (  # noqa: E402
    LSTMBinaryClassifier, build_sequence_features,
)


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


def _load_bars(db_path: Path, df: pd.DataFrame) -> dict:
    """모든 (ticker, session_date) 그룹의 분봉 로드 → dict."""
    conn = sqlite3.connect(str(db_path))
    keys = list({(str(r["ticker"]), str(r["session_date"])) for _, r in df.iterrows()})
    lookup: dict = {}
    chunk = 200
    for i in range(0, len(keys), chunk):
        sub = keys[i:i + chunk]
        if not sub:
            continue
        placeholders = " OR ".join(["(ticker=? AND session_date=?)"] * len(sub))
        params = []
        for t, s in sub:
            params.extend([t, s])
        cur = conn.execute(
            f"SELECT ticker, session_date, minute_offset, open, high, low, close, volume, "
            f"ma120, atr_pct, rsi FROM minute_bars "
            f"WHERE timeframe='1m' AND ({placeholders}) ORDER BY minute_offset ASC",
            params,
        )
        for row in cur:
            t, s = row[0], row[1]
            lookup.setdefault((t, s), []).append({
                "minute_offset": row[2], "open": row[3], "high": row[4],
                "low": row[5], "close": row[6], "volume": row[7],
                "ma120": row[8], "atr_pct": row[9], "rsi": row[10],
            })
    conn.close()
    return lookup


def _build_sequences(df: pd.DataFrame, lookup: dict, seq_len: int, feature_keys: tuple) -> tuple[np.ndarray, list[int]]:
    X = np.zeros((len(df), seq_len, len(feature_keys)), dtype=float)
    valid = []
    for i, (_, row) in enumerate(df.iterrows()):
        key = (str(row["ticker"]), str(row["session_date"]))
        bars = lookup.get(key, [])
        entry_off = int(pd.to_numeric(row.get("minutes_since_open", 0), errors="coerce") or 0)
        past = [b for b in bars if int(b["minute_offset"]) < entry_off]
        if len(past) < 10:
            continue
        X[i] = build_sequence_features(past, seq_len=seq_len, feature_keys=feature_keys)
        valid.append(i)
    return X, valid


def _score_ml_dl_ensemble(
    df: pd.DataFrame,
    rf_model, rf_cols,
    mlp_model, mlp_cols,
    lstm_model, lstm_feature_keys, lstm_seq_len, bars_lookup,
    ae_obj,
    weights: tuple[float, float, float],
    ae_penalty: float,
) -> pd.DataFrame:
    """4-model 앙상블 점수 → df 에 ensemble_tp_prob 추가."""
    df = df.reset_index(drop=True).copy()
    n = len(df)

    # 1) RF
    rf_p = rf_model.predict_proba(_build_X(df, rf_cols))[:, _tp_idx(rf_model)]
    # 2) MLP
    mlp_p = mlp_model.predict_proba(_build_X(df, mlp_cols))[:, _tp_idx(mlp_model)]
    # 3) LSTM (sequence)
    X_seq, valid_idx = _build_sequences(df, bars_lookup, lstm_seq_len, lstm_feature_keys)
    lstm_p = np.full(n, 0.5, dtype=float)   # 시퀀스 없으면 neutral
    if len(valid_idx) > 0:
        proba = lstm_model.predict_proba(X_seq[valid_idx])
        if proba.shape[1] >= 2:
            for k, i in enumerate(valid_idx):
                lstm_p[i] = float(proba[k, 1])
    # 4) AE filter (advisory)
    is_outlier = np.zeros(n, dtype=bool)
    ae_err = np.zeros(n, dtype=float)
    if ae_obj is not None:
        ae = ae_obj["ae"] if isinstance(ae_obj, dict) else ae_obj
        scaler = ae.pipeline.named_steps["scaler"]
        mlp_ae = ae.pipeline.named_steps["ae"]
        X_ae = _build_X(df, ae.feature_columns)
        X_scaled = scaler.transform(X_ae)
        X_recon = mlp_ae.predict(X_scaled)
        ae_err = np.mean((X_scaled - X_recon) ** 2, axis=1)
        is_outlier = ae_err > ae.anomaly_threshold

    w = np.array(weights, dtype=float)
    w = w / max(w.sum(), 1e-9)
    ensemble = w[0] * rf_p + w[1] * mlp_p + w[2] * lstm_p
    if ae_penalty > 0:
        ensemble = np.where(is_outlier, np.maximum(0.0, ensemble - ae_penalty), ensemble)

    df["rf_tp_prob"]   = rf_p
    df["mlp_tp_prob"]  = mlp_p
    df["lstm_tp_prob"] = lstm_p
    df["ae_recon_err"] = ae_err
    df["ae_is_outlier"] = is_outlier.astype(int)
    df["ensemble_tp_prob"] = ensemble
    return df


def _evaluate_phase_2(df: pd.DataFrame, ret_col: str) -> dict:
    if "session_date" not in df.columns or ret_col not in df.columns:
        return {"error": "missing columns"}
    picks = []
    for _, g in df.groupby("session_date"):
        picks.append(g.nlargest(2, "ensemble_tp_prob"))
    tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
    if len(tdf) == 0:
        return {"daily_top_2": None}
    hit = float((tdf[ret_col] > 0).mean())
    avg = float(tdf[ret_col].mean())
    cum = tdf[ret_col].cumsum()
    dd = float((cum - cum.cummax()).min())
    winners = int(tdf.loc[tdf[ret_col] > 0, "ticker"].nunique()) if "ticker" in tdf.columns else 0
    # walk-forward 7s
    per_s = []
    for sd, g in df.groupby("session_date"):
        top = g.nlargest(2, "ensemble_tp_prob")
        if len(top):
            per_s.append((str(sd), float(top[ret_col].mean())))
    per_s.sort(key=lambda x: x[0])
    win7 = []
    for i in range(0, len(per_s) - 6):
        win7.append(sum(v for _, v in per_s[i:i + 7]) / 7.0)
    pos_w = sum(1 for w in win7 if w > 0) if win7 else 0
    avg_w = sum(win7) / len(win7) if win7 else None
    criteria = {
        "daily_top_2_hit_rate": {"value": round(hit, 4), "target": 0.40, "pass": hit >= 0.40},
        "daily_top_2_avg_net":  {"value": round(avg, 4), "target": 0.0, "pass": avg >= 0.0},
        "walk_forward_7s":      {"windows": len(win7), "positive_windows": pos_w,
                                  "avg": round(avg_w, 4) if avg_w is not None else None,
                                  "pass": (avg_w is not None and avg_w > 0)},
        "max_drawdown_pct":     {"value": round(dd, 4), "target": -5.0, "pass": dd >= -5.0},
        "unique_winning_tickers": {"value": winners, "target": 5, "pass": winners >= 5},
    }
    passes = sum(1 for v in criteria.values() if v.get("pass"))
    return {
        "criteria": criteria,
        "passes": passes,
        "total": 5,
        "phase_2_pass": passes == 5,
        "daily_top_2_n": len(tdf),
        "sessions": int(df["session_date"].nunique()),
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
    if "return_pct" in df.columns:
        df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce").fillna(0.0)
        if "gross_return_pct" in df.columns:
            df["gross_return_pct"] = pd.to_numeric(df["gross_return_pct"], errors="coerce").fillna(0.0)
            df["net_return_pct"] = df["gross_return_pct"] - cost_pct
        else:
            df["net_return_pct"] = df["return_pct"] - cost_pct
    return df


def _load_lstm(meta_path: str) -> tuple:
    import torch
    meta = joblib.load(meta_path)
    pt_path = meta["model_path"]
    state = torch.load(pt_path, weights_only=False)
    cfg = meta["cfg"]
    model = LSTMBinaryClassifier(cfg)
    model.load_from_state(state)
    return model, tuple(meta["feature_keys"]), meta["seq_len"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rf", required=True)
    p.add_argument("--mlp", required=True)
    p.add_argument("--lstm", required=True)
    p.add_argument("--ae", default=None)
    p.add_argument("--train-dataset", required=True)
    p.add_argument("--forward-events", default="db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv")
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--oos-date-count", type=int, default=14)
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--weights", default="0.5,0.3,0.2",
                   help="RF,MLP,LSTM weights (comma-separated)")
    p.add_argument("--ae-penalty", type=float, default=0.1)
    p.add_argument("--cost-pct", type=float, default=0.4)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    weights = tuple(float(x) for x in args.weights.split(","))

    # Load models
    rf_paths = sorted(glob.glob(args.rf))
    if not rf_paths:
        print(f"[error] RF not found: {args.rf}", file=sys.stderr)
        return 2
    rf_obj = joblib.load(rf_paths[0])
    rf_model, rf_cols = _resolve(rf_obj)
    mlp_obj = joblib.load(args.mlp)
    mlp_model, mlp_cols = _resolve(mlp_obj)
    lstm_model, lstm_feature_keys, lstm_seq_len = _load_lstm(args.lstm)
    ae_obj = joblib.load(args.ae) if args.ae else None

    print(f"RF:   {Path(rf_paths[0]).name} ({len(rf_cols)} cols)")
    print(f"MLP:  {Path(args.mlp).name} ({len(mlp_cols)} cols)")
    print(f"LSTM: {Path(args.lstm).name} (seq_len={lstm_seq_len}, feat_dim={len(lstm_feature_keys)})")
    print(f"AE:   {Path(args.ae).name if args.ae else 'none'}")
    print(f"Weights (RF/MLP/LSTM): {weights}, AE penalty: {args.ae_penalty}, cost: {args.cost_pct}")

    # ── 1) IN-DISTRIBUTION OOS
    print(f"\n{'='*70}\n=== 1) IN-DISTRIBUTION OOS ===\n{'='*70}")
    df_tr = pd.read_csv(args.train_dataset)
    df_tr["session_date"] = df_tr["session_date"].astype(str)
    df_tr["ticker"] = df_tr["ticker"].astype(str)
    df_tr["fast_score"] = pd.to_numeric(df_tr.get("fast_score", 0), errors="coerce").fillna(0)
    df_tr["strategies_passed"] = pd.to_numeric(df_tr.get("strategies_passed", 0), errors="coerce").fillna(0)
    df_tr["label_int"] = pd.to_numeric(df_tr["label_int"], errors="coerce")
    df_tr = df_tr[(df_tr["fast_score"] >= args.min_fast_score)
                  & (df_tr["strategies_passed"] >= args.min_strategies_passed)
                  & (df_tr["label_int"].isin([0, 1, -1]))].copy()
    dates = sorted(df_tr["session_date"].unique())
    test_dates = dates[-args.oos_date_count:]
    tr_test = df_tr[df_tr["session_date"].isin(test_dates)].reset_index(drop=True)
    if "net_return_pct" not in tr_test.columns:
        if "gross_return_pct" in tr_test.columns:
            tr_test["net_return_pct"] = pd.to_numeric(tr_test["gross_return_pct"], errors="coerce") - args.cost_pct
        else:
            tr_test["net_return_pct"] = pd.to_numeric(tr_test["return_pct"], errors="coerce") - args.cost_pct
    print(f"  test rows: {len(tr_test)}, sessions: {len(test_dates)}, tickers: {tr_test['ticker'].nunique()}")
    bars_tr = _load_bars(Path(args.minute_db), tr_test)
    print(f"  loaded {len(bars_tr)} minute-bar groups")
    scored_tr = _score_ml_dl_ensemble(
        tr_test, rf_model, rf_cols, mlp_model, mlp_cols,
        lstm_model, lstm_feature_keys, lstm_seq_len, bars_tr,
        ae_obj, weights, args.ae_penalty,
    )
    phase2_in = _evaluate_phase_2(scored_tr, "net_return_pct")
    print(f"  phase_2: {phase2_in.get('passes')}/{phase2_in.get('total')} criteria passed")
    for k, v in phase2_in.get("criteria", {}).items():
        print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    # ── 2) FORWARD PAPER full
    print(f"\n{'='*70}\n=== 2) FORWARD PAPER (실 라이브 분포) ===\n{'='*70}")
    fp = _load_forward_events(args.forward_events, args.cost_pct)
    if "fast_score" in fp.columns:
        fp["fast_score"] = pd.to_numeric(fp["fast_score"], errors="coerce").fillna(0)
        fp["strategies_passed"] = pd.to_numeric(fp.get("strategies_passed", 0), errors="coerce").fillna(0)
    fp_f = fp[(fp["fast_score"] >= args.min_fast_score)
              & (fp["strategies_passed"] >= args.min_strategies_passed)].reset_index(drop=True)
    print(f"  full forward: {len(fp)}, after filter: {len(fp_f)}, sessions: {fp_f['session_date'].nunique()}, tickers: {fp_f['ticker'].nunique()}")
    if len(fp_f) == 0:
        phase2_fp = {"error": "no forward events"}
    else:
        bars_fp = _load_bars(Path(args.minute_db), fp_f)
        print(f"  loaded {len(bars_fp)} minute-bar groups")
        scored_fp = _score_ml_dl_ensemble(
            fp_f, rf_model, rf_cols, mlp_model, mlp_cols,
            lstm_model, lstm_feature_keys, lstm_seq_len, bars_fp,
            ae_obj, weights, args.ae_penalty,
        )
        phase2_fp = _evaluate_phase_2(scored_fp, "net_return_pct")
        print(f"  phase_2: {phase2_fp.get('passes')}/{phase2_fp.get('total')} criteria passed")
        for k, v in phase2_fp.get("criteria", {}).items():
            print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    # ── 3) FORWARD PAPER + FORWARD-tuned AE
    print(f"\n{'='*70}\n=== 3) FORWARD PAPER + AE trained on forward dist ===\n{'='*70}")
    phase2_fpae = {}
    if len(fp_f) > 0 and ae_obj is not None:
        # Retrain AE on forward distribution to find WITHIN-forward outliers
        ae_orig = ae_obj["ae"] if isinstance(ae_obj, dict) else ae_obj
        feat_cols_ae = ae_orig.feature_columns
        X_fp_ae = _build_X(fp_f, feat_cols_ae)
        ae_fp = fit_autoencoder(X_fp_ae, feat_cols_ae, AutoEncoderConfig(bottleneck_size=12, max_iter=100))
        ae_fp_wrapper = {"ae": ae_fp, "feature_columns": feat_cols_ae}
        scored_fp_ae = _score_ml_dl_ensemble(
            fp_f, rf_model, rf_cols, mlp_model, mlp_cols,
            lstm_model, lstm_feature_keys, lstm_seq_len, bars_fp,
            ae_fp_wrapper, weights, args.ae_penalty,
        )
        phase2_fpae = _evaluate_phase_2(scored_fp_ae, "net_return_pct")
        ae_outlier_rate = float(scored_fp_ae["ae_is_outlier"].mean())
        print(f"  forward-AE outlier rate: {ae_outlier_rate:.4f}")
        print(f"  phase_2: {phase2_fpae.get('passes')}/{phase2_fpae.get('total')} criteria passed")
        for k, v in phase2_fpae.get("criteria", {}).items():
            print(f"    {'OK ' if v.get('pass') else 'MISS'} {k}: {v}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "models": {
                "rf": rf_paths[0],
                "mlp": args.mlp,
                "lstm": args.lstm,
                "ae": args.ae,
            },
            "weights_rf_mlp_lstm": list(weights),
            "ae_penalty": args.ae_penalty,
            "cost_pct": args.cost_pct,
            "in_distribution_oos": phase2_in,
            "forward_paper": phase2_fp,
            "forward_paper_with_fp_ae": phase2_fpae,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
