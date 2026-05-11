"""
scripts/analyze_iter_models.py — research iter 모델 학습 보고서 비교 분석.

각 학습 보고서의 다음을 표 형태로 출력:
  - ROC AUC, PR AUC, statistical_strength
  - precision_at_top_probability (top_1pct/5pct/10pct)
  - daily_top_1/2/3 precision + avg_return
  - thresholds 0.30/0.40/0.50/0.55 별 count + hit_rate + avg_return
  - candidate_filter 적용 여부

사용:
  python scripts/analyze_iter_models.py reports/ml/research/iter*.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _fmt(v: Any, width: int = 7, fmt: str = ".3f") -> str:
    if v is None:
        return f"{'-':>{width}}"
    try:
        return f"{float(v):{width}{fmt}}"
    except Exception:
        return f"{str(v):>{width}}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("reports", nargs="+", help="iter*.json paths")
    args = p.parse_args(argv)

    rows = []
    for rpath in args.reports:
        try:
            d = json.loads(Path(rpath).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[skip] {rpath}: {e}", file=sys.stderr)
            continue
        m = d.get("metrics", {})
        cf = m.get("candidate_filter", {})
        ptop = m.get("precision_at_top_probability", {}) or {}
        dtop = m.get("daily_top_n_probability", {}) or {}
        thr = d.get("thresholds", {}) or {}
        rows.append({
            "name": Path(rpath).stem,
            "model_type": m.get("model_type") or m.get("estimator_params") or "?",
            "label_mode": m.get("label_mode"),
            "filter_min_fast": cf.get("min_fast_score"),
            "filter_min_strat": cf.get("min_strategies_passed"),
            "filter_rows_after": cf.get("rows_after"),
            "rows_train": m.get("rows_train"),
            "rows_test": m.get("rows_test"),
            "roc_auc": m.get("roc_auc"),
            "pr_auc": m.get("pr_auc"),
            "stat_strength": m.get("statistical_strength"),
            "pos_rate_test": m.get("positive_rate_test"),
            "top1pct_n": (ptop.get("top_1pct") or {}).get("count"),
            "top1pct_p": (ptop.get("top_1pct") or {}).get("precision"),
            "top1pct_r": (ptop.get("top_1pct") or {}).get("avg_return_pct"),
            "top5pct_n": (ptop.get("top_5pct") or {}).get("count"),
            "top5pct_p": (ptop.get("top_5pct") or {}).get("precision"),
            "top5pct_r": (ptop.get("top_5pct") or {}).get("avg_return_pct"),
            "top10pct_n": (ptop.get("top_10pct") or {}).get("count"),
            "top10pct_p": (ptop.get("top_10pct") or {}).get("precision"),
            "top10pct_r": (ptop.get("top_10pct") or {}).get("avg_return_pct"),
            "dtop1_n": (dtop.get("daily_top_1") or {}).get("count"),
            "dtop1_p": (dtop.get("daily_top_1") or {}).get("precision"),
            "dtop1_r": (dtop.get("daily_top_1") or {}).get("avg_return_pct"),
            "dtop2_n": (dtop.get("daily_top_2") or {}).get("count"),
            "dtop2_p": (dtop.get("daily_top_2") or {}).get("precision"),
            "dtop2_r": (dtop.get("daily_top_2") or {}).get("avg_return_pct"),
            "dtop3_n": (dtop.get("daily_top_3") or {}).get("count"),
            "dtop3_p": (dtop.get("daily_top_3") or {}).get("precision"),
            "dtop3_r": (dtop.get("daily_top_3") or {}).get("avg_return_pct"),
            "thr055_n": (thr.get("0.55") or {}).get("count"),
            "thr055_h": (thr.get("0.55") or {}).get("hit_rate"),
            "thr055_r": (thr.get("0.55") or {}).get("avg_return_pct"),
            "thr050_n": (thr.get("0.50") or {}).get("count"),
            "thr050_h": (thr.get("0.50") or {}).get("hit_rate"),
            "thr040_n": (thr.get("0.40") or {}).get("count"),
            "thr040_h": (thr.get("0.40") or {}).get("hit_rate"),
            "thr030_n": (thr.get("0.30") or {}).get("count"),
            "thr030_h": (thr.get("0.30") or {}).get("hit_rate"),
        })

    # Header
    print(f"{'name':<55} {'roc':>5} {'pr':>5} {'rows':>6}")
    print("-" * 80)
    for r in rows:
        print(
            f"{r['name'][:55]:<55} "
            f"{_fmt(r['roc_auc'], 5)} "
            f"{_fmt(r['pr_auc'], 5)} "
            f"{(r['filter_rows_after'] or '?'):>6}"
        )

    print()
    print("DAILY TOP-N PRECISION + AVG RETURN (test OOS):")
    print(f"{'name':<55} {'top1_p':>7} {'top1_r':>8} {'top2_p':>7} {'top2_r':>8} {'top3_p':>7} {'top3_r':>8}")
    print("-" * 105)
    for r in rows:
        print(
            f"{r['name'][:55]:<55} "
            f"{_fmt(r['dtop1_p'], 7, '.3f')} {_fmt(r['dtop1_r'], 8, '.3f')} "
            f"{_fmt(r['dtop2_p'], 7, '.3f')} {_fmt(r['dtop2_r'], 8, '.3f')} "
            f"{_fmt(r['dtop3_p'], 7, '.3f')} {_fmt(r['dtop3_r'], 8, '.3f')}"
        )

    print()
    print("TOP-PCT PRECISION + AVG RETURN (test OOS):")
    print(f"{'name':<55} {'1pct_n':>6} {'1pct_p':>7} {'1pct_r':>8} {'5pct_n':>6} {'5pct_p':>7} {'5pct_r':>8} {'10pct_p':>7}")
    print("-" * 120)
    for r in rows:
        print(
            f"{r['name'][:55]:<55} "
            f"{(r['top1pct_n'] or '-'):>6} {_fmt(r['top1pct_p'], 7, '.3f')} {_fmt(r['top1pct_r'], 8, '.3f')} "
            f"{(r['top5pct_n'] or '-'):>6} {_fmt(r['top5pct_p'], 7, '.3f')} {_fmt(r['top5pct_r'], 8, '.3f')} "
            f"{_fmt(r['top10pct_p'], 7, '.3f')}"
        )

    print()
    print("THRESHOLD SWEEP (count / hit_rate):")
    print(f"{'name':<55} {'thr=0.30':>16} {'thr=0.40':>16} {'thr=0.50':>16} {'thr=0.55':>16}")
    print("-" * 125)
    for r in rows:
        def fmt_thr(n, h):
            n_s = "-" if n is None else f"{int(n)}"
            h_s = "-" if h is None else f"{float(h):.3f}"
            return f"{n_s}/{h_s}"
        print(
            f"{r['name'][:55]:<55} "
            f"{fmt_thr(r['thr030_n'], r['thr030_h']):>16} "
            f"{fmt_thr(r['thr040_n'], r['thr040_h']):>16} "
            f"{fmt_thr(r['thr050_n'], r['thr050_h']):>16} "
            f"{fmt_thr(r['thr055_n'], r['thr055_h']):>16}"
        )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
