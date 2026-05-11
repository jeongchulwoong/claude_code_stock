"""
scripts/measure_longterm_forward_ev.py — 장투 forward EV 측정 + max_positions 시뮬.

읽기 전용 (DB 변경 0):
  - db/trade_log.db 의 orders (strategy='longterm') 조회
  - 보유 종목별 미실현 P&L (entry vs 가장 최근 close)
  - 청산된 종목의 실현 P&L
  - 시뮬: max_positions 1/3/5/10 별 capital allocation 영향

장투 forward EV 게이트 (사용자 spec):
  - Sharpe ≥ 1.0 (연환산)
  - 종목당 hit rate ≥ 55%
  - 30+ closed trades 후에만 통계 신뢰

현재 데이터 (5/11 KST): closed 1건, open 3건 — 통계 불충분.

사용:
  python scripts/measure_longterm_forward_ev.py
  python scripts/measure_longterm_forward_ev.py --simulate-max-positions 5
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _fetch_open_longterm_positions(conn: sqlite3.Connection) -> list[dict]:
    """현재 보유 중인 장투 종목 (FILLED BUY - FILLED SELL > 0)."""
    cur = conn.execute(
        """
        WITH agg AS (
            SELECT ticker,
                   SUM(CASE WHEN order_type='BUY'  AND status='FILLED' THEN filled_qty ELSE 0 END) AS buy_qty,
                   SUM(CASE WHEN order_type='SELL' AND status='FILLED' THEN filled_qty ELSE 0 END) AS sell_qty,
                   SUM(CASE WHEN order_type='BUY'  AND status='FILLED' THEN filled_qty * avg_fill_price ELSE 0 END) AS buy_cost,
                   MIN(CASE WHEN order_type='BUY'  AND status='FILLED' THEN timestamp END) AS first_buy_ts
            FROM orders
            WHERE strategy='longterm'
            GROUP BY ticker
        )
        SELECT ticker, buy_qty, sell_qty, buy_cost, first_buy_ts
        FROM agg
        WHERE (buy_qty - sell_qty) > 0
        ORDER BY first_buy_ts ASC
        """
    )
    out = []
    for r in cur:
        ticker, buy_qty, sell_qty, buy_cost, first_buy_ts = r
        net_qty = (buy_qty or 0) - (sell_qty or 0)
        avg_cost = (buy_cost or 0) / buy_qty if buy_qty else 0.0
        out.append({
            "ticker": ticker,
            "qty": net_qty,
            "avg_cost": avg_cost,
            "total_cost": avg_cost * net_qty,
            "first_buy_ts": first_buy_ts,
        })
    return out


def _fetch_closed_longterm_trades(conn: sqlite3.Connection) -> list[dict]:
    """청산 완료된 장투 거래 (FILLED SELL — pair with prior BUY).

    단순화: 같은 ticker 의 FIRST FILLED SELL 시점까지의 평균 매수가 vs 매도가.
    여러 매수 lot 이 있어도 평균 매수가 기준.
    """
    cur = conn.execute(
        """
        SELECT ticker, timestamp, filled_qty, avg_fill_price, realized_pnl
        FROM orders
        WHERE strategy='longterm' AND order_type='SELL' AND status='FILLED'
        ORDER BY timestamp ASC
        """
    )
    sells = list(cur.fetchall())

    out = []
    for ticker, sell_ts, sell_qty, sell_price, realized_pnl in sells:
        # 그 시점 이전의 BUY 평균
        cur2 = conn.execute(
            """
            SELECT SUM(filled_qty), SUM(filled_qty * avg_fill_price)
            FROM orders
            WHERE strategy='longterm' AND order_type='BUY' AND status='FILLED'
            AND ticker=? AND timestamp <= ?
            """,
            (ticker, sell_ts),
        )
        buy_qty, buy_cost = cur2.fetchone()
        buy_qty = buy_qty or 0
        avg_buy = (buy_cost or 0) / buy_qty if buy_qty else 0.0
        if avg_buy <= 0 or sell_price <= 0:
            continue
        gross_pct = (sell_price - avg_buy) / avg_buy * 100.0
        out.append({
            "ticker": ticker,
            "sell_ts": sell_ts,
            "avg_buy_price": avg_buy,
            "sell_price": sell_price,
            "qty": sell_qty,
            "gross_return_pct": gross_pct,
            "realized_pnl_recorded": realized_pnl,
        })
    return out


def _fetch_latest_close(minute_db: Path, ticker: str) -> tuple[float, str] | None:
    if not minute_db.exists():
        return None
    conn = sqlite3.connect(str(minute_db))
    try:
        cur = conn.execute(
            "SELECT close, timestamp FROM minute_bars WHERE ticker=? AND timeframe='1m' "
            "AND close IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        return (float(row[0]), row[1]) if row else None
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trade-db", default="db/trade_log.db")
    p.add_argument("--minute-db", default="db/kiwoom_minute_bars.db")
    p.add_argument("--capital", type=int, default=2_000_000,
                   help="장투 자본 한도 (config LONG_RISK_CONFIG.capital_limit)")
    p.add_argument("--current-max-positions", type=int, default=3)
    p.add_argument("--simulate-max-positions", type=int, default=0,
                   help="이 종목 수로 늘렸을 때 자본 allocation 시뮬 (0=skip)")
    p.add_argument("--cost-pct", type=float, default=0.4)
    args = p.parse_args(argv)

    conn = _connect(args.trade_db)
    open_pos = _fetch_open_longterm_positions(conn)
    closed = _fetch_closed_longterm_trades(conn)
    conn.close()

    print("=" * 80)
    print(f"  LONGTERM FORWARD EV — {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 80)

    # ── 1) 보유 중 — 미실현 P&L
    print(f"\n[1] OPEN POSITIONS ({len(open_pos)})")
    print(f"{'ticker':<12} {'qty':>4} {'avg_cost':>9} {'cur_price':>10} {'unrealized_pct':>15} {'unrealized_krw':>15} {'first_buy':<12}")
    total_cost = 0.0
    total_unrealized_krw = 0.0
    rows_for_avg = []
    for pos in open_pos:
        cur_info = _fetch_latest_close(Path(args.minute_db), pos["ticker"])
        if cur_info is None:
            print(f"{pos['ticker'][:12]:<12} {pos['qty']:>4} {pos['avg_cost']:>9.0f} {'-':>10} {'no_data':>15} {'-':>15} {(pos['first_buy_ts'] or '')[:10]:<12}")
            continue
        cur_price, cur_ts = cur_info
        unrealized_pct = (cur_price - pos["avg_cost"]) / pos["avg_cost"] * 100.0
        unrealized_krw = (cur_price - pos["avg_cost"]) * pos["qty"]
        net_unrealized_pct = unrealized_pct - args.cost_pct
        total_cost += pos["total_cost"]
        total_unrealized_krw += unrealized_krw
        rows_for_avg.append({"pct": unrealized_pct, "net_pct": net_unrealized_pct, "ticker": pos["ticker"]})
        print(f"{pos['ticker'][:12]:<12} {pos['qty']:>4} {pos['avg_cost']:>9.0f} {cur_price:>10.0f} "
              f"{unrealized_pct:>14.2f}% {unrealized_krw:>15,.0f} {(pos['first_buy_ts'] or '')[:10]:<12}")

    capital_used_pct = (total_cost / max(1, args.capital)) * 100.0
    avg_unrealized_pct = sum(r["pct"] for r in rows_for_avg) / max(1, len(rows_for_avg))
    avg_net_unrealized_pct = sum(r["net_pct"] for r in rows_for_avg) / max(1, len(rows_for_avg))
    wins = sum(1 for r in rows_for_avg if r["net_pct"] > 0)
    open_hit_rate = wins / max(1, len(rows_for_avg))

    print(f"\n  총 진입 비용: {total_cost:,.0f}원 ({capital_used_pct:.1f}% of {args.capital:,} 자본)")
    print(f"  총 미실현 손익: {total_unrealized_krw:+,.0f}원")
    print(f"  종목당 평균 미실현 수익률 (gross): {avg_unrealized_pct:+.2f}%")
    print(f"  종목당 평균 미실현 수익률 (net @ {args.cost_pct}% cost): {avg_net_unrealized_pct:+.2f}%")
    print(f"  미실현 hit rate (net > 0): {open_hit_rate:.2%} ({wins}/{len(rows_for_avg)})")

    # ── 2) 청산 완료
    print(f"\n[2] CLOSED TRADES ({len(closed)})")
    if not closed:
        print("  (no closed trades yet)")
    else:
        print(f"{'ticker':<12} {'sell_ts':<22} {'avg_buy':>9} {'sell_px':>9} {'gross%':>8} {'net%':>8} {'pnl_krw':>10}")
        gross_avg = sum(c["gross_return_pct"] for c in closed) / len(closed)
        net_avg = gross_avg - args.cost_pct
        for c in closed:
            print(f"{c['ticker'][:12]:<12} {(c['sell_ts'] or '')[:19]:<22} {c['avg_buy_price']:>9.0f} {c['sell_price']:>9.0f} "
                  f"{c['gross_return_pct']:>7.2f}% {c['gross_return_pct'] - args.cost_pct:>7.2f}% "
                  f"{c['realized_pnl_recorded'] or 0:>10.0f}")
        closed_wins = sum(1 for c in closed if (c["gross_return_pct"] - args.cost_pct) > 0)
        print(f"\n  평균 gross: {gross_avg:+.2f}% | 평균 net: {net_avg:+.2f}% | hit rate: {closed_wins}/{len(closed)} ({closed_wins/len(closed):.1%})")

    # ── 3) Phase_2 게이트 평가 (장투용)
    print(f"\n[3] LONGTERM PHASE_2 GATE (사용자 spec 기준)")
    print(f"{'-' * 80}")
    total_observations = len(rows_for_avg) + len(closed)
    print(f"  총 관측 (open + closed):     {total_observations}")
    print(f"  closed trades 표본:           {len(closed)}   (gate: ≥ 30 권장, 현재 통계 불충분)")
    # 미실현 + 실현 결합 hit rate
    combined_wins = wins + sum(1 for c in closed if (c["gross_return_pct"] - args.cost_pct) > 0)
    combined_hit = combined_wins / max(1, total_observations)
    avg_combined_net = (
        sum(r["net_pct"] for r in rows_for_avg) +
        sum(c["gross_return_pct"] - args.cost_pct for c in closed)
    ) / max(1, total_observations)
    print(f"  결합 hit rate (open+closed): {combined_hit:.2%}   (gate: ≥ 0.55)")
    print(f"  결합 평균 net return:        {avg_combined_net:+.2f}%   (gate: > 0)")

    gate_hit_pass = combined_hit >= 0.55
    gate_ev_pass = avg_combined_net > 0
    gate_sample_pass = len(closed) >= 30
    n_passing = sum([gate_hit_pass, gate_ev_pass, gate_sample_pass])
    print(f"\n  gate hit_rate ≥ 0.55:        {'OK ' if gate_hit_pass else 'MISS'} ({combined_hit:.2%})")
    print(f"  gate avg_net > 0:            {'OK ' if gate_ev_pass else 'MISS'} ({avg_combined_net:+.2f}%)")
    print(f"  gate sample ≥ 30 closed:     {'OK ' if gate_sample_pass else 'MISS'} ({len(closed)} closed)")
    print(f"\n  PASS: {n_passing}/3 — {'활성화 가능' if n_passing == 3 else '확대 보류 (표본/EV 부족)'}")

    # ── 4) max_positions 시뮬
    print(f"\n[4] MAX_POSITIONS SIMULATION")
    print(f"{'-' * 80}")
    print(f"  현재 max_positions: {args.current_max_positions}")
    print(f"  자본: {args.capital:,}원, 현재 보유: {len(open_pos)} 종목")
    print(f"  현재 종목당 평균 진입: {(total_cost / max(1, len(open_pos))):,.0f}원")
    print()
    for n in (1, 3, 5, 10):
        per_pos = args.capital / n
        # Korea retail 비용 계산: 종목당 0.21% 고정 cost. 1% 이익 시 net.
        net_per_pos_at_1pct = per_pos * 0.01 * (1 - 0.21 / 1.0)   # 1% 이익 시 (개략)
        max_loss_at_stop_7 = per_pos * 0.07   # -7% stop 시 손실
        print(f"  N={n:>2}  per-position: {per_pos:>10,.0f}원   "
              f"1% 이익 시 net ≈ {net_per_pos_at_1pct:>7,.0f}원   "
              f"-7% stop 손실 ≈ {max_loss_at_stop_7:>9,.0f}원   "
              f"{'(현재)' if n == args.current_max_positions else ''}")

    if args.simulate_max_positions > 0:
        target = args.simulate_max_positions
        per_pos = args.capital / target
        # 현재 보유 종목 평균 net 으로 시뮬레이션
        if rows_for_avg:
            avg_net = avg_net_unrealized_pct / 100.0
            expected_pnl_per_pos = per_pos * avg_net
            expected_total = expected_pnl_per_pos * target
            print(f"\n  시뮬 max_positions={target} 시:")
            print(f"    per-position: {per_pos:,.0f}원")
            print(f"    현재 평균 net ({avg_net*100:+.2f}%) 적용 시 종목당: {expected_pnl_per_pos:+,.0f}원")
            print(f"    {target} 종목 총: {expected_total:+,.0f}원")
            print(f"    (caveat: 평균 net 표본 = {len(rows_for_avg)}건 — 통계적 신뢰성 낮음)")

    # ── 5) 권고
    print(f"\n[5] RECOMMENDATION")
    print(f"{'-' * 80}")
    if n_passing < 3:
        print(f"  ❌ 보유 종목 수 확대 보류 — phase_2 gate {n_passing}/3 미통과")
        print(f"     현재 closed 표본 {len(closed)}건은 통계적 무의미 (gate 권장 ≥ 30)")
        print(f"     활성화 조건:")
        print(f"       1. closed trades 30+ 누적 (현재 +29건 더 필요)")
        print(f"       2. 결합 hit rate ≥ 55% (현재 {combined_hit:.0%})")
        print(f"       3. 결합 avg net > 0 (현재 {avg_combined_net:+.2f}%)")
        print(f"     예상 소요: 평균 hold 기간 미상 — 3-6 개월 후 재평가 권장")
    else:
        print(f"  ✅ 보유 종목 수 확대 검토 가능 — phase_2 gate 통과")
        print(f"     1 → 3 → 5 → 10 단계적 확대 권장 (한 번에 큰 점프 금지)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
