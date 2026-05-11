"""
scripts/check_ml_promotion_readiness.py -- ML 운영/승급 readiness 자동 평가.

본 스크립트는:
  - manifest.roadmap 의 각 phase exit_criteria 와 현재 paper-forward 데이터를 비교
  - 각 phase 에 PASS / PENDING / NOT_REACHED 라벨링
  - 다음 운영자 액션을 명시
  - **자동 promotion 절대 안 함** -- 보고만.
  - **paper_only / use_as_entry_gate 절대 변경 X**.
  - 어떤 입력에도 raise 하지 않는다.

데이터 소스:
  - db/ml/daytrade_ml_paper_trader_events_asym90_YYYYMMDD.csv (forward snapshots)
  - db/ml/approved_model_manifest.json (roadmap + criteria)
  - reports/ml/research/*.json (DL challenger 학습 결과 -- 선택)

사용:
  python scripts/check_ml_promotion_readiness.py
  python scripts/check_ml_promotion_readiness.py --json reports/ml/promotion_readiness.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

READINESS_VERSION = "ml_promotion_readiness_v1"

DEFAULT_MANIFEST  = "db/ml/approved_model_manifest.json"
DEFAULT_SNAPSHOTS_DIR = "db/ml"
DEFAULT_SNAPSHOT_PATTERN = "daytrade_ml_paper_trader_events_asym90_2026*.csv"


@dataclass
class CriterionResult:
    name: str
    target: Any
    actual: Any
    passed: bool
    notes: str = ""


@dataclass
class PhaseResult:
    phase: str
    description: str
    status: str   # 'PASS' / 'PENDING' / 'NOT_REACHED'
    criteria: list[CriterionResult] = field(default_factory=list)
    next_action: str = ""


@dataclass
class ReadinessReport:
    generated_at: str
    version: str = READINESS_VERSION
    manifest_path: str = ""
    forward_paper_snapshot_count: int = 0
    forward_paper_total_events: int = 0
    forward_paper_unique_tickers: int = 0
    avg_microstructure_coverage: float = 0.0
    daily_top_n_avg_return_pct: float | None = None
    daily_top_n_hit_rate: float | None = None
    unique_winning_tickers: int = 0
    max_drawdown_pct: float | None = None
    # Walk-forward N-session top-2 EV (cost-adjusted) — 단일 세션 노이즈 회피용.
    daily_top_n_size: int = 2
    walk_forward_window_sessions: int = 7
    walk_forward_avg_return_pct: float | None = None
    walk_forward_window_count: int = 0
    walk_forward_positive_window_count: int = 0
    # Threshold sensitivity (overfit detector) — chosen threshold ± 0.05 의 EV drift.
    threshold_sensitivity: dict = field(default_factory=dict)
    phases: list[PhaseResult] = field(default_factory=list)
    current_phase_label: str = ""
    overall_summary: str = ""


def _read_json_safe(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_forward_snapshots(snapshots_dir: Path, pattern: str) -> Any:
    """Return concatenated DataFrame of forward paper snapshots, or None on failure."""
    try:
        import pandas as pd
    except Exception:
        return None
    files = sorted(snapshots_dir.glob(pattern))
    files = [f for f in files if not f.name.endswith("_today.csv")]
    if not files:
        return None
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_csv(f))
        except Exception:
            continue
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
    except Exception:
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n


# ──────────────────────────────────────────────────────────────────
# Forward paper aggregation
# ──────────────────────────────────────────────────────────────────

def _net_return_column(df: Any) -> str:
    """비용 차감 수익률 컬럼이 있으면 그쪽을, 없으면 raw return 컬럼을 사용.

    학습 데이터셋 v2 (cost-adjusted) 는 net_return_pct 를 가진다. 이전 v1 은 return_pct
    만 있어 비용 모델링이 게이트 호출자 책임. 둘 다 있으면 net 우선.
    """
    if df is None:
        return "return_pct"
    cols = set(getattr(df, "columns", []))
    if "net_return_pct" in cols:
        return "net_return_pct"
    return "return_pct"


def _walk_forward_top_n(
    df: Any,
    *,
    n: int = 2,
    window: int = 7,
    rank_col: str | None = None,
    ret_col: str | None = None,
) -> dict:
    """Daily top-N (대개 N=2) cost-adjusted EV 의 walk-forward 평균.

    - 세션별로 rank_col (default fast_score) 상위 N 개 선택
    - per-session 평균 net_return 시계열 생성
    - 시간순으로 window (default 7) 세션씩 rolling mean
    - 각 window 의 평균이 양수인 비율을 반환

    단일 세션 노이즈 (랜덤 스파이크 / 한 종목 운빨) 를 흡수하기 위한 게이트.
    """
    out = {
        "n": int(n),
        "window": int(window),
        "avg_return_pct": None,
        "window_count": 0,
        "positive_window_count": 0,
    }
    if df is None or len(df) == 0:
        return out
    if "session_date" not in df.columns:
        return out
    rc = rank_col or ("fast_score" if "fast_score" in df.columns else None)
    if rc is None:
        return out
    rrc = ret_col or _net_return_column(df)
    if rrc not in df.columns:
        return out
    try:
        per_session_avg: list[tuple[str, float]] = []
        for sd, g in df.groupby("session_date"):
            top = g.nlargest(n, rc)
            if len(top) == 0:
                continue
            per_session_avg.append((str(sd), float(top[rrc].mean())))
        per_session_avg.sort(key=lambda t: t[0])   # session_date 가 ISO 라 lexical = 시간순
        if not per_session_avg:
            return out
        # rolling window: 정확히 `window` 만큼 모여야 평가 (편향 회피).
        windows: list[float] = []
        for i in range(0, len(per_session_avg) - window + 1):
            chunk = per_session_avg[i:i + window]
            avg = sum(v for _, v in chunk) / window
            windows.append(avg)
        if not windows:
            return out
        positive = sum(1 for w in windows if w > 0)
        out["avg_return_pct"] = float(sum(windows) / len(windows))
        out["window_count"] = len(windows)
        out["positive_window_count"] = positive
    except Exception:
        return out
    return out


def _threshold_sensitivity(
    df: Any,
    *,
    score_col: str | None = None,
    base_threshold: float = 0.55,
    delta: float = 0.05,
    drift_warn_pct: float = 50.0,
) -> dict:
    """선택된 score threshold 주변 ± delta 에서 EV 가 ±drift_warn_pct% 이상 흔들리면 overfit 의심.

    - score_col 이 없거나 데이터가 얇으면 'unknown' 반환
    - threshold 는 prob 또는 model confidence 류 컬럼에 적용 (제공되는 것 우선)

    드리프트가 큰 경우 모델이 특정 cutoff 에 과적합된 신호. operator 에 경고만, 자동 차단 X.
    """
    out: dict = {
        "score_col": None,
        "base_threshold": float(base_threshold),
        "delta": float(delta),
        "ev_at_minus": None,
        "ev_at_base": None,
        "ev_at_plus": None,
        "max_drift_pct": None,
        "overfit_warning": False,
        "drift_warn_pct": float(drift_warn_pct),
    }
    if df is None or len(df) == 0:
        return out
    candidates = ("model_confidence", "tp_first_prob", "score", "ai_confidence")
    sc = score_col
    if sc is None:
        for c in candidates:
            if c in getattr(df, "columns", []):
                sc = c
                break
    if sc is None or sc not in df.columns:
        return out
    rrc = _net_return_column(df)
    if rrc not in df.columns:
        return out
    try:
        evs = {}
        for label, thr in (("ev_at_minus", base_threshold - delta),
                           ("ev_at_base",  base_threshold),
                           ("ev_at_plus",  base_threshold + delta)):
            sel = df[df[sc].astype(float) >= float(thr)]
            evs[label] = float(sel[rrc].mean()) if len(sel) > 0 else None
        out["score_col"] = sc
        out.update(evs)
        # drift = ((ev_perturbed - ev_base) / |ev_base|) * 100
        base_ev = evs.get("ev_at_base")
        if base_ev is not None and abs(base_ev) > 1e-9:
            drifts = []
            for k in ("ev_at_minus", "ev_at_plus"):
                v = evs.get(k)
                if v is None:
                    continue
                drifts.append(abs((v - base_ev) / abs(base_ev) * 100.0))
            if drifts:
                out["max_drift_pct"] = max(drifts)
                out["overfit_warning"] = bool(out["max_drift_pct"] > drift_warn_pct)
    except Exception:
        return out
    return out


def aggregate_forward_paper(df: Any) -> dict:
    """Compute aggregated metrics from forward paper snapshots."""
    if df is None or len(df) == 0:
        return {
            "rows": 0, "sessions": 0, "unique_tickers": 0,
            "avg_microstructure_coverage": 0.0,
            "daily_top_n_avg_return_pct": None,
            "daily_top_n_hit_rate": None,
            "unique_winning_tickers": 0,
            "max_drawdown_pct": None,
        }
    rows = len(df)
    sessions = int(df["session_date"].nunique()) if "session_date" in df.columns else 0
    tickers = int(df["ticker"].nunique()) if "ticker" in df.columns else 0

    # microstructure coverage 평균 -- bid_qty/ask_qty/queue_imbalance 가 0 이 아닌 비율.
    ms_cols = ["spread_pct", "bid_qty", "ask_qty", "queue_imbalance", "microprice_gap_pct"]
    have = [c for c in ms_cols if c in df.columns]
    if have:
        coverage_each = [(df[c] != 0).mean() for c in have]
        avg_ms = float(sum(coverage_each) / len(coverage_each))
    else:
        avg_ms = 0.0

    # daily top-2 EV 시뮬레이션 (sessions 별로 상위 2개 picked)
    daily_top_avg = None
    daily_top_hit = None
    unique_wins = 0
    max_dd = None
    ret_col = _net_return_column(df)   # cost-adjusted 우선
    if sessions > 0 and "session_date" in df.columns and ret_col in df.columns:
        try:
            picks = []
            for sd, g in df.groupby("session_date"):
                # rank by score-like field if available, else by return.
                rank_col = "fast_score" if "fast_score" in g.columns else None
                if rank_col is None:
                    continue
                top = g.nlargest(2, rank_col)
                picks.append(top)
            if picks:
                import pandas as pd
                tdf = pd.concat(picks, ignore_index=True)
                if len(tdf) > 0:
                    daily_top_avg = float(tdf[ret_col].mean())
                    if "label_int" in tdf.columns:
                        daily_top_hit = float((tdf["label_int"] == 1).mean())
                    if "ticker" in tdf.columns and "label_int" in tdf.columns:
                        winners = tdf.loc[tdf["label_int"] == 1, "ticker"].unique()
                        unique_wins = int(len(winners))
                    # max drawdown across picks (simple: cumulative sum of returns)
                    cum = tdf[ret_col].cumsum()
                    peak = cum.cummax()
                    dd = (cum - peak).min()
                    max_dd = float(dd)
        except Exception:
            pass

    walk_forward = _walk_forward_top_n(df, n=2, window=7)
    threshold_sens = _threshold_sensitivity(df, base_threshold=0.55, delta=0.05)

    return {
        "rows": rows,
        "sessions": sessions,
        "unique_tickers": tickers,
        "avg_microstructure_coverage": avg_ms,
        "daily_top_n_avg_return_pct": daily_top_avg,
        "daily_top_n_hit_rate": daily_top_hit,
        "unique_winning_tickers": unique_wins,
        "max_drawdown_pct": max_dd,
        "return_col_used": ret_col,
        "walk_forward_top_n": walk_forward,
        "threshold_sensitivity": threshold_sens,
    }


# ──────────────────────────────────────────────────────────────────
# Phase evaluation
# ──────────────────────────────────────────────────────────────────

def _eval_phase_1(metrics: dict, criteria: dict) -> PhaseResult:
    rs = []
    rs.append(CriterionResult(
        name="forward_paper_trades",
        target=criteria.get("min_forward_paper_trades", 300),
        actual=metrics.get("rows", 0),
        passed=int(metrics.get("rows", 0)) >= int(criteria.get("min_forward_paper_trades", 300)),
    ))
    rs.append(CriterionResult(
        name="forward_paper_sessions",
        target=criteria.get("min_forward_paper_sessions", 10),
        actual=metrics.get("sessions", 0),
        passed=int(metrics.get("sessions", 0)) >= int(criteria.get("min_forward_paper_sessions", 10)),
    ))
    rs.append(CriterionResult(
        name="unique_tickers_seen",
        target=criteria.get("min_unique_tickers_seen", 50),
        actual=metrics.get("unique_tickers", 0),
        passed=int(metrics.get("unique_tickers", 0)) >= int(criteria.get("min_unique_tickers_seen", 50)),
    ))
    rs.append(CriterionResult(
        name="avg_microstructure_coverage",
        target=criteria.get("min_avg_microstructure_coverage", 0.5),
        actual=round(_safe_float(metrics.get("avg_microstructure_coverage", 0.0)), 3),
        passed=_safe_float(metrics.get("avg_microstructure_coverage", 0.0)) >= _safe_float(criteria.get("min_avg_microstructure_coverage", 0.5)),
    ))
    all_pass = all(c.passed for c in rs)
    return PhaseResult(
        phase="phase_1_data_accumulation",
        description="RF/meta-label baseline 유지 + 호가/체결/분봉 forward 데이터 축적",
        status=("PASS" if all_pass else "PENDING"),
        criteria=rs,
        next_action=(
            "Phase 1 통과 -- phase_2_baseline_validation 진입 검토 가능"
            if all_pass else
            "Daily paper_trader 자동 가동 + scripts/run_daily_ml_pipeline.py 매일 실행 유지"
        ),
    )


def _eval_phase_2(metrics: dict, criteria: dict, phase_1_passed: bool) -> PhaseResult:
    rs = []
    rs.append(CriterionResult(
        name="cost_adjusted_avg_return_at_daily_top_2",
        target=criteria.get("min_cost_adjusted_avg_return_pct_at_daily_top_2", 0.0),
        actual=metrics.get("daily_top_n_avg_return_pct"),
        passed=(metrics.get("daily_top_n_avg_return_pct") is not None and
                _safe_float(metrics.get("daily_top_n_avg_return_pct")) >= _safe_float(criteria.get("min_cost_adjusted_avg_return_pct_at_daily_top_2", 0.0))),
    ))
    rs.append(CriterionResult(
        name="hit_rate_at_daily_top_2",
        target=criteria.get("min_hit_rate_at_daily_top_2", 0.40),
        actual=metrics.get("daily_top_n_hit_rate"),
        passed=(metrics.get("daily_top_n_hit_rate") is not None and
                _safe_float(metrics.get("daily_top_n_hit_rate")) >= _safe_float(criteria.get("min_hit_rate_at_daily_top_2", 0.40))),
    ))
    rs.append(CriterionResult(
        name="unique_winning_tickers",
        target=criteria.get("min_unique_winning_tickers", 5),
        actual=metrics.get("unique_winning_tickers", 0),
        passed=int(metrics.get("unique_winning_tickers", 0)) >= int(criteria.get("min_unique_winning_tickers", 5)),
    ))
    rs.append(CriterionResult(
        name="max_drawdown_pct",
        target=criteria.get("max_drawdown_pct_limit", 5.0),
        actual=metrics.get("max_drawdown_pct"),
        passed=(metrics.get("max_drawdown_pct") is None or
                abs(_safe_float(metrics.get("max_drawdown_pct"))) <= _safe_float(criteria.get("max_drawdown_pct_limit", 5.0))),
    ))

    # Walk-forward 7-session daily-top-2 평균이 양수여야 한다 (단일 세션 노이즈 회피).
    wf = metrics.get("walk_forward_top_n") or {}
    wf_avg = wf.get("avg_return_pct")
    wf_window_count = int(wf.get("window_count", 0))
    rs.append(CriterionResult(
        name="walk_forward_7s_top2_avg_return_positive",
        target="walk-forward avg(7-session top-2 net return) > 0 AND ≥ 1 window",
        actual={"avg_return_pct": wf_avg,
                "window_count": wf_window_count,
                "positive_window_count": int(wf.get("positive_window_count", 0))},
        passed=(wf_avg is not None and wf_window_count >= 1 and _safe_float(wf_avg) > 0.0),
        notes="단일 세션 양수는 노이즈 — 7세션 rolling 평균이 양수여야 통과.",
    ))

    # threshold sensitivity — 정보 제공만, fail 처리 X (overfit 의심 경고).
    ts = metrics.get("threshold_sensitivity") or {}
    if ts.get("overfit_warning"):
        rs.append(CriterionResult(
            name="threshold_sensitivity_overfit_warning",
            target=f"max drift ≤ {ts.get('drift_warn_pct')}%",
            actual={"max_drift_pct": ts.get("max_drift_pct"),
                    "score_col": ts.get("score_col")},
            passed=False,
            notes="±0.05 perturbation 에서 EV 가 50% 이상 변동 — 특정 cutoff 과적합 의심.",
        ))

    if not phase_1_passed:
        status = "NOT_REACHED"
        action = "Phase 1 통과 후 평가 가능"
    else:
        all_pass = all(c.passed for c in rs)
        status = "PASS" if all_pass else "PENDING"
        action = (
            "Phase 2 통과 -- phase_3_dl_challenger 추가 검토 가능 + operational promotion 도 운영자 검토 대상"
            if all_pass else
            "현재 RF baseline 의 EV/승률 기준 미달. 데이터 품질 개선 / 종목 확장 / 비용 모델 정밀화 우선 (DL 추가 금지)"
        )
    return PhaseResult(
        phase="phase_2_baseline_validation",
        description="현재 RF baseline 이 양수 EV / 승률 기준 통과하는지 OOS forward paper 로 확인",
        status=status,
        criteria=rs,
        next_action=action,
    )


def _eval_phase_3(research_candidates: list, phase_2_passed: bool) -> PhaseResult:
    dl_candidates = [
        rc for rc in research_candidates
        if str(rc.get("model_path", "")).startswith("models/research/dl_")
        or "dl_" in str(rc.get("label", "")).lower()
    ]
    rs = [
        CriterionResult(
            name="dl_challenger_count",
            target="≥ 1 paper-only DL challenger",
            actual=len(dl_candidates),
            passed=len(dl_candidates) >= 1,
        ),
    ]
    if not phase_2_passed:
        status = "NOT_REACHED"
        action = "Phase 2 통과 후에만 DL challenger 추가 가능"
    else:
        status = "PASS" if rs[0].passed else "PENDING"
        action = (
            f"DL challenger {len(dl_candidates)}개 등록됨 -- Phase 4 비교 평가 진행 가능"
            if rs[0].passed else
            "DL challenger 미등록 -- AutoEncoder filter (rank 1) 또는 LSTM (rank 2) 부터 추가 (paper_only)"
        )
    return PhaseResult(
        phase="phase_3_dl_challenger",
        description="DL 을 별도 paper-only challenger 로 추가 -- 운영 path 절대 무영향",
        status=status,
        criteria=rs,
        next_action=action,
    )


def _eval_phase_4(phase_3_passed: bool) -> PhaseResult:
    # Phase 4 는 RF vs DL OOS paper 비교 결과가 별도 보고서로 누적되어야 평가 가능.
    # 현재 단계에서는 항상 NOT_REACHED 또는 PENDING (구현 예약).
    rs = [CriterionResult(
        name="rf_vs_dl_oos_comparison_report",
        target="reports/ml/research/dl_vs_rf_paper_compare_*.json (≥ 20 sessions)",
        actual="not_implemented",
        passed=False,
        notes="DL challenger 등록 후 별도 비교 스크립트로 측정",
    )]
    return PhaseResult(
        phase="phase_4_ensemble_evaluation",
        description="DL challenger 가 RF 대비 OOS forward paper 에서 의미 있게 이기면 ensemble 후보 승격",
        status=("PENDING" if phase_3_passed else "NOT_REACHED"),
        criteria=rs,
        next_action=(
            "DL challenger 의 OOS paper 결과를 RF 와 동일 기간/동일 후보로 비교"
            if phase_3_passed else
            "Phase 3 통과 후 평가 가능"
        ),
    )


def _eval_phase_5(phase_4_passed: bool) -> PhaseResult:
    rs = [CriterionResult(
        name="ensemble_paper_validation",
        target="ensemble_v1 in research_candidates AND ≥ 20 sessions paper run",
        actual="not_implemented",
        passed=False,
        notes="ensemble 승격 후 별도 검증 스크립트로 측정",
    )]
    return PhaseResult(
        phase="phase_5_ensemble_paper_validation",
        description="Ensemble (RF + DL) 추가 paper validation -- operational 승급은 별도 운영자 결정",
        status=("PENDING" if phase_4_passed else "NOT_REACHED"),
        criteria=rs,
        next_action=(
            "Ensemble (RF + DL) artifact 가 research_candidates 에 등록된 후 paper 검증"
            if phase_4_passed else
            "Phase 4 통과 후 평가 가능"
        ),
    )


# ──────────────────────────────────────────────────────────────────
# Top-level orchestration
# ──────────────────────────────────────────────────────────────────

def assess_readiness(
    manifest_path: Path,
    snapshots_dir: Path,
    snapshot_pattern: str,
) -> ReadinessReport:
    rep = ReadinessReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        manifest_path=str(manifest_path),
    )

    manifest = _read_json_safe(manifest_path) or {}
    roadmap  = manifest.get("roadmap") or {}
    phases   = roadmap.get("phases") or {}
    rep.current_phase_label = str(roadmap.get("current_phase", ""))
    research_candidates = manifest.get("research_candidates") or []

    df = _load_forward_snapshots(snapshots_dir, snapshot_pattern)
    metrics = aggregate_forward_paper(df)
    rep.forward_paper_snapshot_count = (
        sum(1 for _ in (snapshots_dir.glob(snapshot_pattern)))
    )
    rep.forward_paper_total_events = int(metrics.get("rows", 0))
    rep.forward_paper_unique_tickers = int(metrics.get("unique_tickers", 0))
    rep.avg_microstructure_coverage = _safe_float(metrics.get("avg_microstructure_coverage", 0.0))
    rep.daily_top_n_avg_return_pct = metrics.get("daily_top_n_avg_return_pct")
    rep.daily_top_n_hit_rate = metrics.get("daily_top_n_hit_rate")
    rep.unique_winning_tickers = int(metrics.get("unique_winning_tickers", 0))
    rep.max_drawdown_pct = metrics.get("max_drawdown_pct")
    wf = metrics.get("walk_forward_top_n") or {}
    rep.daily_top_n_size = int(wf.get("n", 2))
    rep.walk_forward_window_sessions = int(wf.get("window", 7))
    rep.walk_forward_avg_return_pct = wf.get("avg_return_pct")
    rep.walk_forward_window_count = int(wf.get("window_count", 0))
    rep.walk_forward_positive_window_count = int(wf.get("positive_window_count", 0))
    rep.threshold_sensitivity = metrics.get("threshold_sensitivity") or {}

    p1 = _eval_phase_1(metrics, (phases.get("phase_1_data_accumulation") or {}).get("exit_criteria") or {})
    p2 = _eval_phase_2(metrics, (phases.get("phase_2_baseline_validation") or {}).get("exit_criteria") or {}, p1.status == "PASS")
    p3 = _eval_phase_3(research_candidates, p2.status == "PASS")
    p4 = _eval_phase_4(p3.status == "PASS")
    p5 = _eval_phase_5(p4.status == "PASS")
    rep.phases = [p1, p2, p3, p4, p5]

    statuses = [p.status for p in rep.phases]
    if "PASS" in statuses and "PENDING" in statuses:
        rep.overall_summary = f"진행 중 -- {statuses.count('PASS')} phase PASS, 다음 PENDING phase 평가 중"
    elif statuses[0] == "PENDING":
        rep.overall_summary = "Phase 1 진행 중 -- 데이터 누적 단계"
    elif statuses[0] == "PASS" and statuses[1] != "PASS":
        rep.overall_summary = "Phase 1 PASS / Phase 2 baseline 검증 단계"
    elif all(s == "PASS" for s in statuses):
        rep.overall_summary = "전체 PASS -- operational promotion / live gate 운영자 검토"
    else:
        rep.overall_summary = "다중 phase 평가 결과 혼합 -- phase 별 next_action 참조"

    return rep


def render_text(rep: ReadinessReport) -> str:
    lines = []
    lines.append(f"=== ML PROMOTION READINESS -- {rep.generated_at} ===")
    lines.append(f"manifest: {rep.manifest_path}")
    lines.append(f"current_phase (manifest): {rep.current_phase_label}")
    lines.append(f"overall: {rep.overall_summary}")
    lines.append("")
    lines.append("forward paper aggregate:")
    lines.append(f"  snapshots:          {rep.forward_paper_snapshot_count}")
    lines.append(f"  total events:       {rep.forward_paper_total_events}")
    lines.append(f"  unique tickers:     {rep.forward_paper_unique_tickers}")
    lines.append(f"  avg ms coverage:    {rep.avg_microstructure_coverage:.3f}")
    lines.append(f"  daily_top_{rep.daily_top_n_size} avg ret: {rep.daily_top_n_avg_return_pct}")
    lines.append(f"  daily_top_{rep.daily_top_n_size} hit rate:{rep.daily_top_n_hit_rate}")
    lines.append(f"  unique winners:     {rep.unique_winning_tickers}")
    lines.append(f"  max drawdown pct:   {rep.max_drawdown_pct}")
    lines.append(
        f"  walk_forward(top_{rep.daily_top_n_size}, {rep.walk_forward_window_sessions}-session): "
        f"avg={rep.walk_forward_avg_return_pct} "
        f"positive={rep.walk_forward_positive_window_count}/{rep.walk_forward_window_count}"
    )
    ts = rep.threshold_sensitivity or {}
    if ts.get("score_col"):
        lines.append(
            f"  threshold_sens({ts.get('score_col')} @ {ts.get('base_threshold')}±{ts.get('delta')}): "
            f"ev=[{ts.get('ev_at_minus')}, {ts.get('ev_at_base')}, {ts.get('ev_at_plus')}] "
            f"max_drift={ts.get('max_drift_pct')}% "
            f"overfit_warning={ts.get('overfit_warning')}"
        )
    lines.append("")
    for p in rep.phases:
        lines.append(f"[{p.status:11s}] {p.phase}")
        for c in p.criteria:
            mark = "OK  " if c.passed else "MISS"
            lines.append(f"    {mark}  {c.name:50s} target={c.target}  actual={c.actual}")
        if p.next_action:
            lines.append(f"    => {p.next_action}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ML promotion readiness checker (read-only).")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--snapshots-dir", default=DEFAULT_SNAPSHOTS_DIR)
    parser.add_argument("--snapshot-pattern", default=DEFAULT_SNAPSHOT_PATTERN)
    parser.add_argument("--json", default=None,
                        help="If set, also write JSON report to this path.")
    args = parser.parse_args(argv)

    rep = assess_readiness(
        manifest_path=Path(args.manifest),
        snapshots_dir=Path(args.snapshots_dir),
        snapshot_pattern=str(args.snapshot_pattern),
    )

    print(render_text(rep))

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(rep)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[wrote JSON report -> {out_path}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
