"""
core/strategy_tracker.py — 전략별 실시간 성과 추적기

각 전략의:
  - 총 신호 수 / 실행 수 / 승률
  - 평균 수익률 / 평균 보유일
  - 최대 수익 / 최대 손실 거래
  - 누적 손익 추이

DB에 저장하여 대시보드에서 조회 가능.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from loguru import logger

from config import DB_PATH


@dataclass
class StrategyStats:
    strategy_name: str
    total_signals: int     = 0
    executed:      int     = 0
    wins:          int     = 0
    losses:        int     = 0
    total_pnl:     float   = 0.0
    best_pnl:      float   = 0.0
    worst_pnl:     float   = 0.0
    avg_hold_days: float   = 0.0
    last_signal:   str     = ""

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total * 100 if total else 0.0

    @property
    def profit_factor(self) -> float:
        if self.losses == 0: return float("inf")
        if self.wins   == 0: return 0.0
        # DB에서 정확한 값 계산 필요 — 여기선 근사
        return abs(self.best_pnl / self.worst_pnl) if self.worst_pnl else 1.0

    def summary(self) -> str:
        return (
            f"[{self.strategy_name}] "
            f"신호:{self.total_signals} | 실행:{self.executed} | "
            f"승률:{self.win_rate:.1f}% | 손익:{self.total_pnl:+,.0f}원"
        )


class StrategyTracker:
    """
    전략별 신호와 실행 결과를 DB에 기록하고
    실시간 성과를 집계한다.
    """

    def __init__(self) -> None:
        self._init_db()

    # ── 신호 기록 ─────────────────────────────

    def record_signal(
        self,
        strategy:   str,
        ticker:     str,
        action:     str,          # BUY / SELL / HOLD
        confidence: int,
        price:      int,
        executed:   bool = False,
        reason:     str  = "",
    ) -> None:
        """AI 판단 신호를 기록한다."""
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO strategy_signals "
                "(signal_date, strategy, ticker, action, confidence, price, executed, reason) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    datetime.now().isoformat(),
                    strategy, ticker, action, confidence,
                    price, int(executed), reason,
                ),
            )

    def record_trade_result(
        self,
        strategy:   str,
        ticker:     str,
        pnl:        float,
        hold_days:  int,
        entry_price: int,
        exit_price:  int,
    ) -> None:
        """거래 결과(청산 후)를 기록한다."""
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO strategy_trades "
                "(trade_date, strategy, ticker, pnl, hold_days, entry_price, exit_price) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    date.today().isoformat(),
                    strategy, ticker, pnl, hold_days, entry_price, exit_price,
                ),
            )
        logger.info(
            "거래 기록 [{}] {} | 손익:{:+,.0f}원 | {}일 보유",
            strategy, ticker, pnl, hold_days,
        )

    # ── 성과 조회 ─────────────────────────────

    def get_stats(self, strategy: str = None) -> list[StrategyStats]:
        """
        전략별 성과 통계를 반환한다.
        strategy=None이면 전체 전략 목록.
        """
        with sqlite3.connect(DB_PATH) as con:
            if strategy:
                rows = con.execute(
                    "SELECT strategy FROM strategy_trades WHERE strategy=? GROUP BY strategy",
                    (strategy,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT DISTINCT strategy FROM strategy_trades"
                ).fetchall()

        results = []
        for (strat,) in rows:
            stats = self._calc_stats(strat)
            results.append(stats)

        return results

    def get_all_stats_dict(self) -> dict[str, dict]:
        """대시보드 API용 딕셔너리 반환"""
        stats_list = self.get_stats()
        return {
            s.strategy_name: {
                "signals":    s.total_signals,
                "executed":   s.executed,
                "win_rate":   round(s.win_rate, 1),
                "total_pnl":  round(s.total_pnl, 0),
                "best_pnl":   round(s.best_pnl, 0),
                "worst_pnl":  round(s.worst_pnl, 0),
                "avg_hold":   round(s.avg_hold_days, 1),
            }
            for s in stats_list
        }

    def _calc_stats(self, strategy: str) -> StrategyStats:
        with sqlite3.connect(DB_PATH) as con:
            trades = con.execute(
                "SELECT pnl, hold_days FROM strategy_trades WHERE strategy=?",
                (strategy,),
            ).fetchall()

            signals = con.execute(
                "SELECT COUNT(*), SUM(executed) FROM strategy_signals WHERE strategy=?",
                (strategy,),
            ).fetchone()

            last_sig = con.execute(
                "SELECT signal_date FROM strategy_signals WHERE strategy=? "
                "ORDER BY signal_date DESC LIMIT 1",
                (strategy,),
            ).fetchone()

        pnls       = [t[0] for t in trades]
        hold_days  = [t[1] for t in trades]
        wins       = [p for p in pnls if p > 0]
        losses     = [p for p in pnls if p <= 0]

        return StrategyStats(
            strategy_name = strategy,
            total_signals = signals[0] if signals else 0,
            executed      = int(signals[1] or 0) if signals else 0,
            wins          = len(wins),
            losses        = len(losses),
            total_pnl     = sum(pnls),
            best_pnl      = max(pnls) if pnls else 0.0,
            worst_pnl     = min(pnls) if pnls else 0.0,
            avg_hold_days = sum(hold_days)/len(hold_days) if hold_days else 0.0,
            last_signal   = last_sig[0] if last_sig else "",
        )

    # ── 터미널 출력 ───────────────────────────

    def print_leaderboard(self) -> None:
        """전략 성과 리더보드를 출력한다."""
        stats = self.get_stats()
        if not stats:
            print("  전략 기록 없음")
            return

        stats.sort(key=lambda s: -s.total_pnl)
        print("\n" + "═"*70)
        print("  🏆 전략별 성과 리더보드")
        print("─"*70)
        print(f"  {'전략':<20} {'신호':>6} {'실행':>6} {'승률':>7} {'누적손익':>14} {'최고':>12} {'평균보유':>8}")
        print("─"*70)
        for s in stats:
            print(
                f"  {s.strategy_name:<20} {s.total_signals:>6} {s.executed:>6} "
                f"{s.win_rate:>6.1f}% {s.total_pnl:>+13,.0f}원 "
                f"{s.best_pnl:>+11,.0f}원 {s.avg_hold_days:>7.1f}일"
            )
        print("═"*70 + "\n")

    # ── DB 초기화 ─────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_date TEXT,
                    strategy    TEXT,
                    ticker      TEXT,
                    action      TEXT,
                    confidence  INTEGER,
                    price       INTEGER,
                    executed    INTEGER DEFAULT 0,
                    reason      TEXT
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS strategy_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date  TEXT,
                    strategy    TEXT,
                    ticker      TEXT,
                    pnl         REAL,
                    hold_days   INTEGER,
                    entry_price INTEGER,
                    exit_price  INTEGER
                )
            """)
