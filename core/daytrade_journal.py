from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from loguru import logger

from config import DB_PATH, LOG_DIR


class DayTradeJournal:
    """Append-only daytrade event journal for clean short-term performance review."""

    def __init__(self) -> None:
        self._init_db()

    def record_entry(
        self,
        *,
        ticker: str,
        qty: int,
        price: float,
        strategy: str = "",
        confidence: float = 0.0,
        atr: float = 0.0,
        reason: str = "",
    ) -> None:
        self._write(
            {
                "event": "ENTRY",
                "ticker": ticker,
                "qty": int(qty or 0),
                "price": float(price or 0.0),
                "strategy": strategy,
                "confidence": float(confidence or 0.0),
                "atr": float(atr or 0.0),
                "reason": str(reason or "")[:500],
            }
        )

    def record_exit(
        self,
        *,
        ticker: str,
        qty: int,
        entry_price: float,
        exit_price: float,
        pnl: float,
        converted: bool = False,
        reason: str = "",
    ) -> None:
        entry_price = float(entry_price or 0.0)
        exit_price = float(exit_price or 0.0)
        pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
        self._write(
            {
                "event": "EXIT",
                "ticker": ticker,
                "qty": int(qty or 0),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": float(pnl or 0.0),
                "pnl_pct": round(pnl_pct, 4),
                "converted": bool(converted),
                "reason": str(reason or "")[:500],
            }
        )

    def record_conversion(
        self,
        *,
        ticker: str,
        qty: int,
        entry_price: float,
        current_price: float,
        reason: str = "",
    ) -> None:
        entry_price = float(entry_price or 0.0)
        current_price = float(current_price or 0.0)
        unrealized_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
        self._write(
            {
                "event": "CONVERT_TO_LONG",
                "ticker": ticker,
                "qty": int(qty or 0),
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pct": round(unrealized_pct, 4),
                "reason": str(reason or "")[:500],
            }
        )

    def _write(self, payload: dict[str, Any]) -> None:
        payload = {"timestamp": datetime.now().isoformat(), **payload}
        self._write_file(payload)
        self._write_db(payload)

    def _write_file(self, payload: dict[str, Any]) -> None:
        path = LOG_DIR / f"daytrades_{datetime.now():%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _write_db(self, payload: dict[str, Any]) -> None:
        try:
            with sqlite3.connect(DB_PATH) as con:
                con.execute(
                    "INSERT INTO daytrade_events "
                    "(timestamp,event,ticker,qty,price,entry_price,exit_price,pnl,pnl_pct,converted,strategy,confidence,atr,reason,raw_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        payload.get("timestamp", ""),
                        payload.get("event", ""),
                        payload.get("ticker", ""),
                        int(payload.get("qty") or 0),
                        float(payload.get("price") or payload.get("current_price") or 0.0),
                        float(payload.get("entry_price") or 0.0),
                        float(payload.get("exit_price") or 0.0),
                        float(payload.get("pnl") or 0.0),
                        float(payload.get("pnl_pct") or payload.get("unrealized_pct") or 0.0),
                        int(bool(payload.get("converted", False))),
                        payload.get("strategy", ""),
                        float(payload.get("confidence") or 0.0),
                        float(payload.get("atr") or 0.0),
                        payload.get("reason", ""),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        except Exception as e:
            logger.debug("daytrade journal db write failed: {}", e)

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS daytrade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event TEXT,
                    ticker TEXT,
                    qty INTEGER,
                    price REAL,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    pnl_pct REAL,
                    converted INTEGER DEFAULT 0,
                    strategy TEXT,
                    confidence REAL,
                    atr REAL,
                    reason TEXT,
                    raw_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_daytrade_events_ts ON daytrade_events(timestamp)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daytrade_events_ticker ON daytrade_events(ticker)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daytrade_events_event ON daytrade_events(event)")
