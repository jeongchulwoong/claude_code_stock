"""
scripts/run_microstructure_collector_daemon.py - 장 시간 자동 가동 wrapper.

설계:
  - KST 시간 기준 영업일 09:00~15:30 만 collector 실행
  - 장 시간 외에는 대기 (sleep) - Kiwoom WS 연결 낭비 X
  - 장 중 collector 비정상 종료 시 자동 재시작 (max 5회/시간)
  - market-data only - broker/order 영향 0

사용:
  python scripts/run_microstructure_collector_daemon.py
  (영구 가동. Ctrl+C 로 종료.)

flag:
  --max-restarts-per-hour: 시간당 최대 재시작 횟수 (기본 5, 무한 루프 방지)
  --skip-weekends: 토/일 가동 안 함 (기본 True)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")

# 영업일 시장 시간 (KST)
MARKET_OPEN_HHMM  = (9, 0)
MARKET_CLOSE_HHMM = (15, 30)
WARMUP_MINUTES_BEFORE_OPEN = 5    # 08:55 부터 가동 (token + WS 연결 warmup)


def is_market_hours(now: datetime) -> bool:
    """현재 KST 가 영업일 시장 시간이면 True."""
    if now.weekday() >= 5:   # 토(5), 일(6)
        return False
    open_dt = now.replace(hour=MARKET_OPEN_HHMM[0], minute=MARKET_OPEN_HHMM[1],
                           second=0, microsecond=0)
    close_dt = now.replace(hour=MARKET_CLOSE_HHMM[0], minute=MARKET_CLOSE_HHMM[1],
                            second=0, microsecond=0)
    open_dt = open_dt - timedelta(minutes=WARMUP_MINUTES_BEFORE_OPEN)
    return open_dt <= now < close_dt


def next_market_open(now: datetime) -> datetime:
    """다음 영업일 시장 개장 시각 (08:55 KST)."""
    candidate = now.replace(hour=MARKET_OPEN_HHMM[0], minute=MARKET_OPEN_HHMM[1],
                             second=0, microsecond=0) - timedelta(minutes=WARMUP_MINUTES_BEFORE_OPEN)
    # 오늘 이미 지났으면 내일로
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    # 주말이면 월요일로
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    return candidate


def run_collector_once(args: argparse.Namespace) -> int:
    """collector 1회 실행. 종료 코드 반환."""
    cmd = [
        sys.executable, "-u",
        str(ROOT / "scripts" / "collect_kiwoom_realtime_microstructure.py"),
        "--watch-list", args.watch_list,
        "--max-tickers", str(args.max_tickers),
        "--types", args.types,
    ]
    # 자동 종료 시간 (장 종료 시각까지)
    now_kst = datetime.now(KST)
    close_dt = now_kst.replace(hour=MARKET_CLOSE_HHMM[0], minute=MARKET_CLOSE_HHMM[1],
                                 second=0, microsecond=0)
    seconds_remaining = max(60, int((close_dt - now_kst).total_seconds()))
    cmd += ["--max-seconds", str(seconds_remaining)]
    print(f"[{datetime.now(KST).isoformat()}] launching collector: {' '.join(cmd[1:])}", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT))
        return proc.returncode
    except KeyboardInterrupt:
        print("[interrupt] daemon stopping by Ctrl+C", flush=True)
        raise
    except Exception as exc:
        print(f"[error] collector subprocess error: {exc}", file=sys.stderr, flush=True)
        return 99


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-list", choices=("priority", "watch", "domestic"), default="domestic")
    parser.add_argument("--max-tickers", type=int, default=200)
    parser.add_argument("--types", default="tick,hoga,orderbook,hoga_depth")
    parser.add_argument("--max-restarts-per-hour", type=int, default=5)
    parser.add_argument("--check-interval-sec", type=int, default=60,
                        help="장 외 대기 시 sleep 간격 (default 60s).")
    args = parser.parse_args(argv)

    print(f"[{datetime.now(KST).isoformat()}] microstructure daemon started", flush=True)
    print(f"  watch_list={args.watch_list} max_tickers={args.max_tickers}", flush=True)
    print(f"  market hours: weekday {MARKET_OPEN_HHMM[0]:02d}:{MARKET_OPEN_HHMM[1]:02d} - "
          f"{MARKET_CLOSE_HHMM[0]:02d}:{MARKET_CLOSE_HHMM[1]:02d} KST", flush=True)

    restart_timestamps: list[datetime] = []

    try:
        while True:
            now = datetime.now(KST)
            if not is_market_hours(now):
                next_open = next_market_open(now)
                wait_seconds = max(args.check_interval_sec, int((next_open - now).total_seconds()))
                print(f"[{now.isoformat()}] outside market hours - sleeping until {next_open.isoformat()} "
                      f"({wait_seconds // 60} min)", flush=True)
                time.sleep(min(wait_seconds, 3600))   # 최대 1시간 sleep, 그 후 재체크
                continue

            # 최근 1시간 재시작 횟수 체크 (무한 루프 방지)
            one_hour_ago = now - timedelta(hours=1)
            restart_timestamps = [t for t in restart_timestamps if t > one_hour_ago]
            if len(restart_timestamps) >= args.max_restarts_per_hour:
                print(f"[{now.isoformat()}] max restarts ({args.max_restarts_per_hour}) "
                      f"exceeded in last hour - sleeping 5 min", flush=True)
                time.sleep(300)
                continue

            restart_timestamps.append(now)
            rc = run_collector_once(args)
            print(f"[{datetime.now(KST).isoformat()}] collector exited code={rc} "
                  f"(restart count last hour: {len(restart_timestamps)})", flush=True)

            # 짧은 backoff 후 재시작 (5초)
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"[{datetime.now(KST).isoformat()}] daemon stopped by interrupt", flush=True)
        return 0
    except Exception as exc:
        print(f"[error] daemon crashed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
