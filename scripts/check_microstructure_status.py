"""
scripts/check_microstructure_status.py — microstructure collector 건강 상태 확인.

읽기 전용 — DB 변경 0건.
보고 항목:
  - 마지막 수신 시각 (얼마 전인지)
  - 최근 24h 종목 수, 이벤트 수
  - 최근 7세션 일별 종목/이벤트
  - 학습 dataset 의 169 종목 중 얼마나 커버되는지

사용:
  python scripts/check_microstructure_status.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="db/kiwoom_microstructure_events.db")
    p.add_argument("--target-watch-list", default="domestic",
                   help="확인할 커버리지 기준 (priority/watch/domestic). 기본 domestic.")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[error] microstructure DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1) 최근 수신 시각
    row = cur.execute(
        "SELECT MAX(received_at), COUNT(*), COUNT(DISTINCT ticker) FROM microstructure_events"
    ).fetchone()
    last_received, total_events, total_tickers = row or (None, 0, 0)
    print("=" * 70)
    print(f"  MICROSTRUCTURE COLLECTOR STATUS — {db_path}")
    print("=" * 70)
    print(f"  total events: {total_events:,}")
    print(f"  unique tickers (all-time): {total_tickers}")
    print(f"  last received: {last_received}")

    if last_received:
        try:
            from datetime import datetime as _dt
            last_dt = _dt.fromisoformat(last_received)
            now = _dt.now(last_dt.tzinfo) if last_dt.tzinfo else _dt.now()
            delta = now - last_dt
            mins = int(delta.total_seconds() // 60)
            if mins < 5:
                status = "ACTIVE (last receipt < 5 min)"
            elif mins < 60:
                status = f"IDLE ({mins} min stale)"
            elif mins < 60 * 24:
                status = f"STALE ({mins // 60} hours)"
            else:
                status = f"DEAD ({delta.days} days stale)"
            print(f"  status: {status}")
        except Exception as exc:
            print(f"  status: PARSE_ERROR {exc}")

    # 2) 최근 7세션
    print(f"\n  recent 7 sessions:")
    rows = cur.execute(
        """
        SELECT substr(received_at, 1, 10) AS session,
               COUNT(DISTINCT ticker), COUNT(*),
               MIN(substr(received_at, 12, 5)) AS first_event,
               MAX(substr(received_at, 12, 5)) AS last_event
        FROM microstructure_events
        WHERE received_at >= ?
        GROUP BY session
        ORDER BY session DESC
        LIMIT 7
        """,
        ((datetime.now() - timedelta(days=14)).isoformat(),),
    ).fetchall()
    print(f"  {'session':<12} {'tickers':>8} {'events':>10} {'first':>7} {'last':>7}")
    print(f"  {'-'*12} {'-'*8} {'-'*10} {'-'*7} {'-'*7}")
    for s, t, c, f, lst in rows:
        print(f"  {s:<12} {t:>8} {c:>10,} {f or '-':>7} {lst or '-':>7}")

    # 3) 학습 dataset universe 커버리지
    try:
        from scripts.collect_kiwoom_minute_bars import resolve_tickers
        target_tickers = resolve_tickers(None, args.target_watch_list, max_tickers=300)
        target_codes = {str(t).replace(".KS", "").replace(".KQ", "").strip() for t in target_tickers}

        # 최근 24h 동안 수신된 ticker 목록
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recent_rows = cur.execute(
            "SELECT DISTINCT ticker FROM microstructure_events WHERE received_at >= ?",
            (cutoff,),
        ).fetchall()
        recent_tickers = {str(r[0]).strip() for r in recent_rows}
        covered = target_codes & recent_tickers
        missing = target_codes - recent_tickers

        print(f"\n  coverage vs '{args.target_watch_list}' watch_list ({len(target_codes)} tickers):")
        print(f"    covered (last 24h): {len(covered)} ({len(covered)/max(1,len(target_codes))*100:.1f}%)")
        print(f"    missing:            {len(missing)}")
        if missing and len(missing) <= 20:
            print(f"    missing sample: {sorted(list(missing))[:10]}")
    except Exception as exc:
        print(f"\n  [coverage check skipped: {exc}]")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
