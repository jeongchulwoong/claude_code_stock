"""
dashboard/db_reader.py — SQLite 데이터 조회 모듈

orders 테이블 + 포지션 상태를 읽어 대시보드에 제공한다.
실제 거래 데이터가 없으면 데모 데이터를 생성한다.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "db" / "trade_log.db"


# ── 데모 데이터 시드 (DB가 비어있을 때) ──────────

def seed_demo_data() -> None:
    """대시보드 시연용 더미 거래 데이터를 삽입한다."""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT, timestamp TEXT, ticker TEXT,
                order_type TEXT, qty INTEGER, price INTEGER,
                status TEXT, reason TEXT
            )
        """)
        count = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if count > 0:
            return  # 이미 데이터 있음

        tickers = ["005930", "000660", "035420", "051910", "006400"]
        names   = {"005930":"삼성전자","000660":"SK하이닉스",
                   "035420":"NAVER","051910":"LG화학","006400":"삼성SDI"}
        prices  = {"005930":75400,"000660":189000,"035420":198000,
                   "051910":312000,"006400":285000}
        reasons = [
            "RSI 28 과매도 + MACD 골든크로스 + 거래량 3.2배",
            "볼린저밴드 하단 터치 + RSI 26 강한 과매도",
            "MA5 골든크로스 + 외인 순매수 확인",
            "손절선 도달 (-3.1%)",
            "익절 목표 달성 (+6.2%)",
            "AI 신뢰도 82점 — 추세 전환 신호",
        ]

        rows = []
        base = datetime.now() - timedelta(days=30)
        random.seed(42)

        for i in range(60):
            ticker = random.choice(tickers)
            otype  = random.choice(["BUY","BUY","SELL"])
            price  = prices[ticker] + random.randint(-3000, 3000)
            qty    = random.randint(1, 10)
            ts     = (base + timedelta(
                days=random.randint(0,29),
                hours=random.randint(9,15),
                minutes=random.randint(0,59),
            )).isoformat()
            status = random.choice(["PAPER_FILLED","PAPER_FILLED","PAPER_FILLED","BLOCKED"])
            rows.append((
                f"demo_{i:04d}", ts, ticker, otype, qty, price, status,
                random.choice(reasons),
            ))

        con.executemany(
            "INSERT INTO orders (order_id,timestamp,ticker,order_type,qty,price,status,reason) "
            "VALUES (?,?,?,?,?,?,?,?)", rows
        )
    print("[DB] 데모 데이터 삽입 완료")


# ── 조회 함수 ─────────────────────────────────

def get_orders(limit: int = 200) -> list[dict]:
    """최근 주문 내역 반환"""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM orders ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_pnl() -> list[dict]:
    """
    날짜별 실현 손익 집계.
    PAPER_FILLED 매도 주문 기준으로 근사 계산한다.
    """
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT
                DATE(timestamp) as date,
                SUM(CASE WHEN order_type='BUY'  THEN -qty*price ELSE qty*price END) as pnl,
                COUNT(*) as trade_count
            FROM orders
            WHERE status IN ('PAPER_FILLED','FILLED')
            GROUP BY DATE(timestamp)
            ORDER BY date
        """).fetchall()
    return [{"date": r[0], "pnl": round(r[1] or 0, 0), "count": r[2]} for r in rows]


def get_ticker_stats() -> list[dict]:
    """종목별 거래 통계"""
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT
                ticker,
                COUNT(*) as total,
                SUM(CASE WHEN order_type='BUY'  THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN order_type='SELL' THEN 1 ELSE 0 END) as sells,
                AVG(price) as avg_price,
                SUM(qty*price) as total_amount
            FROM orders
            WHERE status IN ('PAPER_FILLED','FILLED')
            GROUP BY ticker
            ORDER BY total DESC
        """).fetchall()
    return [
        {"ticker": r[0], "total": r[1], "buys": r[2], "sells": r[3],
         "avg_price": round(r[4] or 0, 0), "total_amount": round(r[5] or 0, 0)}
        for r in rows
    ]


def get_summary_stats() -> dict:
    """대시보드 상단 KPI 카드용 요약 통계"""
    with sqlite3.connect(DB_PATH) as con:
        total   = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        filled  = con.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('PAPER_FILLED','FILLED')"
        ).fetchone()[0]
        blocked = con.execute(
            "SELECT COUNT(*) FROM orders WHERE status='BLOCKED'"
        ).fetchone()[0]
        today_c = con.execute(
            "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=DATE('now')"
        ).fetchone()[0]
        buy_amt = con.execute(
            "SELECT COALESCE(SUM(qty*price),0) FROM orders "
            "WHERE order_type='BUY' AND status IN ('PAPER_FILLED','FILLED')"
        ).fetchone()[0]
        sell_amt= con.execute(
            "SELECT COALESCE(SUM(qty*price),0) FROM orders "
            "WHERE order_type='SELL' AND status IN ('PAPER_FILLED','FILLED')"
        ).fetchone()[0]

    return {
        "total_orders":   total,
        "filled_orders":  filled,
        "blocked_orders": blocked,
        "today_count":    today_c,
        "realized_pnl":   round(sell_amt - buy_amt, 0),
        "buy_amount":     round(buy_amt, 0),
        "sell_amount":    round(sell_amt, 0),
    }


def get_ai_judge_log(date_str: Optional[str] = None) -> list[dict]:
    """AI 판단 로그 파일을 읽어 반환한다."""
    import json
    from pathlib import Path

    log_dir = Path(__file__).parent.parent / "logs"
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    log_file = log_dir / f"ai_judge_{date_str}.log"
    if not log_file.exists():
        # 데모 데이터
        return [
            {"ticker":"005930","action":"BUY","confidence":82,
             "reason":"RSI 28 과매도 + MACD 골든크로스","executable":True},
            {"ticker":"000660","action":"HOLD","confidence":55,
             "reason":"추세 불명확 — 관망 유지","executable":False},
            {"ticker":"035420","action":"SELL","confidence":76,
             "reason":"RSI 72 과매수 + 볼린저밴드 상단","executable":True},
        ]

    records = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass
    return records
