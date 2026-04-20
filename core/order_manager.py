"""
core/order_manager.py — 주문 실행 모듈

- 매수 / 매도 주문 전송
- 중복 주문 방지 (pending_orders 세트 관리)
- 모든 주문 내역을 SQLite DB에 저장
- 페이퍼 트레이딩 모드에서는 실제 주문 전송 없이 DB만 기록
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Literal, Optional

from loguru import logger

from config import API_CONFIG, DB_PATH, PAPER_TRADING, RISK_CONFIG
from core.ai_judge import AIVerdict
from core.risk_manager import STYLE_DAY, RiskManager


OrderType = Literal["BUY", "SELL"]
HogaType  = Literal["00", "03"]   # 00=지정가, 03=시장가


class OrderManager:
    """
    주문 실행 및 내역 관리 클래스.

    사용 예:
        om = OrderManager(kiwoom_api, risk_manager)
        om.execute(verdict, current_price, available_cash)
    """

    def __init__(self, kiwoom, risk_manager: RiskManager) -> None:
        self._kw  = kiwoom
        self._rm  = risk_manager
        self._acc = API_CONFIG["account_no"]
        self._pending: set[str] = set()   # 중복 주문 방지용 ticker 세트

        self._init_db()
        mode = "📄 페이퍼" if PAPER_TRADING else "💰 실거래"
        logger.info("OrderManager 초기화 | 모드: {} | 계좌: {}", mode, self._acc)

    # ── 퍼블릭 API ────────────────────────────

    def execute(
        self,
        verdict: AIVerdict,
        current_price: int,
        available_cash: int = 0,
        hoga: HogaType = "03",   # 기본: 시장가
        style: str = STYLE_DAY,  # "daytrading" | "longterm"
    ) -> bool:
        """
        AIVerdict를 받아 리스크 검사 후 주문을 실행한다.
        성공 시 True, 차단 시 False 반환.
        """
        ticker = verdict.ticker

        if verdict.action == "HOLD":
            logger.debug("HOLD — 주문 없음: {}", ticker)
            return False

        # ── 중복 주문 방지 ────────────────────
        if ticker in self._pending:
            logger.warning("중복 주문 차단: {}", ticker)
            return False

        if verdict.action == "BUY":
            return self._buy(ticker, current_price, available_cash, hoga, verdict, style)
        elif verdict.action == "SELL":
            return self._sell(ticker, current_price, hoga, verdict)

        return False

    def cancel_all_pending(self) -> None:
        """미체결 주문을 모두 취소한다 (장 마감 시 호출)"""
        for ticker in list(self._pending):
            logger.info("미체결 주문 취소: {}", ticker)
            self._pending.discard(ticker)

    # ── 매수 ─────────────────────────────────

    def _buy(
        self,
        ticker: str,
        price: int,
        available_cash: int,
        hoga: HogaType,
        verdict: AIVerdict,
        style: str = STYLE_DAY,
    ) -> bool:
        check = self._rm.check_buy(ticker, price, verdict.confidence, available_cash, style=style)
        if not check.allowed:
            logger.warning("매수 차단 [{}][{}]: {}", style, ticker, check.reason)
            self._save_order(ticker, "BUY", 0, price, "BLOCKED", check.reason)
            return False

        qty = check.qty
        order_id = str(uuid.uuid4())[:8]
        self._pending.add(ticker)

        if PAPER_TRADING:
            logger.info(
                "📄 [PAPER 매수][{}] {} x{}주 @{:,}원 | 신뢰도:{}",
                style, ticker, qty, price, verdict.confidence,
            )
            self._rm.add_position(ticker, verdict.ticker, qty, price, style=style)
            self._save_order(ticker, "BUY", qty, price, "PAPER_FILLED", verdict.reason, order_id)
            self._pending.discard(ticker)
            return True

        # 실거래
        ret = self._kw.send_order(
            rq_name    = f"매수_{ticker}_{order_id}",
            scr_no     = "2000",
            acc_no     = self._acc,
            order_type = 1,
            code       = ticker,
            qty        = qty,
            price      = price if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        if ret == 0:
            logger.success("매수 주문 전송 [{}]: {} x{}주 @{:,}", style, ticker, qty, price)
            self._rm.add_position(ticker, ticker, qty, price, style=style)
            self._save_order(ticker, "BUY", qty, price, "SENT", verdict.reason, order_id)
        else:
            logger.error("매수 주문 실패: {} | ret={}", ticker, ret)
            self._pending.discard(ticker)
            self._save_order(ticker, "BUY", qty, price, "ERROR", f"ret={ret}", order_id)
            return False

        self._pending.discard(ticker)
        return True

    # ── 매도 ─────────────────────────────────

    def _sell(
        self,
        ticker: str,
        price: int,
        hoga: HogaType,
        verdict: AIVerdict,
    ) -> bool:
        check = self._rm.check_sell(ticker)
        if not check.allowed:
            logger.warning("매도 차단 [{}]: {}", ticker, check.reason)
            return False

        qty = check.qty
        order_id = str(uuid.uuid4())[:8]
        self._pending.add(ticker)

        if PAPER_TRADING:
            pnl = self._rm.remove_position(ticker, price)
            logger.info(
                "📄 [PAPER 매도] {} x{}주 @{:,}원 | 손익:{:+,.0f}원",
                ticker, qty, price, pnl or 0,
            )
            self._save_order(
                ticker, "SELL", qty, price, "PAPER_FILLED",
                f"{verdict.reason} | 손익:{pnl:+,.0f}" if pnl else verdict.reason,
                order_id,
            )
            self._pending.discard(ticker)
            return True

        # 실거래
        ret = self._kw.send_order(
            rq_name    = f"매도_{ticker}_{order_id}",
            scr_no     = "2001",
            acc_no     = self._acc,
            order_type = 2,           # 신규매도
            code       = ticker,
            qty        = qty,
            price      = price if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        if ret == 0:
            pnl = self._rm.remove_position(ticker, price)
            logger.success("매도 주문 전송: {} x{}주 @{:,} | 손익:{:+,.0f}", ticker, qty, price, pnl or 0)
            self._save_order(ticker, "SELL", qty, price, "SENT", verdict.reason, order_id)
        else:
            logger.error("매도 주문 실패: {} | ret={}", ticker, ret)
            self._save_order(ticker, "SELL", qty, price, "ERROR", f"ret={ret}", order_id)
            self._pending.discard(ticker)
            return False

        self._pending.discard(ticker)
        return True

    # ── DB ───────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id    TEXT,
                    timestamp   TEXT,
                    ticker      TEXT,
                    order_type  TEXT,
                    qty         INTEGER,
                    price       INTEGER,
                    status      TEXT,
                    reason      TEXT,
                    strategy    TEXT DEFAULT ''
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_orders_ticker    ON orders(ticker)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status)")
        logger.debug("orders 테이블 초기화 완료")

    def _save_order(
        self,
        ticker: str,
        order_type: str,
        qty: int,
        price: int,
        status: str,
        reason: str,
        order_id: str = "",
    ) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO orders (order_id,timestamp,ticker,order_type,qty,price,status,reason) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    datetime.now().isoformat(),
                    ticker,
                    order_type,
                    qty,
                    price,
                    status,
                    reason,
                ),
            )
