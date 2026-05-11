"""tests/test_check_ml_promotion_readiness.py — readiness checker 단위 테스트.

본 테스트는 main / order_manager / risk_manager 를 import 하지 않는다.
Phase gate 정확성 + read-only 정책 + 안전성을 회귀 차단한다.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_ml_promotion_readiness import (   # noqa: E402
    READINESS_VERSION,
    aggregate_forward_paper,
    assess_readiness,
    _eval_phase_1,
    _eval_phase_2,
    _eval_phase_3,
    _eval_phase_4,
    _eval_phase_5,
    _walk_forward_top_n,
    _threshold_sensitivity,
)


def _write_manifest(tmp: pathlib.Path, *, current_phase: str = "phase_1_data_accumulation",
                    research_candidates: list | None = None) -> pathlib.Path:
    rc = research_candidates if research_candidates is not None else []
    manifest = {
        "_schema": "daytrade_ml_manifest_v2",
        "operational": {"model_path": "models/op.joblib", "promotion_gate_status": "not_promoted"},
        "research_candidates": rc,
        "roadmap": {
            "current_phase": current_phase,
            "phases": {
                "phase_1_data_accumulation": {
                    "exit_criteria": {
                        "min_forward_paper_trades": 300,
                        "min_forward_paper_sessions": 10,
                        "min_unique_tickers_seen": 50,
                        "min_avg_microstructure_coverage": 0.5,
                    },
                },
                "phase_2_baseline_validation": {
                    "exit_criteria": {
                        "min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
                        "min_hit_rate_at_daily_top_2": 0.40,
                        "min_unique_winning_tickers": 5,
                        "max_drawdown_pct_limit": 5.0,
                    },
                },
            },
        },
    }
    p = tmp / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _write_snapshot(dir_: pathlib.Path, name: str, rows: list[dict]) -> pathlib.Path:
    import pandas as pd
    p = dir_ / name
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


# ──────────────────────────────────────────────────────────────────
# aggregate_forward_paper
# ──────────────────────────────────────────────────────────────────

def test_aggregate_handles_none_input():
    out = aggregate_forward_paper(None)
    assert out["rows"] == 0
    assert out["sessions"] == 0
    assert out["daily_top_n_avg_return_pct"] is None


def test_aggregate_basic_metrics():
    import pandas as pd
    df = pd.DataFrame([
        {"ticker": "A", "session_date": "2026-05-04", "label_int": 1,
         "fast_score": 80, "return_pct": 1.5, "spread_pct": 0.1, "bid_qty": 10, "ask_qty": 5,
         "queue_imbalance": 0.3, "microprice_gap_pct": 0.0},
        {"ticker": "B", "session_date": "2026-05-04", "label_int": 0,
         "fast_score": 75, "return_pct": -1.0, "spread_pct": 0.2, "bid_qty": 0, "ask_qty": 0,
         "queue_imbalance": 0.0, "microprice_gap_pct": 0.0},
        {"ticker": "C", "session_date": "2026-05-05", "label_int": 1,
         "fast_score": 90, "return_pct": 2.0, "spread_pct": 0.15, "bid_qty": 20, "ask_qty": 10,
         "queue_imbalance": 0.4, "microprice_gap_pct": 0.05},
    ])
    out = aggregate_forward_paper(df)
    assert out["rows"] == 3
    assert out["sessions"] == 2
    assert out["unique_tickers"] == 3
    # daily_top_2 by fast_score: 5/4 → A,B (sorted by fast_score=80,75)
    #                           5/5 → C only (1 row)
    assert out["daily_top_n_avg_return_pct"] is not None


# ──────────────────────────────────────────────────────────────────
# Phase 1 — data accumulation
# ──────────────────────────────────────────────────────────────────

def test_phase_1_pending_when_data_thin():
    crit = {
        "min_forward_paper_trades": 300,
        "min_forward_paper_sessions": 10,
        "min_unique_tickers_seen": 50,
        "min_avg_microstructure_coverage": 0.5,
    }
    metrics = {"rows": 50, "sessions": 2, "unique_tickers": 30, "avg_microstructure_coverage": 0.10}
    out = _eval_phase_1(metrics, crit)
    assert out.status == "PENDING"
    assert all(not c.passed for c in out.criteria)


def test_phase_1_pass_when_all_criteria_met():
    crit = {
        "min_forward_paper_trades": 300,
        "min_forward_paper_sessions": 10,
        "min_unique_tickers_seen": 50,
        "min_avg_microstructure_coverage": 0.5,
    }
    metrics = {"rows": 350, "sessions": 12, "unique_tickers": 75, "avg_microstructure_coverage": 0.65}
    out = _eval_phase_1(metrics, crit)
    assert out.status == "PASS"
    assert all(c.passed for c in out.criteria)


# ──────────────────────────────────────────────────────────────────
# Phase 2 — baseline validation
# ──────────────────────────────────────────────────────────────────

def test_phase_2_not_reached_when_phase1_pending():
    crit = {"min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
            "min_hit_rate_at_daily_top_2": 0.40,
            "min_unique_winning_tickers": 5,
            "max_drawdown_pct_limit": 5.0}
    metrics = {"daily_top_n_avg_return_pct": 0.5, "daily_top_n_hit_rate": 0.6,
               "unique_winning_tickers": 8, "max_drawdown_pct": -2.0}
    out = _eval_phase_2(metrics, crit, phase_1_passed=False)
    assert out.status == "NOT_REACHED"


def test_phase_2_pass_when_baseline_solid():
    crit = {"min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
            "min_hit_rate_at_daily_top_2": 0.40,
            "min_unique_winning_tickers": 5,
            "max_drawdown_pct_limit": 5.0}
    metrics = {"daily_top_n_avg_return_pct": 0.30, "daily_top_n_hit_rate": 0.45,
               "unique_winning_tickers": 7, "max_drawdown_pct": -3.0,
               "walk_forward_top_n": {
                   "avg_return_pct": 0.20, "window_count": 5,
                   "positive_window_count": 5, "n": 2, "window": 7,
               }}
    out = _eval_phase_2(metrics, crit, phase_1_passed=True)
    assert out.status == "PASS"


def test_phase_2_pending_when_walk_forward_negative():
    """단일 세션 daily_top_2 는 양수지만 7세션 walk-forward 평균이 음수면 promotion 불가."""
    crit = {"min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
            "min_hit_rate_at_daily_top_2": 0.40,
            "min_unique_winning_tickers": 5,
            "max_drawdown_pct_limit": 5.0}
    metrics = {"daily_top_n_avg_return_pct": 0.30, "daily_top_n_hit_rate": 0.45,
               "unique_winning_tickers": 7, "max_drawdown_pct": -3.0,
               "walk_forward_top_n": {
                   "avg_return_pct": -0.05, "window_count": 3,
                   "positive_window_count": 1, "n": 2, "window": 7,
               }}
    out = _eval_phase_2(metrics, crit, phase_1_passed=True)
    assert out.status == "PENDING"
    failed_names = [c.name for c in out.criteria if not c.passed]
    assert "walk_forward_7s_top2_avg_return_positive" in failed_names


def test_phase_2_pending_when_walk_forward_no_window():
    """7세션 미달 (window_count=0) 이면 walk-forward 미충족."""
    crit = {"min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
            "min_hit_rate_at_daily_top_2": 0.40,
            "min_unique_winning_tickers": 5,
            "max_drawdown_pct_limit": 5.0}
    metrics = {"daily_top_n_avg_return_pct": 0.30, "daily_top_n_hit_rate": 0.45,
               "unique_winning_tickers": 7, "max_drawdown_pct": -3.0,
               "walk_forward_top_n": {
                   "avg_return_pct": None, "window_count": 0,
                   "positive_window_count": 0, "n": 2, "window": 7,
               }}
    out = _eval_phase_2(metrics, crit, phase_1_passed=True)
    assert out.status == "PENDING"


def test_phase_2_pending_when_negative_ev():
    crit = {"min_cost_adjusted_avg_return_pct_at_daily_top_2": 0.0,
            "min_hit_rate_at_daily_top_2": 0.40,
            "min_unique_winning_tickers": 5,
            "max_drawdown_pct_limit": 5.0}
    metrics = {"daily_top_n_avg_return_pct": -0.10, "daily_top_n_hit_rate": 0.50,
               "unique_winning_tickers": 8, "max_drawdown_pct": -3.0}
    out = _eval_phase_2(metrics, crit, phase_1_passed=True)
    assert out.status == "PENDING"
    # 음수 EV 항목만 fail
    failed_names = [c.name for c in out.criteria if not c.passed]
    assert "cost_adjusted_avg_return_at_daily_top_2" in failed_names


# ──────────────────────────────────────────────────────────────────
# Phase 3 — DL challenger gate
# ──────────────────────────────────────────────────────────────────

def test_phase_3_not_reached_when_phase2_pending():
    out = _eval_phase_3(research_candidates=[], phase_2_passed=False)
    assert out.status == "NOT_REACHED"


def test_phase_3_pending_when_no_dl_candidates_yet():
    rc = [{"label": "rf_baseline", "model_path": "models/rf.joblib"}]
    out = _eval_phase_3(research_candidates=rc, phase_2_passed=True)
    assert out.status == "PENDING"


def test_phase_3_pass_when_dl_candidate_present():
    rc = [
        {"label": "rf_baseline", "model_path": "models/rf.joblib"},
        {"label": "dl_autoencoder_v1", "model_path": "models/research/dl_autoencoder_20260601.joblib"},
    ]
    out = _eval_phase_3(research_candidates=rc, phase_2_passed=True)
    assert out.status == "PASS"


# ──────────────────────────────────────────────────────────────────
# Phase 4/5 — placeholder
# ──────────────────────────────────────────────────────────────────

def test_phase_4_not_reached_when_phase3_pending():
    out = _eval_phase_4(phase_3_passed=False)
    assert out.status == "NOT_REACHED"


def test_phase_4_pending_when_phase3_passed():
    out = _eval_phase_4(phase_3_passed=True)
    assert out.status == "PENDING"


def test_phase_5_not_reached_when_phase4_pending():
    out = _eval_phase_5(phase_4_passed=False)
    assert out.status == "NOT_REACHED"


# ──────────────────────────────────────────────────────────────────
# Top-level assess_readiness
# ──────────────────────────────────────────────────────────────────

def test_assess_readiness_no_snapshots():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        manifest = _write_manifest(tmp)
        out = assess_readiness(
            manifest_path=manifest,
            snapshots_dir=tmp,
            snapshot_pattern="daytrade_ml_paper_trader_events_asym90_2026*.csv",
        )
        assert out.forward_paper_total_events == 0
        # 5 phases 모두 채워짐
        assert len(out.phases) == 5
        # phase 1 은 PENDING (no data)
        assert out.phases[0].status == "PENDING"


def test_assess_readiness_with_snapshots_phase1_passes():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # 12 sessions × 30 rows × 5 different tickers = 360 rows / 5 unique tickers
        # microstructure coverage 100%
        # tickers must be 50+ for phase 1 PASS — we'll make it exactly that.
        rows = []
        tickers = [f"T{i:03d}" for i in range(60)]
        for s in range(12):
            session = f"2026-05-{s+1:02d}"
            for t in tickers:
                rows.append({
                    "ticker": t, "session_date": session, "label_int": 1 if (hash(t)%3==0) else 0,
                    "fast_score": 80, "return_pct": 0.5,
                    "spread_pct": 0.1, "bid_qty": 5, "ask_qty": 5,
                    "queue_imbalance": 0.0, "microprice_gap_pct": 0.0,
                })
        for s in range(12):
            day_str = f"2026050{s+1}" if s+1 < 10 else f"202605{s+1}"
            sub = [r for r in rows if r["session_date"] == f"2026-05-{s+1:02d}"]
            _write_snapshot(tmp, f"daytrade_ml_paper_trader_events_asym90_{day_str}.csv", sub)

        manifest = _write_manifest(tmp)
        out = assess_readiness(
            manifest_path=manifest,
            snapshots_dir=tmp,
            snapshot_pattern="daytrade_ml_paper_trader_events_asym90_2026*.csv",
        )
        # 12 sessions × 60 tickers each — meets all phase 1 criteria
        assert out.phases[0].status == "PASS", f"phase 1 should PASS, got {out.phases[0].status}"


def test_assess_readiness_does_not_modify_manifest():
    """가장 중요한 회귀: readiness check 가 manifest 를 절대 수정하면 안 됨."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        manifest = _write_manifest(tmp)
        before = manifest.read_text(encoding="utf-8")
        _ = assess_readiness(
            manifest_path=manifest, snapshots_dir=tmp,
            snapshot_pattern="daytrade_ml_paper_trader_events_asym90_2026*.csv",
        )
        after = manifest.read_text(encoding="utf-8")
        assert before == after, "readiness check must NEVER modify manifest (operational safety)"


