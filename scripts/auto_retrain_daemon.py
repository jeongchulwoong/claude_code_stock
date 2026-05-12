"""
scripts/auto_retrain_daemon.py — 자동 재학습 daemon (paper-only research).

목적:
  - 매 영업일 장 마감 후 (16:00 KST) microstructure 누적 readiness 점검
  - 조건 만족 시 dataset rebuild + iterZB 레시피 학습 + backtest 자동 실행
  - 결과는 models/research/dl_auto_*, reports/ml/research/auto_retrain_* 로 저장
  - **매니페스트 operational path / DAYTRADE_ML_CONFIG 절대 수정 X**
  - 운영자 명시 결정 없이 promotion 자동 안 함

readiness 조건 (모두 만족):
  1. microstructure DB 최근 N 영업일 (default 10) 세션 모두 ≥150 unique tickers
  2. 직전 auto-retrain ≥ 7일 전 (또는 처음)
  3. 영업일 16:00 ~ 23:30 KST (장 마감 후 ~ 자정 전)

파이프라인:
  1. augment_dataset_with_ma120_slope.py (ma120_then 채우기)
  2. relabel_dataset_atr.py (ATR 1.5/0.6, cost 0.4%)
  3. join_microstructure_to_dataset.py (5/6 이후 mstruct features)
  4. train_daytrade_ml_model.py (RF iterZB recipe, ExtraTrees + time-decay)
  5. simulate_ml_plus_trailing.py (백테스트 + phase_2 gate 측정)
  6. 결과 JSON 보고서 + log

state file (db/ml/auto_retrain_state.json):
  - last_run_timestamp: 마지막 학습 시각
  - last_run_status: 'success' / 'failed' / 'skipped'
  - last_model_path: 가장 최근 모델 artifact 경로
  - last_phase_2_passes: 0~5

usage:
  python scripts/auto_retrain_daemon.py
  python scripts/auto_retrain_daemon.py --min-sessions 10 --min-tickers 150
  python scripts/auto_retrain_daemon.py --dry-run   # readiness 만 체크, 실행 X
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")

# 자동 학습 readiness 기본값
DEFAULT_MIN_SESSIONS = 10
DEFAULT_MIN_TICKERS_PER_SESSION = 150
DEFAULT_COOLDOWN_DAYS = 7

# 학습 시작 가능 시간대 (KST)
POST_CLOSE_START_HHMM = (16, 0)
POST_CLOSE_END_HHMM   = (23, 30)

STATE_FILE_DEFAULT = ROOT / "db" / "ml" / "auto_retrain_state.json"


@dataclass(frozen=True)
class ReadinessCheck:
    """readiness 진단 결과. raise 안 함."""
    ready: bool
    reason: str
    qualifying_sessions: int
    min_sessions_required: int
    last_run_at: str | None
    cooldown_satisfied: bool
    time_window_ok: bool
    details: dict = field(default_factory=dict)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def check_microstructure_readiness(
    *,
    minute_db: Path,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    min_tickers_per_session: int = DEFAULT_MIN_TICKERS_PER_SESSION,
) -> tuple[int, list[dict]]:
    """microstructure DB 의 영업일별 ticker coverage 측정.

    Returns:
      (qualifying_sessions, session_details)
      qualifying_sessions = ≥min_tickers_per_session 인 세션 수
    """
    if not minute_db.exists():
        return 0, []
    conn = sqlite3.connect(str(minute_db))
    try:
        cur = conn.execute(
            """
            SELECT substr(received_at, 1, 10) AS session,
                   COUNT(DISTINCT ticker) AS tickers,
                   COUNT(*) AS events
            FROM microstructure_events
            GROUP BY session
            ORDER BY session DESC
            """,
        )
        details = []
        qualifying = 0
        for s, t, e in cur:
            qualifies = (t or 0) >= min_tickers_per_session
            details.append({"session": s, "tickers": t, "events": e, "qualifies": qualifies})
            if qualifies:
                qualifying += 1
        return qualifying, details[:30]   # 최근 30개만 반환 (DB 크기 보호)
    finally:
        conn.close()


def is_post_close_window(now_kst: datetime) -> bool:
    """KST 16:00 ~ 23:30 사이면 True (영업일만 가정)."""
    if now_kst.weekday() >= 5:   # 주말
        return False
    start = now_kst.replace(hour=POST_CLOSE_START_HHMM[0], minute=POST_CLOSE_START_HHMM[1],
                             second=0, microsecond=0)
    end = now_kst.replace(hour=POST_CLOSE_END_HHMM[0], minute=POST_CLOSE_END_HHMM[1],
                           second=0, microsecond=0)
    return start <= now_kst <= end


def cooldown_satisfied(last_run_at: str | None, cooldown_days: int, now: datetime) -> bool:
    """마지막 학습 시각과의 간격이 cooldown_days 이상이면 True."""
    if not last_run_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run_at)
        delta = now - last_dt
        return delta >= timedelta(days=cooldown_days)
    except Exception:
        return True   # parse 실패 시 안전하게 cooldown 통과로 간주


def evaluate_readiness(
    *,
    minute_db: Path,
    state: dict,
    now_kst: datetime,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    min_tickers_per_session: int = DEFAULT_MIN_TICKERS_PER_SESSION,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    enforce_time_window: bool = True,
) -> ReadinessCheck:
    """현재 시점에서 학습 트리거 조건 통과 여부."""
    qualifying, details = check_microstructure_readiness(
        minute_db=minute_db,
        min_sessions=min_sessions,
        min_tickers_per_session=min_tickers_per_session,
    )
    last_run_at = state.get("last_run_timestamp")
    cooldown_ok = cooldown_satisfied(last_run_at, cooldown_days, now_kst)
    time_ok = (not enforce_time_window) or is_post_close_window(now_kst)

    has_enough_sessions = qualifying >= min_sessions
    ready = has_enough_sessions and cooldown_ok and time_ok

    if not has_enough_sessions:
        reason = f"insufficient_sessions: {qualifying}/{min_sessions} (need ≥{min_tickers_per_session} tickers each)"
    elif not cooldown_ok:
        reason = f"cooldown_active: last_run {last_run_at}, cooldown {cooldown_days}d"
    elif not time_ok:
        reason = f"outside_post_close_window: now {now_kst.strftime('%a %H:%M')} not in {POST_CLOSE_START_HHMM}-{POST_CLOSE_END_HHMM} KST"
    else:
        reason = "ready"

    return ReadinessCheck(
        ready=ready, reason=reason,
        qualifying_sessions=qualifying,
        min_sessions_required=min_sessions,
        last_run_at=last_run_at,
        cooldown_satisfied=cooldown_ok,
        time_window_ok=time_ok,
        details={"session_coverage_sample": details[:10]},
    )


def run_pipeline(
    *,
    timestamp_tag: str,
    base_dataset: Path,
    minute_db: Path,
    microstructure_db: Path,
    out_dir: Path,
    cost_pct: float = 0.4,
    log_path: Path,
) -> dict:
    """5단계 파이프라인을 subprocess 로 실행.

    각 단계 실패해도 raise 안 함 — 결과 dict 에 status 기록.

    Returns:
      pipeline result dict:
        steps: [{name, returncode, duration_sec, output_path}]
        final_status: 'success' / 'partial' / 'failed'
        artifact_path: 최종 모델 path (성공 시)
        report_path: backtest 보고서 path (성공 시)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    aug_path = out_dir / f"auto_aug_ma120slope_{timestamp_tag}.csv"
    relabel_path = out_dir / f"auto_atr15x06_{timestamp_tag}.csv"
    ms_path = out_dir / f"auto_atr15x06_ms_{timestamp_tag}.csv"
    model_path = ROOT / "models" / "research" / f"dl_auto_iterZB_{timestamp_tag}.joblib"
    model_report = ROOT / "reports" / "ml" / "research" / f"dl_auto_iterZB_{timestamp_tag}.json"
    backtest_report = ROOT / "reports" / "ml" / "research" / f"auto_retrain_backtest_{timestamp_tag}.json"

    steps: list[dict] = []

    def _exec(name: str, args: list[str]) -> int:
        t0 = time.monotonic()
        with open(log_path, "a", encoding="utf-8", errors="replace") as log_f:
            log_f.write(f"\n=== {name} @ {datetime.now(KST).isoformat()} ===\n")
            log_f.write(f"cmd: {' '.join(args)}\n")
            try:
                p = subprocess.run(
                    [sys.executable, "-u"] + args,
                    cwd=str(ROOT),
                    stdout=log_f, stderr=subprocess.STDOUT,
                    timeout=3600,   # 1시간 단계별 timeout
                )
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = -1
                log_f.write("[error] step timed out (>1h)\n")
            except Exception as exc:
                rc = -2
                log_f.write(f"[error] subprocess exception: {exc}\n")
        elapsed = time.monotonic() - t0
        steps.append({"name": name, "returncode": rc, "duration_sec": round(elapsed, 1)})
        return rc

    # Step 1: augment ma120_slope
    rc1 = _exec("augment_ma120_slope", [
        "scripts/augment_dataset_with_ma120_slope.py",
        "--in",  str(base_dataset),
        "--out", str(aug_path),
        "--minute-db", str(minute_db),
        "--lookback-minutes", "5",
    ])
    if rc1 != 0:
        return {"steps": steps, "final_status": "failed", "failed_at": "augment_ma120_slope"}

    # Step 2: ATR 라벨 재계산 (TP=1.5×ATR, SL=0.6×ATR, cost 0.4%)
    rc2 = _exec("relabel_atr_15x06", [
        "scripts/relabel_dataset_atr.py",
        "--in",  str(aug_path),
        "--out", str(relabel_path),
        "--minute-db", str(minute_db),
        "--tp-atr-mult", "1.5", "--sl-atr-mult", "0.6",
        "--horizon-minutes", "30", "--cost-pct", str(cost_pct),
    ])
    if rc2 != 0:
        return {"steps": steps, "final_status": "failed", "failed_at": "relabel_atr_15x06"}

    # Step 3: microstructure features join (5/6 이후만)
    rc3 = _exec("join_microstructure", [
        "scripts/join_microstructure_to_dataset.py",
        "--dataset", str(relabel_path),
        "--microstructure", str(microstructure_db) if microstructure_db.suffix == ".csv" else "",
        "--out", str(ms_path),
    ])
    # rc3 실패해도 단순 base dataset 으로 진행 (microstructure 미가용 fallback)
    train_input = ms_path if rc3 == 0 and ms_path.exists() else relabel_path

    # Step 4: 학습 (iterZB recipe: ExtraTrees + time-decay + sigmoid cal)
    rc4 = _exec("train_iterZB_recipe", [
        "scripts/train_daytrade_ml_model.py",
        "--dataset", str(train_input),
        "--model-type", "extra_trees",
        "--min-fast-score", "85",
        "--min-strategies-passed", "4",
        "--use-sample-weight", "--balance-sessions",
        "--calibration-method", "sigmoid",
        "--split-mode", "date_oos",
        "--oos-date-count", "14",
        "--embargo-minutes", "30",
        "--purge-horizon-minutes", "30",
        "--model-out",  str(model_path),
        "--report-out", str(model_report),
    ])
    if rc4 != 0:
        return {"steps": steps, "final_status": "failed", "failed_at": "train_iterZB_recipe"}

    # Step 5: 백테스트 + phase_2 게이트 측정
    rc5 = _exec("backtest_phase2_gate", [
        "scripts/simulate_ml_plus_trailing.py",
        "--dataset", str(train_input),
        "--rf", str(model_path),
        "--minute-db", str(minute_db),
        "--top-n", "2",
        "--horizon-minutes", "30",
        "--cost-pct", str(cost_pct),
        "--steps", "1.0,2.0,3.0",
        "--chandelier-atr", "1.5", "--initial-atr-mult", "1.5",
        "--oos-date-count", "14",
        "--min-fast-score", "85",
        "--min-strategies-passed", "4",
        "--json-out", str(backtest_report),
    ])

    # phase_2 결과 파싱
    phase_2_passes = 0
    phase_2_pass = False
    try:
        if backtest_report.exists():
            d = json.loads(backtest_report.read_text(encoding="utf-8"))
            phase_2 = d.get("phase_2", {})
            phase_2_passes = int(phase_2.get("passes", 0))
            phase_2_pass = bool(phase_2.get("phase_2_pass", False))
    except Exception:
        pass

    return {
        "steps": steps,
        "final_status": "success" if rc5 == 0 else "partial",
        "artifact_path": str(model_path) if model_path.exists() else None,
        "model_report_path": str(model_report) if model_report.exists() else None,
        "backtest_report_path": str(backtest_report) if backtest_report.exists() else None,
        "phase_2_passes": phase_2_passes,
        "phase_2_pass": phase_2_pass,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--minute-db", default=str(ROOT / "db" / "kiwoom_minute_bars.db"))
    p.add_argument("--microstructure-db",
                   default=str(ROOT / "db" / "kiwoom_microstructure_events.db"))
    p.add_argument("--microstructure-features-csv",
                   default=str(ROOT / "db" / "ml" / "microstructure_features_per_minute_20260511.csv"),
                   help="join_microstructure_to_dataset.py 입력 (CSV)")
    p.add_argument("--base-dataset",
                   default=str(ROOT / "db" / "ml" / "daytrade_event_dataset_kiwoom1m_domestic169_dynamic_min3_20260509_122823.csv"),
                   help="event dataset 기반 (augment 입력)")
    p.add_argument("--out-dir", default=str(ROOT / "db" / "ml"))
    p.add_argument("--state-file", default=str(STATE_FILE_DEFAULT))
    p.add_argument("--log-file", default=str(ROOT / "logs" / "auto_retrain.log"))
    p.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    p.add_argument("--min-tickers", type=int, default=DEFAULT_MIN_TICKERS_PER_SESSION)
    p.add_argument("--cooldown-days", type=int, default=DEFAULT_COOLDOWN_DAYS)
    p.add_argument("--check-interval-sec", type=int, default=300,
                   help="readiness 재체크 주기 (기본 5분).")
    p.add_argument("--dry-run", action="store_true", help="readiness 만 체크. 파이프라인 실행 X.")
    p.add_argument("--once", action="store_true", help="1회 체크 후 종료 (daemon 비활성).")
    p.add_argument("--ignore-time-window", action="store_true",
                   help="장 마감 후 시간대 조건 무시 (테스트용).")
    args = p.parse_args(argv)

    state_path = Path(args.state_file)
    log_path = Path(args.log_file)
    minute_db = Path(args.minute_db)
    base_dataset = Path(args.base_dataset)

    def _heartbeat(msg: str) -> None:
        ts = datetime.now(KST).isoformat(timespec="seconds")
        print(f"[{ts}] {msg}", flush=True)

    _heartbeat("auto-retrain daemon started")
    _heartbeat(f"  microstructure DB: {args.microstructure_db}")
    _heartbeat(f"  base dataset:     {args.base_dataset}")
    _heartbeat(f"  readiness:        ≥{args.min_sessions} sessions × ≥{args.min_tickers} tickers")
    _heartbeat(f"  cooldown:         {args.cooldown_days} days")
    _heartbeat(f"  dry_run:          {args.dry_run}, once: {args.once}")

    try:
        while True:
            now_kst = datetime.now(KST)
            state = _load_state(state_path)
            ms_db = Path(args.microstructure_db)
            readiness = evaluate_readiness(
                minute_db=ms_db,
                state=state,
                now_kst=now_kst,
                min_sessions=args.min_sessions,
                min_tickers_per_session=args.min_tickers,
                cooldown_days=args.cooldown_days,
                enforce_time_window=not args.ignore_time_window,
            )
            _heartbeat(f"readiness: ready={readiness.ready} reason='{readiness.reason}' "
                        f"qualifying_sessions={readiness.qualifying_sessions}/{readiness.min_sessions_required}")

            if readiness.ready and not args.dry_run:
                tag = now_kst.strftime("%Y%m%d_%H%M%S")
                _heartbeat(f"trigger pipeline (tag={tag})")
                result = run_pipeline(
                    timestamp_tag=tag,
                    base_dataset=base_dataset,
                    minute_db=minute_db,
                    microstructure_db=Path(args.microstructure_features_csv),
                    out_dir=Path(args.out_dir),
                    cost_pct=0.4,
                    log_path=log_path,
                )
                _heartbeat(f"pipeline done: {result.get('final_status')} "
                            f"phase_2={result.get('phase_2_passes')}/5")
                # state 갱신
                state["last_run_timestamp"] = now_kst.isoformat()
                state["last_run_status"] = result.get("final_status")
                state["last_run_result"] = result
                _save_state(state_path, state)
                if args.once:
                    return 0

            if args.once:
                return 0
            time.sleep(args.check_interval_sec)
    except KeyboardInterrupt:
        _heartbeat("daemon stopped by Ctrl+C")
        return 0
    except Exception as exc:
        _heartbeat(f"[error] daemon crashed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
