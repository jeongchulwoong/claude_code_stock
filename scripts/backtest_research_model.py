"""
scripts/backtest_research_model.py — research 모델을 forward paper 데이터에 적용하는 백테스트.

본 스크립트는 ML model artifact 를 paper-trader event CSV (또는 event dataset CSV) 에
적용해 daily-top-N + threshold 별 win-rate / avg net return / max drawdown 을 보고한다.

운영 매니페스트 / 라이브 게이트는 절대 수정하지 않는다. 결과는 stdout + 선택적 JSON.

사용:
  python scripts/backtest_research_model.py \\
    --model models/research/iterQ_et_strict_*.joblib \\
    --events 'db/ml/daytrade_ml_paper_trader_events_asym90_2026*.csv'
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
import pandas as pd  # noqa: E402

COST_PCT_DEFAULT = 0.4   # 왕복 비용 (%) 기본 — KOSPI retail 권장.


def _resolve_estimator(obj: Any) -> tuple[Any, list[str] | None]:
    """artifact dict 에서 estimator + feature_columns 추출."""
    if isinstance(obj, dict):
        est = obj.get("model") or obj.get("estimator")
        cols = obj.get("feature_columns")
        return est, (list(cols) if isinstance(cols, (list, tuple)) else None)
    return obj, getattr(obj, "feature_columns", None)


def _build_feature_matrix(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in feature_columns:
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            # 결측 컬럼 — 0 으로 채움 (학습 빌더와 일관).
            v = pd.Series(0.0, index=df.index)
        out[col] = v
    return out


def _tp_class_index(estimator: Any) -> int:
    """estimator.classes_ 에서 label=1 (TP) 의 컬럼 위치."""
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


def _backtest_metrics(picks: pd.DataFrame, ret_col: str) -> dict:
    if len(picks) == 0:
        return {"n": 0, "hit_rate": None, "avg_return_pct": None,
                "max_drawdown_pct": None, "wins": 0, "losses": 0}
    wins = int((picks[ret_col] > 0).sum())
    losses = int((picks[ret_col] <= 0).sum())
    hit = wins / len(picks)
    avg = float(picks[ret_col].mean())
    cum = picks[ret_col].cumsum()
    peak = cum.cummax()
    dd = float((cum - peak).min())
    return {
        "n": len(picks),
        "hit_rate": round(hit, 4),
        "avg_return_pct": round(avg, 4),
        "max_drawdown_pct": round(dd, 4),
        "wins": wins,
        "losses": losses,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True, help="model joblib path")
    p.add_argument("--events", required=True, help="event CSV glob")
    p.add_argument("--cost-pct", type=float, default=COST_PCT_DEFAULT)
    p.add_argument("--json-out", default=None)
    p.add_argument("--rank-col", default=None,
                   help="추가 ranking 보조 — 'fast_score' 등 (없으면 raw probability 만 사용)")
    args = p.parse_args(argv)

    # 모델 로드
    obj = joblib.load(args.model)
    estimator, feature_columns = _resolve_estimator(obj)
    if estimator is None or not feature_columns:
        print(f"[error] artifact missing model or feature_columns: {args.model}", file=sys.stderr)
        return 2

    # 이벤트 데이터 로드
    paths = sorted(glob.glob(args.events))
    paths = [p for p in paths if not p.endswith("_today.csv")]
    if not paths:
        print(f"[error] no event CSVs match: {args.events}", file=sys.stderr)
        return 2
    dfs = []
    for path in paths:
        try:
            dfs.append(pd.read_csv(path))
        except Exception as e:
            print(f"[skip] {path}: {e}", file=sys.stderr)
    if not dfs:
        print("[error] no readable event CSV", file=sys.stderr)
        return 2
    df = pd.concat(dfs, ignore_index=True)

    # 라벨된 행만 평가 (label_int 가 -1, 0, 1 중 하나)
    if "label_int" not in df.columns:
        print("[error] event CSV missing label_int", file=sys.stderr)
        return 2
    df["label_int"] = pd.to_numeric(df["label_int"], errors="coerce")
    labeled = df[df["label_int"].isin([-1, 0, 1])].copy()
    if len(labeled) == 0:
        print("[error] no labeled rows", file=sys.stderr)
        return 2

    # 가격 차감 net return 컬럼.
    if "return_pct" in labeled.columns:
        labeled["return_pct"] = pd.to_numeric(labeled["return_pct"], errors="coerce").fillna(0.0)
        labeled["net_return_pct"] = labeled["return_pct"] - args.cost_pct
    else:
        print("[error] event CSV missing return_pct", file=sys.stderr)
        return 2

    # 점수 매기기
    X = _build_feature_matrix(labeled, list(feature_columns))
    proba = estimator.predict_proba(X)
    tp_idx = _tp_class_index(estimator)
    labeled["tp_prob"] = proba[:, tp_idx]
    if args.rank_col and args.rank_col in labeled.columns:
        labeled["rank_score"] = labeled["tp_prob"] * pd.to_numeric(labeled[args.rank_col], errors="coerce").fillna(0.0)
    else:
        labeled["rank_score"] = labeled["tp_prob"]

    sessions = sorted(labeled.get("session_date", pd.Series(dtype=str)).dropna().unique().tolist())
    n_sessions = len(sessions)

    print(f"=== BACKTEST: {Path(args.model).name} ===")
    print(f"events labeled: {len(labeled)} / {len(df)} (sessions={n_sessions}, tickers={labeled['ticker'].nunique() if 'ticker' in labeled.columns else 0})")
    print(f"cost_pct: {args.cost_pct}, label distribution: TP={int((labeled['label_int']==1).sum())} SL={int((labeled['label_int']==0).sum())} timeout={int((labeled['label_int']==-1).sum())}")
    print()

    # ── 전체 풀 (필터 없음)
    print("=== A. 전체 라벨된 풀 (no filter) ===")
    all_metrics_decided = _backtest_metrics(
        labeled[labeled["label_int"].isin([0, 1])], "net_return_pct"
    )
    print(f"  decided  : n={all_metrics_decided['n']}  TP/(TP+SL)={all_metrics_decided['hit_rate']}  avg_net={all_metrics_decided['avg_return_pct']}  DD={all_metrics_decided['max_drawdown_pct']}")
    all_metrics_total = _backtest_metrics(labeled, "net_return_pct")
    print(f"  all      : n={all_metrics_total['n']}  positive_rate={all_metrics_total['hit_rate']}  avg_net={all_metrics_total['avg_return_pct']}  DD={all_metrics_total['max_drawdown_pct']}")
    print()

    # ── B. probability threshold 별
    print("=== B. probability threshold 별 ===")
    by_thr = {}
    for thr in (0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65):
        sub = labeled[labeled["tp_prob"] >= thr]
        m = _backtest_metrics(sub, "net_return_pct")
        sub_dec = sub[sub["label_int"].isin([0, 1])]
        m_dec = _backtest_metrics(sub_dec, "net_return_pct")
        by_thr[thr] = {"all": m, "decided": m_dec}
        print(f"  thr={thr:.2f}  n={m['n']:>5}  pos_rate={m['hit_rate']!s:>6}  decided_n={m_dec['n']:>5}  decided_hit={m_dec['hit_rate']!s:>6}  avg_net={m['avg_return_pct']!s:>9}  DD={m['max_drawdown_pct']!s:>9}")
    print()

    # ── C. daily top-N
    print("=== C. daily top-N (rank by tp_prob) ===")
    by_topn = {}
    if "session_date" in labeled.columns and n_sessions > 0:
        for n in (1, 2, 3, 5, 10):
            picks = []
            for _, g in labeled.groupby("session_date"):
                top = g.nlargest(n, "rank_score")
                picks.append(top)
            tdf = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
            m_all = _backtest_metrics(tdf, "net_return_pct")
            tdf_dec = tdf[tdf["label_int"].isin([0, 1])] if len(tdf) else tdf
            m_dec = _backtest_metrics(tdf_dec, "net_return_pct")
            by_topn[n] = {"all": m_all, "decided": m_dec}
            print(f"  daily_top_{n:<2} n={m_all['n']:>5}  pos_rate={m_all['hit_rate']!s:>6}  decided_n={m_dec['n']:>5}  decided_hit={m_dec['hit_rate']!s:>6}  avg_net={m_all['avg_return_pct']!s:>9}  DD={m_all['max_drawdown_pct']!s:>9}")
    print()

    # ── D. top-K percentile of tp_prob
    print("=== D. top-K percentile by tp_prob ===")
    by_pct = {}
    for pct in (0.01, 0.05, 0.10, 0.20, 0.30):
        k = max(1, int(len(labeled) * pct))
        sub = labeled.nlargest(k, "rank_score")
        m_all = _backtest_metrics(sub, "net_return_pct")
        sub_dec = sub[sub["label_int"].isin([0, 1])]
        m_dec = _backtest_metrics(sub_dec, "net_return_pct")
        by_pct[pct] = {"all": m_all, "decided": m_dec}
        print(f"  top_{pct*100:>4.1f}%  n={m_all['n']:>5}  pos_rate={m_all['hit_rate']!s:>6}  decided_n={m_dec['n']:>5}  decided_hit={m_dec['hit_rate']!s:>6}  avg_net={m_all['avg_return_pct']!s:>9}  DD={m_all['max_drawdown_pct']!s:>9}")
    print()

    if args.json_out:
        payload = {
            "model": args.model,
            "events_glob": args.events,
            "cost_pct": args.cost_pct,
            "labeled_rows": len(labeled),
            "sessions": n_sessions,
            "all_pool": {
                "decided": all_metrics_decided,
                "all": all_metrics_total,
            },
            "by_threshold": by_thr,
            "by_daily_top_n": by_topn,
            "by_top_percentile": by_pct,
        }
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[wrote JSON -> {args.json_out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
