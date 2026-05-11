"""
scripts/train_daytrade_lstm.py — LSTM 학습 (PyTorch, CPU).

각 event row 별로:
  - minute_bars DB 에서 entry timestamp 이전 N=60분 OHLCV+TA 시퀀스 가져옴
  - 시퀀스 + label_int (TP-first) 로 LSTM 학습

매니페스트 phase_3 사양:
  - paper-only
  - artifact: models/research/dl_lstm_v1.pt + dl_lstm_v1_meta.joblib

사용:
  python scripts/train_daytrade_lstm.py \\
    --dataset db/ml/<event_dataset_with_atr_labels>.csv \\
    --minute-db db/kiwoom_minute_bars.db \\
    --min-fast-score 85 --min-strategies-passed 4 \\
    --oos-date-count 14 --seq-len 60 --epochs 25 \\
    --out-base models/research/dl_lstm_v1
"""

from __future__ import annotations

import argparse
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
from sklearn.metrics import (  # noqa: E402
    average_precision_score, roc_auc_score,
)

from core.daytrade_dl_lstm import (  # noqa: E402
    LSTMBinaryClassifier, LSTMConfig, build_sequence_features,
)

FEATURE_KEYS = ("close", "high", "low", "volume", "ma120", "atr_pct", "rsi")


def _load_bars_lookup(db_path: Path, df: pd.DataFrame) -> dict:
    """모든 (ticker, session_date) 그룹의 분봉 데이터를 한 번에 로드 → dict."""
    conn = sqlite3.connect(str(db_path))
    keys = list({(str(r["ticker"]), str(r["session_date"])) for _, r in df.iterrows()})
    lookup: dict[tuple[str, str], list[dict]] = {}
    print(f"  loading bars for {len(keys)} (ticker,session) pairs...")
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
            f"WHERE timeframe='1m' AND ({placeholders}) "
            f"ORDER BY minute_offset ASC",
            params,
        )
        for row in cur:
            t, s = row[0], row[1]
            bar = {
                "minute_offset": row[2], "open": row[3], "high": row[4],
                "low": row[5], "close": row[6], "volume": row[7],
                "ma120": row[8], "atr_pct": row[9], "rsi": row[10],
            }
            lookup.setdefault((t, s), []).append(bar)
    conn.close()
    return lookup


