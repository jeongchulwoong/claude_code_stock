"""
scripts/train_daytrade_dl.py — Deep MLP + AutoEncoder 학습 (paper-only research).

매니페스트 phase_3 사양 준수:
  - artifact path = `models/research/dl_<name>.joblib`
  - paper journal 별도 (운영 RF entry decision 영향 0)
  - readiness checker 가 자동 인식 (research_candidates 에 등록 시)

사용:
  python scripts/train_daytrade_dl.py \\
    --dataset db/ml/<event_dataset>.csv \\
    --min-fast-score 85 --min-strategies-passed 4 \\
    --oos-date-count 14 --calibration sigmoid \\
    --deep-mlp-out  models/research/dl_deep_mlp_v1.joblib \\
    --autoencoder-out models/research/dl_autoencoder_v1.joblib \\
    --report-out reports/ml/research/dl_train_v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score, brier_score_loss, roc_auc_score,
)

from core.daytrade_dl_autoencoder import (  # noqa: E402
    AutoEncoderConfig, fit_autoencoder,
)
from core.daytrade_dl_deep_mlp import (  # noqa: E402
    DeepMLPConfig, build_deep_mlp_pipeline,
)
from core.daytrade_ml_schema import MODEL_FEATURE_COLUMNS, NON_MODEL_COLUMNS  # noqa: E402

# 학습 dataset 에서 라벨 / metadata 컬럼 (피처로 쓰면 leakage)
LEAKY_COLUMNS = {
    "label_int", "label_hit", "return_pct", "gross_return_pct", "net_return_pct",
    "event_barrier_upper_pct", "event_barrier_lower_pct", "event_cost_pct",
    "exit_index", "exit_time", "exit_price", "holding_minutes",
    "timestamp", "session_date", "ticker", "source", "timezone", "adjusted",
    "event_reason", "ma120_then",
}


def _select_feature_columns(df: pd.DataFrame, feature_mode: str) -> list[str]:
    if feature_mode == "default":
        cols = [c for c in MODEL_FEATURE_COLUMNS
                if c in df.columns and c not in NON_MODEL_COLUMNS]
    else:   # auto — numeric 만, leaky 제거
        numeric = df.select_dtypes(include=[np.number]).columns.tolist()
        cols = [c for c in numeric if c not in LEAKY_COLUMNS and c not in NON_MODEL_COLUMNS]
    return cols


def _decided_only(df: pd.DataFrame) -> pd.DataFrame:
    df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
    return df[df["label_int"].isin([0, 1])].copy()


def _split_oos(df: pd.DataFrame, oos_dates: int, embargo_minutes: int) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    df["session_date"] = df["session_date"].astype(str)
    dates = sorted(df["session_date"].unique())
    if len(dates) <= oos_dates:
        return df.iloc[0:0], df, dates
    test_dates = dates[-oos_dates:]
    train = df[~df["session_date"].isin(test_dates)].copy()
    test = df[df["session_date"].isin(test_dates)].copy()
    return train, test, test_dates


def _apply_candidate_filter(df: pd.DataFrame, *, min_fast: float, min_strat: int) -> pd.DataFrame:
    if "fast_score" in df.columns and min_fast > 0:
        df = df[pd.to_numeric(df["fast_score"], errors="coerce").fillna(0) >= min_fast]
    if "strategies_passed" in df.columns and min_strat > 0:
        df = df[pd.to_numeric(df["strategies_passed"], errors="coerce").fillna(0) >= min_strat]
    return df


def _compute_top_n(test: pd.DataFrame, probs: np.ndarray, n_list: tuple[int, ...]) -> dict:
    test = test.copy().reset_index(drop=True)
    test["tp_prob"] = probs
    test["label_int"] = pd.to_numeric(test["label_int"], errors="coerce")
    out = {}
    for n in n_list:
        picks = []
        for _, g in test.groupby("session_date"):
            top = g.nlargest(n, "tp_prob")
            picks.append(top)
        tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
        if "net_return_pct" in tdf.columns:
            ret_col = "net_return_pct"
        elif "return_pct" in tdf.columns:
            ret_col = "return_pct"
        else:
            ret_col = None
        if len(tdf) == 0 or ret_col is None:
            out[f"daily_top_{n}"] = {"n": 0, "precision": None, "avg_net": None}
            continue
        precision = float((tdf["label_int"] == 1).mean())
        avg = float(pd.to_numeric(tdf[ret_col], errors="coerce").mean())
        out[f"daily_top_{n}"] = {"n": int(len(tdf)), "precision": precision, "avg_net": avg}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--feature-mode", choices=["default", "auto"], default="default")
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--oos-date-count", type=int, default=14)
    p.add_argument("--embargo-minutes", type=int, default=30)
    p.add_argument("--calibration", choices=["sigmoid", "isotonic", "none"], default="sigmoid")
    p.add_argument("--deep-mlp-out", default="models/research/dl_deep_mlp_v1.joblib")
    p.add_argument("--autoencoder-out", default="models/research/dl_autoencoder_v1.joblib")
    p.add_argument("--report-out", default="reports/ml/research/dl_train.json")
    p.add_argument("--mlp-hidden", default="256,128,64,32")
    p.add_argument("--ae-bottleneck", type=int, default=16)
    args = p.parse_args(argv)

    df = pd.read_csv(args.dataset)
    df = _apply_candidate_filter(df, min_fast=args.min_fast_score, min_strat=args.min_strategies_passed)
    decided = _decided_only(df)
    train_df, test_df, test_dates = _split_oos(decided, args.oos_date_count, args.embargo_minutes)
    feature_cols = _select_feature_columns(train_df, args.feature_mode)
    print(f"dataset: {len(df)} rows | decided: {len(decided)} | train: {len(train_df)} | test: {len(test_df)}")
    print(f"OOS sessions ({len(test_dates)}): {test_dates[:3]} ... {test_dates[-3:] if len(test_dates) >= 3 else test_dates}")
    print(f"feature_columns ({len(feature_cols)}): {feature_cols[:10]}...")

    if len(train_df) < 200:
        print("[error] train set too small", file=sys.stderr)
        return 2

    X_train = train_df[feature_cols].astype(float).fillna(0.0).values
    y_train = train_df["label_int"].astype(int).values
    X_test  = test_df[feature_cols].astype(float).fillna(0.0).values
    y_test  = test_df["label_int"].astype(int).values

    # ── Deep MLP ──
    cfg = DeepMLPConfig(
        hidden_layer_sizes=tuple(int(x) for x in args.mlp_hidden.split(",")),
        calibration_method=(args.calibration if args.calibration != "none" else None),
    )
    print(f"\n[Deep MLP] training (hidden={cfg.hidden_layer_sizes}, cal={cfg.calibration_method}) ...")
    pipeline = build_deep_mlp_pipeline(cfg)
    pipeline.fit(X_train, y_train)
    proba = pipeline.predict_proba(X_test)
    tp_idx = list(pipeline.classes_).index(1) if 1 in list(pipeline.classes_) else 1
    tp_prob = proba[:, tp_idx]
    mlp_metrics = {
        "roc_auc": float(roc_auc_score(y_test, tp_prob)) if len(set(y_test)) > 1 else None,
        "pr_auc":  float(average_precision_score(y_test, tp_prob)) if len(set(y_test)) > 1 else None,
        "brier":   float(brier_score_loss(y_test == 1, tp_prob)) if len(set(y_test)) > 1 else None,
    }
    mlp_topn = _compute_top_n(test_df, tp_prob, (1, 2, 3, 5))
    print(f"[Deep MLP] roc={mlp_metrics['roc_auc']:.4f} pr={mlp_metrics['pr_auc']:.4f}")
    for k, v in mlp_topn.items():
        print(f"  {k}: n={v['n']} precision={v['precision']!s} avg_net={v['avg_net']!s}")

    Path(args.deep_mlp_out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": pipeline,
        "feature_columns": feature_cols,
        "model_type": "deep_mlp_sklearn",
        "model_version": f"daytrade_v1_deep_mlp_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "feature_version": "daytrade_v1",
        "class_labels": [0, 1],
    }, args.deep_mlp_out)
    print(f"[Deep MLP] saved -> {args.deep_mlp_out}")

    # ── AutoEncoder ──
    ae_cfg = AutoEncoderConfig(bottleneck_size=args.ae_bottleneck)
    print(f"\n[AutoEncoder] training (bottleneck={ae_cfg.bottleneck_size}) ...")
    # AE 는 라벨 무관 — train + test 합쳐서 학습 (anomaly detection 정의상 OK,
    # threshold 만 train 분포로). 단 reconstruction error 평가는 test 에서.
    ae = fit_autoencoder(X_train, feature_cols, ae_cfg)
    # test 의 reconstruction error — scaled space 에서 계산 (학습과 동일 정의)
    scaler = ae.pipeline.named_steps["scaler"]
    mlp_ae = ae.pipeline.named_steps["ae"]
    X_test_scaled = scaler.transform(X_test)
    X_test_recon = mlp_ae.predict(X_test_scaled)
    test_errors = np.mean((X_test_scaled - X_test_recon) ** 2, axis=1)
    test_outlier_rate = float((test_errors > ae.anomaly_threshold).mean())
    print(f"[AutoEncoder] threshold={ae.anomaly_threshold:.6f}, test outlier rate={test_outlier_rate:.4f}")
    print(f"  train error stats: {ae.train_error_stats}")

    Path(args.autoencoder_out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "ae": ae,
        "feature_columns": feature_cols,
        "model_type": "autoencoder_sklearn",
        "model_version": f"daytrade_v1_ae_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "feature_version": "daytrade_v1",
    }, args.autoencoder_out)
    print(f"[AutoEncoder] saved -> {args.autoencoder_out}")

    # ── 보고서 ──
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "feature_columns": feature_cols,
        "rows_train": len(train_df),
        "rows_test": len(test_df),
        "test_sessions": test_dates,
        "deep_mlp": {
            "model_path": args.deep_mlp_out,
            "config": {"hidden": list(cfg.hidden_layer_sizes), "calibration": cfg.calibration_method},
            "metrics": mlp_metrics,
            "daily_top_n": mlp_topn,
        },
        "autoencoder": {
            "model_path": args.autoencoder_out,
            "config": {"bottleneck": ae_cfg.bottleneck_size, "anomaly_percentile": ae_cfg.anomaly_percentile},
            "anomaly_threshold": ae.anomaly_threshold,
            "train_error_stats": ae.train_error_stats,
            "test_outlier_rate": test_outlier_rate,
        },
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nreport -> {args.report_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
