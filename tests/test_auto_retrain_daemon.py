"""tests/test_auto_retrain_daemon.py — readiness + cooldown 단위 테스트.

broker / order_manager / risk_manager / 운영 모델 / 운영 manifest 를 import 하지 않는다.
실제 subprocess / 학습 호출 없음 — 의사결정 로직만 검증.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.auto_retrain_daemon import (   # noqa: E402
    POST_CLOSE_END_HHMM, POST_CLOSE_START_HHMM,
    check_microstructure_readiness, cooldown_satisfied,
    evaluate_readiness, is_post_close_window,
)

KST = ZoneInfo("Asia/Seoul")


def _kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# ──────────────────────────────────────────────────────────────────
# cooldown
# ──────────────────────────────────────────────────────────────────

def test_cooldown_satisfied_when_no_prior_run():
    """이전 학습 없음 → cooldown 즉시 통과."""
    assert cooldown_satisfied(None, 7, _kst(2026, 5, 12, 17, 0)) is True


def test_cooldown_not_satisfied_within_window():
    """5/12 시점에서 5/10 학습 → cooldown 7일 미충족."""
    last = "2026-05-10T17:00:00+09:00"
    assert cooldown_satisfied(last, 7, _kst(2026, 5, 12, 17, 0)) is False


def test_cooldown_satisfied_after_window():
    """5/19 시점에서 5/12 학습 → 정확히 7일 후 → 통과."""
    last = "2026-05-12T17:00:00+09:00"
    assert cooldown_satisfied(last, 7, _kst(2026, 5, 19, 17, 0)) is True


def test_cooldown_treats_invalid_timestamp_as_pass():
    """깨진 timestamp → 안전하게 cooldown 통과로 간주."""
    assert cooldown_satisfied("not-a-date", 7, _kst(2026, 5, 12, 17, 0)) is True


# ──────────────────────────────────────────────────────────────────
# post-close window
# ──────────────────────────────────────────────────────────────────

def test_post_close_window_start():
    # 16:00 정확히 — True (시작)
    assert is_post_close_window(_kst(2026, 5, 12, 16, 0)) is True


def test_post_close_window_end():
    # 23:30 정확히 — True (끝, inclusive)
    assert is_post_close_window(_kst(2026, 5, 12, 23, 30)) is True


def test_post_close_window_before_close():
    # 15:00 (장 중) — False
    assert is_post_close_window(_kst(2026, 5, 12, 15, 0)) is False


def test_post_close_window_late_night():
    # 23:31 — False
    assert is_post_close_window(_kst(2026, 5, 12, 23, 31)) is False


def test_post_close_window_weekend_blocked():
    # 토 17:00 — False
    assert is_post_close_window(_kst(2026, 5, 16, 17, 0)) is False


def test_post_close_window_constants():
    assert POST_CLOSE_START_HHMM == (16, 0)
    assert POST_CLOSE_END_HHMM == (23, 30)


# ──────────────────────────────────────────────────────────────────
# readiness 검사 (microstructure DB)
# ──────────────────────────────────────────────────────────────────

def _make_microstructure_db(tmp_path: pathlib.Path, sessions_data: list[tuple[str, int]]) -> pathlib.Path:
    """fixture DB. sessions_data: [(session_date, n_tickers), ...]"""
    db = tmp_path / "microstructure.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE microstructure_events (
            ticker TEXT, received_at TEXT
        )
    """)
    for session, n_tickers in sessions_data:
        for i in range(n_tickers):
            conn.execute(
                "INSERT INTO microstructure_events (ticker, received_at) VALUES (?, ?)",
                (f"T{i:04d}", f"{session}T09:00:00+09:00"),
            )
    conn.commit()
    conn.close()
    return db


def test_microstructure_readiness_qualifying_sessions(tmp_path):
    # 3개 세션 — 2개는 ≥150 tickers, 1개는 < 150
    db = _make_microstructure_db(tmp_path, [
        ("2026-05-12", 169),   # 통과
        ("2026-05-11", 30),    # 미달
        ("2026-05-10", 160),   # 통과
    ])
    qualifying, details = check_microstructure_readiness(
        minute_db=db, min_tickers_per_session=150,
    )
    assert qualifying == 2
    assert len(details) == 3
    by_session = {d["session"]: d for d in details}
    assert by_session["2026-05-12"]["qualifies"] is True
    assert by_session["2026-05-11"]["qualifies"] is False


def test_microstructure_readiness_empty_db(tmp_path):
    db = _make_microstructure_db(tmp_path, [])
    qualifying, details = check_microstructure_readiness(minute_db=db)
    assert qualifying == 0
    assert details == []


def test_microstructure_readiness_missing_db(tmp_path):
    db = tmp_path / "does_not_exist.db"
    qualifying, details = check_microstructure_readiness(minute_db=db)
    assert qualifying == 0
    assert details == []


# ──────────────────────────────────────────────────────────────────
# evaluate_readiness 통합
# ──────────────────────────────────────────────────────────────────

def test_evaluate_ready_when_all_conditions_met(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 13)])
    state = {}   # no prior run
    out = evaluate_readiness(
        minute_db=db, state=state,
        now_kst=_kst(2026, 5, 13, 17, 0),
        min_sessions=10, min_tickers_per_session=150,
        cooldown_days=7,
    )
    assert out.ready is True
    assert out.reason == "ready"
    assert out.qualifying_sessions >= 10


def test_evaluate_not_ready_insufficient_sessions(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 6)])  # 5 sessions only
    out = evaluate_readiness(
        minute_db=db, state={},
        now_kst=_kst(2026, 5, 13, 17, 0),
        min_sessions=10, min_tickers_per_session=150,
    )
    assert out.ready is False
    assert "insufficient_sessions" in out.reason


def test_evaluate_not_ready_cooldown_active(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 13)])
    state = {"last_run_timestamp": "2026-05-10T17:00:00+09:00"}
    out = evaluate_readiness(
        minute_db=db, state=state,
        now_kst=_kst(2026, 5, 12, 17, 0),
        cooldown_days=7,
    )
    assert out.ready is False
    assert "cooldown_active" in out.reason


def test_evaluate_not_ready_outside_post_close_window(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 13)])
    out = evaluate_readiness(
        minute_db=db, state={},
        now_kst=_kst(2026, 5, 13, 11, 0),   # 11:00 — 장 중
    )
    assert out.ready is False
    assert "outside_post_close_window" in out.reason


def test_evaluate_ignore_time_window_for_test(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 13)])
    out = evaluate_readiness(
        minute_db=db, state={},
        now_kst=_kst(2026, 5, 13, 11, 0),   # 11:00 — 장 중
        enforce_time_window=False,
    )
    assert out.ready is True   # time window 무시


def test_evaluate_blocks_on_weekend(tmp_path):
    db = _make_microstructure_db(tmp_path,
                                   [(f"2026-05-{i:02d}", 169) for i in range(1, 13)])
    out = evaluate_readiness(
        minute_db=db, state={},
        now_kst=_kst(2026, 5, 16, 17, 0),   # 토
    )
    assert out.ready is False
    assert "outside_post_close_window" in out.reason
