"""
core/order_manager.py — 주문 실행 모듈 (실전 전용)

- 매수 / 매도 주문 전송
- 중복 주문 방지 (pending_orders 세트 관리)
- 모든 주문 내역을 SQLite DB에 저장
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Literal

from loguru import logger

from config import API_CONFIG, DB_PATH
from core.ai_judge import AIVerdict
from core.daytrade_journal import DayTradeJournal
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
        self._day_journal = DayTradeJournal()
        self._pending: set[str] = set()   # 중복 주문 방지용 ticker 세트

        self._init_db()
        logger.info("OrderManager 초기화 | 💰 실거래 | 계좌: {}", self._acc)

    # ── 퍼블릭 API ────────────────────────────

    def execute(
        self,
        verdict: AIVerdict,
        current_price: float,
        available_cash: int = 0,
        hoga: HogaType = "03",   # 기본: 시장가
        style: str = STYLE_DAY,  # "daytrading" | "longterm"
        atr: float = 0.0,        # ATR(원) — RiskManager 사이징 기준
    ) -> bool:
        """
        AIVerdict를 받아 리스크 검사 후 주문을 실행한다.
        성공 시 True, 차단 시 False 반환.
        """
        ticker = verdict.ticker

        if verdict.action == "HOLD":
            logger.debug("HOLD — 주문 없음: {}", ticker)
            return False

        if ticker in self._pending:
            logger.warning("중복 주문 차단: {}", ticker)
            return False

        if verdict.action == "BUY":
            return self._buy(ticker, current_price, available_cash, hoga, verdict, style, atr)
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
        price: float,
        available_cash: int,
        hoga: HogaType,
        verdict: AIVerdict,
        style: str = STYLE_DAY,
        atr: float = 0.0,
    ) -> bool:
        # ── 해외주식 가드: 키움 OpenAPI는 해외주식 주문을 지원하지 않음 ──
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            reason = "해외주식 주문 미지원 (키움 OpenAPI는 국내주식 전용)"
            logger.warning("매수 차단 [해외][{}]: {}", ticker, reason)
            self._save_order(ticker, "BUY", 0, price, "BLOCKED", reason)
            return False

        if available_cash <= 0:
            available_cash = self._refresh_buying_power()
            logger.info("매수가능 금액 재조회 [{}]: {:,}원", ticker, available_cash)

        check = self._rm.check_buy(ticker, price, verdict.confidence, available_cash,
                                   style=style, atr=atr)
        if not check.allowed:
            logger.warning("매수 차단 [{}][{}]: {}", style, ticker, check.reason)
            self._save_order(ticker, "BUY", 0, price, "BLOCKED", check.reason)
            return False

        qty = check.qty
        order_id = str(uuid.uuid4())[:8]
        self._pending.add(ticker)

        # 실거래 (국내주식만 도달)
        ret = self._kw.send_order(
            rq_name    = f"매수_{ticker}_{order_id}",
            scr_no     = "2000",
            acc_no     = self._acc,
            order_type = 1,
            code       = ticker,
            qty        = qty,
            price      = int(price) if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        if ret == 0:
            logger.success("매수 주문 전송 [{}]: {} x{}주 @{:,.0f} | ATR진입:{:.0f}",
                           style, ticker, qty, price, atr)
            self._rm.add_position(ticker, ticker, qty, price, style=style, atr=atr)
            if style == STYLE_DAY:
                self._day_journal.record_entry(
                    ticker=ticker,
                    qty=qty,
                    price=price,
                    strategy=getattr(verdict, "position_size", "") or "",
                    confidence=float(verdict.confidence or 0),
                    atr=float(atr or 0),
                    reason=verdict.reason or "",
                )
            self._save_order(ticker, "BUY", qty, price, "SENT", verdict.reason, order_id)
            # AI 판단 결과 추적용 기록 (verdict 메타가 풍부한 경우)
            try:
                from core.ai_accuracy_tracker import AIAccuracyTracker, AISignalRecord
                AIAccuracyTracker().record_entry(AISignalRecord(
                    ticker=ticker, name=ticker,
                    entry_at=datetime.now().isoformat(),
                    entry_price=price,
                    ai_action=verdict.action,
                    ai_confidence=float(verdict.confidence or 0),
                    ai_reason=(verdict.reason or "")[:200],
                    setup_type=getattr(verdict, "setup_type", ""),
                    composite=getattr(verdict, "composite", 0.0),
                    tech_score=getattr(verdict, "tech_score", 0.0),
                    fund_passed=bool(getattr(verdict, "fund_passed", True)),
                    regime=getattr(verdict, "regime", ""),
                ))
            except Exception:
                pass
        else:
            logger.error("매수 주문 실패: {} | ret={}", ticker, ret)
            self._pending.discard(ticker)
            self._save_order(ticker, "BUY", qty, price, "ERROR", f"ret={ret}", order_id)
            return False

        self._pending.discard(ticker)
        return True

    # ── 매도 ─────────────────────────────────

    def _refresh_buying_power(self) -> int:
        """주문 직전 현금이 0으로 보일 때 브로커 잔고를 재조회한다."""
        try:
            deposit = self._kw.get_deposit_detail() if hasattr(self._kw, "get_deposit_detail") else {}
            candidates = [
                deposit.get("ord_alow_amt", 0),
                deposit.get("d2_ord_psbl_amt", 0),
                deposit.get("d2_entra", 0),
                deposit.get("entr", 0),
            ]
            def _money(v) -> int:
                try:
                    return int(str(v).replace(",", "").replace("+", "").strip() or 0)
                except Exception:
                    return 0

            best = max((_money(c) for c in candidates if _money(c) > 0), default=0)
            if best:
                return best

            balance = self._kw.get_balance() if hasattr(self._kw, "get_balance") else {}
            output = (balance.get("output2", [{}]) or [{}])[0]
            return _money(output.get("buying_power", 0) or output.get("entr", 0) or output.get("d2_entra", 0) or 0)
        except Exception as e:
            logger.warning("매수가능 금액 재조회 실패: {}", e)
            return 0

    def _restore_position_from_broker(self, ticker: str) -> bool:
        """메모리에 없는 보유 종목은 실제 계좌에서 찾아 매도 가능 상태로 복구한다."""
        if not hasattr(self._kw, "get_holdings"):
            return False
        try:
            for h in self._kw.get_holdings():
                if h.get("ticker") != ticker:
                    continue
                qty = int(h.get("qty") or 0)
                avg = float(h.get("avg_price") or h.get("cur_price") or 0)
                if qty <= 0:
                    return False
                self._rm.add_position(
                    ticker,
                    h.get("name") or ticker,
                    qty,
                    avg or float(h.get("cur_price") or 0),
                    style=STYLE_DAY,
                )
                logger.warning("매도 전 실제 계좌 보유 복구 [{}]: {}주 @{:,.0f}", ticker, qty, avg)
                return True
        except Exception as e:
            logger.warning("실제 계좌 보유 복구 실패 [{}]: {}", ticker, e)
        return False

    def _sell(
        self,
        ticker: str,
        price: float,
        hoga: HogaType,
        verdict: AIVerdict,
    ) -> bool:
        pos_before = self._rm.get_positions().get(ticker)
        check = self._rm.check_sell(ticker)
        if not check.allowed and self._restore_position_from_broker(ticker):
            check = self._rm.check_sell(ticker)
        if not check.allowed:
            logger.warning("매도 차단 [{}]: {}", ticker, check.reason)
            return False

        qty = check.qty
        order_id = str(uuid.uuid4())[:8]
        self._pending.add(ticker)

        # 실거래
        ret = self._kw.send_order(
            rq_name    = f"매도_{ticker}_{order_id}",
            scr_no     = "2001",
            acc_no     = self._acc,
            order_type = 2,           # 신규매도
            code       = ticker,
            qty        = qty,
            price      = int(price) if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        if ret == 0:
            pnl = self._rm.remove_position(ticker, price)
            if pos_before and pos_before.style == STYLE_DAY:
                self._day_journal.record_exit(
                    ticker=ticker,
                    qty=qty,
                    entry_price=pos_before.avg_price,
                    exit_price=price,
                    pnl=float(pnl or 0),
                    converted=bool(pos_before.converted),
                    reason=verdict.reason or "",
                )
            logger.success("매도 주문 전송: {} x{}주 @{:,} | 손익:{:+,.0f}", ticker, qty, price, pnl or 0)
            self._save_order(ticker, "SELL", qty, price, "SENT", verdict.reason, order_id)
            # AI 결과 매핑
            try:
                from core.ai_accuracy_tracker import AIAccuracyTracker
                AIAccuracyTracker().record_exit(ticker, price)
            except Exception:
                pass
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
        price: float,
        status: str,
        reason: str,
        order_id: str = "",
    ) -> None:
        # 국내는 정수원, 해외는 소수점 USD까지 보존
        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
        stored_price = int(round(price)) if is_kr else round(float(price), 4)
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
                    stored_price,
                    status,
                    reason,
                ),
            )