def test_walk_forward_handles_empty_input():
    out = _walk_forward_top_n(None)
    assert out["window_count"] == 0
    assert out["avg_return_pct"] is None


def test_walk_forward_uses_net_return_when_available():
    import pandas as pd
    rows = []
    for s in range(10):
        session = f"2026-05-{s+1:02d}"
        # net return = +0.5% top-2 each session. raw return = +1.0% (will be ignored).
        rows.append({"session_date": session, "ticker": "A", "fast_score": 90,
                     "return_pct": 1.0, "net_return_pct": 0.5, "label_int": 1})
        rows.append({"session_date": session, "ticker": "B", "fast_score": 85,
                     "return_pct": 1.0, "net_return_pct": 0.5, "label_int": 1})
        rows.append({"session_date": session, "ticker": "C", "fast_score": 50,
                     "return_pct": 5.0, "net_return_pct": -2.0, "label_int": 0})
    df = pd.DataFrame(rows)
    out = _walk_forward_top_n(df, n=2, window=7)
    # 10 sessions → 4 rolling windows of size 7
    assert out["window_count"] == 4
    assert out["positive_window_count"] == 4
    assert abs(out["avg_return_pct"] - 0.5) < 1e-9


def test_walk_forward_negative_when_net_negative():
    import pandas as pd
    rows = []
    for s in range(10):
        session = f"2026-05-{s+1:02d}"
        rows.append({"session_date": session, "ticker": "A", "fast_score": 90,
                     "return_pct": -1.0, "net_return_pct": -0.5, "label_int": 0})
        rows.append({"session_date": session, "ticker": "B", "fast_score": 85,
                     "return_pct": -1.0, "net_return_pct": -0.5, "label_int": 0})
    df = pd.DataFrame(rows)
    out = _walk_forward_top_n(df, n=2, window=7)
    assert out["window_count"] == 4
    assert out["positive_window_count"] == 0
    assert out["avg_return_pct"] is not None and out["avg_return_pct"] < 0


