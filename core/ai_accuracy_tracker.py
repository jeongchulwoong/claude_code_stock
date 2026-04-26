"""
core/ai_accuracy_tracker.py — AI 신뢰도 vs 실제 결과 추적

매수 시점에 AI 점수/판단 기록 → N일 후 결과(승/패 + 수익률) 매핑.
이걸로 "AI 신뢰도 80+ 의 실제 승률" 같은 검증 가능.

30년차 퀀트가 가장 먼저 만드는 인프라.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger

from config import DB_PATH


@dataclass
class AISignalRecord:
    ticker:        str
    name:          str
    entry_at:      str        # ISO datetime
    entry_price:   float
    ai_action:     str        # BUY/SELL/HOLD
    ai_confidence: float      # 0~100
    ai_reason:     str
    setup_type:    str
    composite:     float      # 통합 점수
    tech_score:    float
    fund_passed:   bool
    regime:        str
    # 결과 (체결/청산 시 채움)
    exit_at:       Optional[str]   = None
    exit_price:    Optional[float] = None
    pnl_pct:       Optional[float] = None
    won:           Optional[bool]  = None
    holding_min:   Optional[int]   = None    # 보유 분


class AIAccuracyTracker:
    """AI 판단 → 실제 결과 매핑 + 승률 통계."""

    def __init__(self) -> None:
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS ai_signals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker        TEXT,
                    name          TEXT,
                    entry_at      TEXT,
                    entry_price   REAL,
                    ai_action     TEXT,
                    ai_confidence REAL,
                    ai_reason     TEXT,
                    setup_type    TEXT,
                    composite     REAL,
                    tech_score    REAL,
                    fund_passed   INTEGER,
                    regime        TEXT,
                    exit_at       TEXT,
                    exit_price    REAL,
                    pnl_pct       REAL,
                    won           INTEGER,
                    holding_min   INTEGER
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_signals_ticker  ON ai_signals(ticker)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_signals_action  ON ai_signals(ai_action)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_signals_entry   ON ai_signals(entry_at)")

    def record_entry(self, rec: AISignalRecord) -> int:
        """매수 시 AI 판단 기록. 반환: row id (나중에 결과 업데이트용)"""
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute(
                """INSERT INTO ai_signals
                   (ticker,name,entry_at,entry_price,ai_action,ai_confidence,ai_reason,
                    setup_type,composite,tech_score,fund_passed,regime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.ticker, rec.name, rec.entry_at, rec.entry_price,
                 rec.ai_action, rec.ai_confidence, rec.ai_reason,
                 rec.setup_type, rec.composite, rec.tech_score,
                 int(rec.fund_passed), rec.regime),
            )
            return cur.lastrowid

    def record_exit(self, ticker: str, exit_price: float) -> bool:
        """청산 시 가장 최근 미완료 entry 에 결과 기록."""
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute(
                """SELECT id, entry_at, entry_price FROM ai_signals
                   WHERE ticker=? AND exit_at IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (ticker,),
            ).fetchone()
            if not row:
                return False
            rid, entry_at, entry_price = row
            try:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            except Exception:
                pnl_pct = 0.0
            won = pnl_pct > 0
            try:
                hold_min = int((datetime.now() - datetime.fromisoformat(entry_at)).total_seconds() / 60)
            except Exception:
                hold_min = 0
            con.execute(
                """UPDATE ai_signals SET exit_at=?, exit_price=?, pnl_pct=?, won=?, holding_min=?
                   WHERE id=?""",
                (datetime.now().isoformat(), exit_price, round(pnl_pct, 3),
                 int(won), hold_min, rid),
            )
            logger.info("AI 결과 기록 [{}]: {:+.2f}% (보유 {}분, {})",
                        ticker, pnl_pct, hold_min, "승" if won else "패")
            return True

    # ── 승률 통계 ─────────────────────────

    def stats_by_confidence_bucket(self) -> list[dict]:
        """AI 신뢰도 구간별 승률·평균손익."""
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                """SELECT
                     CASE
                       WHEN ai_confidence >= 90 THEN '90+'
                       WHEN ai_confidence >= 80 THEN '80~89'
                       WHEN ai_confidence >= 70 THEN '70~79'
                       WHEN ai_confidence >= 60 THEN '60~69'
                       ELSE '<60'
                     END AS bucket,
                     COUNT(*) as n,
                     AVG(CAST(won AS REAL)) as winrate,
                     AVG(pnl_pct) as avg_pnl,
                     AVG(holding_min) as avg_hold
                   FROM ai_signals
                   WHERE won IS NOT NULL
                   GROUP BY bucket
                   ORDER BY bucket DESC"""
            ).fetchall()
        return [
            {"bucket": r[0], "n": r[1], "winrate": round(r[2] or 0, 3),
             "avg_pnl": round(r[3] or 0, 2), "avg_hold_min": int(r[4] or 0)}
            for r in rows
        ]

    def stats_by_setup(self) -> list[dict]:
        """셋업 패턴별 승률·평균손익."""
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                """SELECT setup_type, COUNT(*), AVG(CAST(won AS REAL)),
                          AVG(pnl_pct), AVG(holding_min)
                   FROM ai_signals
                   WHERE won IS NOT NULL AND setup_type IS NOT NULL AND setup_type != ''
                   GROUP BY setup_type
                   ORDER BY AVG(pnl_pct) DESC"""
            ).fetchall()
        return [
            {"setup": r[0], "n": r[1], "winrate": round(r[2] or 0, 3),
             "avg_pnl": round(r[3] or 0, 2), "avg_hold_min": int(r[4] or 0)}
            for r in rows
        ]

    def overall_stats(self) -> dict:
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute(
                """SELECT COUNT(*), AVG(CAST(won AS REAL)),
                          AVG(pnl_pct), AVG(holding_min)
                   FROM ai_signals WHERE won IS NOT NULL"""
            ).fetchone()
        n, wr, pnl, hold = row or (0, 0, 0, 0)
        return {
            "total_trades": n or 0,
            "winrate":      round(wr or 0, 3),
            "avg_pnl_pct":  round(pnl or 0, 2),
            "avg_hold_min": int(hold or 0),
        }