def _build_sequences(df: pd.DataFrame, lookup: dict, seq_len: int) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """각 row → entry 이전 seq_len 분봉 시퀀스."""
    X = np.zeros((len(df), seq_len, len(FEATURE_KEYS)), dtype=float)
    y = np.zeros(len(df), dtype=int)
    valid_idx = []
    n_valid = 0
    for i, (_, row) in enumerate(df.iterrows()):
        key = (str(row["ticker"]), str(row["session_date"]))
        bars = lookup.get(key, [])
        entry_off = int(row["minutes_since_open"])
        # entry 이전 bars 만 (look-ahead 방지)
        past_bars = [b for b in bars if int(b["minute_offset"]) < entry_off]
        if len(past_bars) < 10:
            continue
        X[i] = build_sequence_features(past_bars, seq_len=seq_len, feature_keys=FEATURE_KEYS)
        y[i] = int(row["label_int"])
        valid_idx.append(i)
        n_valid += 1
    return X, y, valid_idx


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--min-fast-score", type=float, default=85.0)
    p.add_argument("--min-strategies-passed", type=int, default=4)
    p.add_argument("--oos-date-count", type=int, default=14)
    p.add_argument("--seq-len", type=int, default=60)
    p.add_argument("--epochs", type=int, default=25)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--num-layers", type=int, default=2)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--out-base", default="models/research/dl_lstm_v1")
    p.add_argument("--report-out", default="reports/ml/research/dl_lstm_train.json")
    args = p.parse_args(argv)

    print(f"[1/5] loading dataset {args.dataset} ...")
    df = pd.read_csv(args.dataset)
    df["session_date"] = df["session_date"].astype(str)
    df["ticker"] = df["ticker"].astype(str)
    df["fast_score"] = pd.to_numeric(df.get("fast_score", 0), errors="coerce").fillna(0)
    df["strategies_passed"] = pd.to_numeric(df.get("strategies_passed", 0), errors="coerce").fillna(0)
    df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
    df["minutes_since_open"] = pd.to_numeric(df.get("minutes_since_open", 0), errors="coerce").fillna(0).astype(int)
    df = df[(df["fast_score"] >= args.min_fast_score)
            & (df["strategies_passed"] >= args.min_strategies_passed)
            & (df["label_int"].isin([0, 1]))].reset_index(drop=True)
    print(f"  filtered decided rows: {len(df)}")

    print(f"[2/5] OOS split (last {args.oos_date_count} sessions)...")
    dates = sorted(df["session_date"].unique())
    test_dates = dates[-args.oos_date_count:]
    train_df = df[~df["session_date"].isin(test_dates)].reset_index(drop=True)
    test_df  = df[df["session_date"].isin(test_dates)].reset_index(drop=True)
    print(f"  train: {len(train_df)} | test: {len(test_df)} | OOS sessions: {len(test_dates)}")

    print(f"[3/5] loading minute bars (DB join)...")
    lookup_train = _load_bars_lookup(Path(args.minute_db), train_df)
    lookup_test  = _load_bars_lookup(Path(args.minute_db), test_df)
    print(f"  train groups loaded: {len(lookup_train)} | test groups: {len(lookup_test)}")

    print(f"[4/5] building sequences (seq_len={args.seq_len})...")
    X_train, y_train, train_valid = _build_sequences(train_df, lookup_train, args.seq_len)
    X_test, y_test, test_valid    = _build_sequences(test_df,  lookup_test,  args.seq_len)
    X_train = X_train[train_valid]; y_train = y_train[train_valid]
    X_test  = X_test[test_valid];   y_test  = y_test[test_valid]
    test_df_valid = test_df.iloc[test_valid].reset_index(drop=True)
    print(f"  train sequences: {len(X_train)} | test sequences: {len(X_test)}")
    if len(X_train) < 200:
        print("[error] train sequences too small", file=sys.stderr)
        return 2

    # sample_weight: 시간 가중 (최근 학습 세션 가중)
    train_dates = train_df.iloc[train_valid].reset_index(drop=True)["session_date"].astype("datetime64[ns]")
    max_d = train_dates.max()
    days_ago = (max_d - train_dates).dt.days.astype(float).fillna(0.0).values
    sample_weight = np.exp(-0.05 * days_ago).clip(min=0.05)

    print(f"[5/5] training LSTM (hidden={args.hidden}, layers={args.num_layers}, epochs={args.epochs})...")
    cfg = LSTMConfig(
        seq_len=args.seq_len,
        feature_dim=len(FEATURE_KEYS),
        hidden_size=args.hidden,
        num_layers=args.num_layers,
        max_epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.lr,
    )
    model = LSTMBinaryClassifier(cfg)
    history = model.fit(X_train, y_train, sample_weight=sample_weight, val_split=0.15, verbose=True)

    print(f"\n[eval] computing test metrics...")
    proba = model.predict_proba(X_test)
    tp_prob = proba[:, 1] if proba.shape[1] >= 2 else np.zeros(len(X_test))
    if len(set(y_test)) > 1:
        roc = float(roc_auc_score(y_test, tp_prob))
        pr  = float(average_precision_score(y_test, tp_prob))
    else:
        roc = None; pr = None

    # daily_top_N
    test_df_valid["tp_prob"] = tp_prob
    test_df_valid["label_int"] = pd.to_numeric(test_df_valid["label_int"], errors="coerce")
    # net_return_pct 가 있으면 그쪽, 없으면 return_pct
    ret_col = "net_return_pct" if "net_return_pct" in test_df_valid.columns else "return_pct"
    test_df_valid[ret_col] = pd.to_numeric(test_df_valid[ret_col], errors="coerce")
    topn = {}
    for n in (1, 2, 3, 5):
        picks = []
        for _, g in test_df_valid.groupby("session_date"):
            picks.append(g.nlargest(n, "tp_prob"))
        tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
        if len(tdf) == 0:
            topn[f"daily_top_{n}"] = {"n": 0, "precision": None, "avg_net": None, "hit_net_positive": None}
            continue
        precision = float((tdf["label_int"] == 1).mean())
        avg_net = float(tdf[ret_col].mean())
        hit_pos = float((tdf[ret_col] > 0).mean())
        topn[f"daily_top_{n}"] = {"n": int(len(tdf)), "precision": round(precision, 4),
                                   "avg_net": round(avg_net, 4),
                                   "hit_net_positive": round(hit_pos, 4)}
    print(f"  ROC AUC: {roc!s} | PR AUC: {pr!s}")
    for k, v in topn.items():
        print(f"  {k}: {v}")

    # Save artifacts
    pt_path = Path(args.out_base + ".pt")
    meta_path = Path(args.out_base + "_meta.joblib")
    pt_path.parent.mkdir(parents=True, exist_ok=True)

    import torch
    torch.save(model.state_for_save(), pt_path)
    joblib.dump({
        "model_path": str(pt_path),
        "feature_keys": list(FEATURE_KEYS),
        "seq_len": args.seq_len,
        "cfg": cfg,
        "model_type": "lstm_pytorch",
        "model_version": f"daytrade_v1_lstm_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "feature_version": "daytrade_v1_seq",
        "metrics": {
            "roc_auc": roc, "pr_auc": pr, "daily_top_n": topn,
            "rows_train": len(X_train), "rows_test": len(X_test),
        },
    }, meta_path)
    print(f"\n[saved] {pt_path}")
    print(f"[saved] {meta_path}")

    # Train report
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "feature_keys": list(FEATURE_KEYS),
        "rows_train": len(X_train),
        "rows_test": len(X_test),
        "oos_sessions": test_dates,
        "cfg": {
            "seq_len": args.seq_len, "hidden_size": args.hidden,
            "num_layers": args.num_layers, "lr": args.lr,
            "max_epochs": args.epochs, "patience": args.patience,
        },
        "history": history,
        "metrics": {
            "roc_auc": roc, "pr_auc": pr, "daily_top_n": topn,
        },
        "model_path": str(pt_path),
        "meta_path": str(meta_path),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[report] {args.report_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