def test_walk_forward_returns_zero_windows_when_below_window_size():
    import pandas as pd
    rows = []
    for s in range(5):   # 5 sessions, window=7 → 0 rolling windows
        session = f"2026-05-{s+1:02d}"
        rows.append({"session_date": session, "ticker": "A", "fast_score": 90,
                     "return_pct": 1.0, "net_return_pct": 0.5, "label_int": 1})
    df = pd.DataFrame(rows)
    out = _walk_forward_top_n(df, n=2, window=7)
    assert out["window_count"] == 0
    assert out["avg_return_pct"] is None


def test_threshold_sensitivity_flags_overfit_when_drift_large():
    import pandas as pd
    # base=0.55, ev_at_base ≈ 0.30; ev_at_minus(0.50) much smaller; ev_at_plus(0.60) huge.
    # 인위적 drift > 50%.
    rows = []
    # bucket near 0.50 — many losing rows
    rows += [{"model_confidence": 0.51, "net_return_pct": -1.0} for _ in range(20)]
    # bucket near 0.55 — slight positive
    rows += [{"model_confidence": 0.56, "net_return_pct": 0.30} for _ in range(20)]
    # bucket near 0.60 — strong positive
    rows += [{"model_confidence": 0.61, "net_return_pct": 1.50} for _ in range(20)]
    df = pd.DataFrame(rows)
    out = _threshold_sensitivity(df, base_threshold=0.55, delta=0.05, drift_warn_pct=50.0)
    assert out["score_col"] == "model_confidence"
    assert out["ev_at_minus"] is not None
    assert out["ev_at_base"] is not None
    assert out["ev_at_plus"] is not None
    assert out["max_drift_pct"] is not None
    assert out["overfit_warning"] is True


def test_threshold_sensitivity_no_warning_when_stable():
    import pandas as pd
    # 모든 bucket EV 비슷 → drift 작음 → no warning.
    rows = []
    rows += [{"model_confidence": 0.51, "net_return_pct": 0.50} for _ in range(20)]
    rows += [{"model_confidence": 0.56, "net_return_pct": 0.55} for _ in range(20)]
    rows += [{"model_confidence": 0.61, "net_return_pct": 0.60} for _ in range(20)]
    df = pd.DataFrame(rows)
    out = _threshold_sensitivity(df, base_threshold=0.55, delta=0.05, drift_warn_pct=50.0)
    assert out["overfit_warning"] is False


def test_threshold_sensitivity_handles_missing_score_col():
    import pandas as pd
    df = pd.DataFrame([{"return_pct": 1.0}, {"return_pct": -1.0}])
    out = _threshold_sensitivity(df)
    assert out["score_col"] is None
    assert out["overfit_warning"] is False


def test_assess_readiness_returns_versioned_report():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        manifest = _write_manifest(tmp)
        out = assess_readiness(
            manifest_path=manifest, snapshots_dir=tmp,
            snapshot_pattern="daytrade_ml_paper_trader_events_asym90_2026*.csv",
        )
        assert out.version == READINESS_VERSION
        assert out.version.startswith("ml_promotion_readiness_v")
