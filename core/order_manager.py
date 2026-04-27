"""
core/order_manager.py - live order execution module

Core principles:
  - send_order ret==0 means "accepted", not "filled".
  - Real fills are only acknowledged when broker holdings change is observed.
  - add_position / remove_position / DayTradeJournal entries are NOT written
    until a fill is confirmed.
  - Partial fills are tracked cumulatively. e.g. 16 ordered, 5 filled -> only
    5 are reflected in the position.
  - Unfilled orders go into the pending dict; main loop calls
    reconcile_pending() to drain them.
  - On shutdown / market close, cancel_all_pending() also cancels broker-side
    open orders via kw.cancel_order().

Flow:
  execute(verdict)
    -> RiskManager check -> blocked: OrderResult(ok=False, action='BLOCKED')
    -> send_order; ret!=0    -> OrderResult(ok=False, action='ERROR')
    -> short poll for FillSnapshot(filled, filled_qty, avg_price)
       complete fill : finalize -> OrderResult(ok=True, filled=True)
       partial fill  : apply increment + register pending
       no fill       : register pending
  reconcile_pending(): each cycle, sync broker holdings vs pending.
  cancel_all_pending(): cancel broker open orders + clear memory.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from loguru import logger

from config import API_CONFIG, DB_PATH
from core.ai_judge import AIVerdict
from core.daytrade_journal import DayTradeJournal
from core.risk_manager import STYLE_DAY, RiskManager


OrderType = Literal["BUY", "SELL"]
HogaType  = Literal["00", "03"]   # 00=limit, 03=market


# -- result dataclasses --------------------------------------------------

@dataclass
class OrderResult:
    """Unified result for OrderManager.execute() and reconcile_pending()."""
    ok:          bool
    action:      str    # BUY|SELL|HOLD|BLOCKED|ERROR|UNFILLED|PARTIAL|CANCELLED|CANCEL_FAILED|MEMORY_CLEARED|FILLED
    ticker:      str
    qty:         int  = 0
    filled_qty:  int  = 0
    price:       float = 0.0
    order_id:    str  = ""
    style:       str  = ""
    reason:      str  = ""
    filled:      bool = False     # filled_qty >= qty
    pending:     bool = False     # accepted, fill not yet confirmed
    is_partial:  bool = False     # 0 < filled_qty < qty
    pnl:         Optional[float] = None   # SELL realized pnl (cumulative)


@dataclass
class FillSnapshot:
    """Snapshot of broker holdings change for a single ticker."""
    filled:     bool
    filled_qty: int
    avg_price:  float


@dataclass
class _Pending:
    side:                str          # 'BUY' or 'SELL'
    ticker:              str
    qty:                 int
    price:               float
    order_id:            str          # internal uuid8
    style:               str
    verdict:             AIVerdict
    atr:                 float
    pos_before_qty:      int
    sent_at:             float
    timeout_sec:         float = 60.0
    # cumulative fill tracking
    filled_qty:          int   = 0
    avg_fill_price:      float = 0.0
    realized_pnl_so_far: float = 0.0
    entry_price_at_send: float = 0.0   # SELL only - holding avg before sell
    # broker handle
    broker_ord_no:       str   = ""
    # one-shot side-effect guards
    notified_first_fill: bool  = False
    recorded_exit:       bool  = False


class OrderManager:
    """주문 실행 + 미체결/체결 동기화."""

    POLL_INTERVAL = 0.5
    POLL_MAX_WAIT = 1.5

    def __init__(self, kiwoom, risk_manager: RiskManager) -> None:
        self._kw  = kiwoom
        self._rm  = risk_manager
        self._acc = API_CONFIG["account_no"]
        self._day_journal = DayTradeJournal()
        self._sending: set[str] = set()
        self._pending: dict[str, _Pending] = {}
        self._init_db()
        logger.info("OrderManager init | LIVE | acct={}", self._acc)

    # -- public API --------------------------------------------------------

    def execute(
        self,
        verdict: AIVerdict,
        current_price: float,
        available_cash: int = 0,
        hoga: HogaType = "03",
        style: str = STYLE_DAY,
        atr: float = 0.0,
    ) -> OrderResult:
        ticker = verdict.ticker
        if verdict.action == "HOLD":
            logger.debug("HOLD - no order: {}", ticker)
            return OrderResult(ok=False, action="HOLD", ticker=ticker, reason="HOLD")
        if ticker in self._sending or ticker in self._pending:
            logger.warning("duplicate order blocked (in flight): {}", ticker)
            return OrderResult(ok=False, action="BLOCKED", ticker=ticker,
                               reason="order already in flight")
        if verdict.action == "BUY":
            return self._buy(ticker, current_price, available_cash, hoga, verdict, style, atr)
        if verdict.action == "SELL":
            return self._sell(ticker, current_price, hoga, verdict)
        return OrderResult(ok=False, action="UNKNOWN", ticker=ticker,
                           reason=f"unknown action {verdict.action}")

    def cancel_all_pending(self) -> list[OrderResult]:
        """
        Cancel broker open orders and clear memory pending.

        action classification on result:
          CANCELLED      : broker cancel succeeded (preserves any partial fill)
          CANCEL_FAILED  : broker cancel returned non-zero -- broker MAY still hold open order
          MEMORY_CLEARED : cancel API not callable (no cancel_order or no ord_no) -- broker MAY still hold open order
        """
        results: list[OrderResult] = []
        if not self._pending:
            return results

        has_cancel_api = hasattr(self._kw, "cancel_order")
        for ticker in list(self._pending.keys()):
            p = self._pending[ticker]
            remaining = max(0, p.qty - p.filled_qty)
            broker_called = False
            broker_ok     = False

            if has_cancel_api and p.broker_ord_no and remaining > 0:
                broker_called = True
                try:
                    rc = self._kw.cancel_order(
                        orig_ord_no=p.broker_ord_no,
                        ticker=p.ticker,
                        side=p.side,
                        qty=remaining,
                    )
                    broker_ok = (rc == 0)
                except Exception as e:
                    logger.warning("broker cancel exception [{}]: {}", ticker, e)

            if remaining > 0:
                if not has_cancel_api:
                    logger.warning("kw.cancel_order missing - memory only [{}]", ticker)
                elif not p.broker_ord_no:
                    logger.warning("broker_ord_no missing - memory only [{}]", ticker)
                elif not broker_ok:
                    logger.error("broker cancel FAILED - manual check required: {} ord_no={}",
                                 ticker, p.broker_ord_no)

            db_status, action = self._classify_cancel(
                broker_called=broker_called, broker_ok=broker_ok,
                filled_qty=p.filled_qty, total_qty=p.qty, remaining=remaining,
            )

            # SELL with any partial fill -- record exit once
            if p.side == "SELL" and p.filled_qty > 0:
                self._record_sell_exit(p)

            self._update_order_fields(
                p.order_id, status=db_status,
                filled_qty=p.filled_qty,
                avg_fill_price=float(p.avg_fill_price or p.price),
                realized_pnl=(float(p.realized_pnl_so_far or 0.0) if p.side == "SELL" else 0.0),
            )
            results.append(OrderResult(
                ok=broker_ok, action=action,
                ticker=ticker, qty=p.qty, filled_qty=p.filled_qty,
                price=p.price, order_id=p.order_id, style=p.style,
                reason=self._cancel_reason(
                    has_api=has_cancel_api, has_ord_no=bool(p.broker_ord_no),
                    broker_ok=broker_ok, remaining=remaining,
                ),
                is_partial=(p.filled_qty > 0 and p.filled_qty < p.qty),
                pnl=(p.realized_pnl_so_far if p.side == "SELL" else None),
            ))
            self._pending.pop(ticker, None)
            self._sending.discard(ticker)
        return results

    def reconcile_pending(self) -> list[OrderResult]:
        """매 cycle 호출 - broker holdings 와 pending 을 동기화. 부분체결 누적."""
        if not self._pending:
            return []

        try:
            holdings = self._kw.get_holdings() if hasattr(self._kw, "get_holdings") else []
        except Exception as e:
            logger.warning("reconcile: holdings lookup failed: {}", e)
            return []

        broker_qty = self._holdings_to_qty_map(holdings)
        results: list[OrderResult] = []
        now = time.time()

        for ticker in list(self._pending.keys()):
            p = self._pending[ticker]
            cur_qty = broker_qty.get(ticker, 0)
            cur_avg = self._holdings_avg_price(holdings, ticker) or p.price

            if p.side == "BUY":
                filled_now = max(0, cur_qty - p.pos_before_qty)
                if filled_now > p.filled_qty:
                    self._apply_buy_increment(p, filled_now, cur_avg)
                if filled_now >= p.qty:
                    self._update_order_fields(
                        p.order_id, status="FILLED",
                        filled_qty=filled_now,
                        avg_fill_price=float(cur_avg or 0),
                    )
                    results.append(OrderResult(
                        ok=True, action="BUY", ticker=ticker, qty=p.qty,
                        filled_qty=filled_now, price=cur_avg, order_id=p.order_id,
                        style=p.style, reason=p.verdict.reason or "",
                        filled=True, is_partial=False,
                    ))
                    self._pending.pop(ticker, None)
                    self._sending.discard(ticker)
                    continue
            else:  # SELL
                filled_now = max(0, p.pos_before_qty - cur_qty)
                if filled_now > p.filled_qty:
                    self._apply_sell_increment(p, filled_now, p.price)
                if filled_now >= p.qty:
                    self._record_sell_exit(p)
                    self._update_order_fields(
                        p.order_id, status="FILLED",
                        filled_qty=filled_now,
                        avg_fill_price=float(p.price),
                        realized_pnl=float(p.realized_pnl_so_far or 0.0),
                    )
                    results.append(OrderResult(
                        ok=True, action="SELL", ticker=ticker, qty=p.qty,
                        filled_qty=filled_now, price=p.price, order_id=p.order_id,
                        style=p.style, reason=p.verdict.reason or "",
                        filled=True, is_partial=False,
                        pnl=p.realized_pnl_so_far,
                    ))
                    self._pending.pop(ticker, None)
                    self._sending.discard(ticker)
                    continue

            if now - p.sent_at >= p.timeout_sec:
                remaining = max(0, p.qty - p.filled_qty)
                has_cancel_api = hasattr(self._kw, "cancel_order")
                broker_called = False
                broker_ok     = False
                if has_cancel_api and p.broker_ord_no and remaining > 0:
                    broker_called = True
                    try:
                        rc = self._kw.cancel_order(p.broker_ord_no, p.ticker, p.side, remaining)
                        broker_ok = (rc == 0)
                    except Exception as e:
                        logger.warning("timeout broker cancel exception [{}]: {}", ticker, e)

                if p.side == "SELL" and p.filled_qty > 0:
                    self._record_sell_exit(p)

                db_status, action = self._classify_cancel(
                    broker_called=broker_called, broker_ok=broker_ok,
                    filled_qty=p.filled_qty, total_qty=p.qty, remaining=remaining,
                )
                # In timeout context, when broker cancel succeeded the high-level intent
                # is PARTIAL or UNFILLED, not "CANCELLED". CANCEL_FAILED / MEMORY_CLEARED
                # surface as-is so the operator can see broker-side risk.
                if action == "CANCELLED" and remaining > 0:
                    if p.filled_qty > 0:
                        action    = "PARTIAL"
                        db_status = "PARTIAL_CANCELLED"
                    else:
                        action    = "UNFILLED"
                        db_status = "UNFILLED"

                logger.warning(
                    "order timeout - {} [{}/{}]: {} | filled {}/{} | broker={}",
                    db_status, p.side, p.style, ticker, p.filled_qty, p.qty,
                    "ok" if broker_ok else ("not-called" if not broker_called else "FAIL"),
                )
                self._update_order_fields(
                    p.order_id, status=db_status,
                    filled_qty=p.filled_qty,
                    avg_fill_price=float(p.avg_fill_price or p.price),
                    realized_pnl=(float(p.realized_pnl_so_far or 0.0) if p.side == "SELL" else 0.0),
                )
                reason_detail = self._cancel_reason(
                    has_api=has_cancel_api, has_ord_no=bool(p.broker_ord_no),
                    broker_ok=broker_ok, remaining=remaining,
                )
                results.append(OrderResult(
                    ok=broker_ok, action=action, ticker=ticker, qty=p.qty,
                    filled_qty=p.filled_qty, price=p.price, order_id=p.order_id,
                    style=p.style,
                    reason=f"{p.side} timeout {p.timeout_sec:.0f}s; {reason_detail}",
                    is_partial=(p.filled_qty > 0 and p.filled_qty < p.qty),
                    pnl=(p.realized_pnl_so_far if p.side == "SELL" else None),
                ))
                self._pending.pop(ticker, None)
                self._sending.discard(ticker)

        return results

    def get_pending_count(self) -> int:
        return len(self._pending)

    # -- startup-time DB reconciliation -----------------------------------

    def reconcile_persisted_orders(self, lookback_days: int = 3) -> dict:
        """Resolve SENT / PARTIAL_FILLED rows that survived a process restart.

        Strategy:
          1) Pull broker's open-order list (kt00007). Anything still open stays SENT.
          2) For everything else, compare against current holdings:
             - BUY  + holdings.qty >= row.qty  -> FILLED
             - BUY  + 0 < holdings.qty < row.qty -> PARTIAL_FILLED (best effort)
             - BUY  + holdings.qty == 0       -> UNFILLED
             - SELL + ticker absent in holdings -> FILLED
             - SELL + ticker still present     -> UNFILLED
          3) When multiple SENT rows share a (ticker, side), reconciliation is
             ambiguous; we leave them alone and surface them in the summary so an
             operator can review manually.

        Returns a summary dict the caller can render to log / telegram.
        """
        summary = {
            "checked": 0, "filled": 0, "partial": 0,
            "unfilled": 0, "kept_open": 0, "ambiguous": 0,
        }

        try:
            with sqlite3.connect(DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT order_id, ticker, order_type, qty, price, broker_ord_no, "
                    "       COALESCE(filled_qty,0) AS filled_qty, timestamp "
                    "FROM orders "
                    "WHERE status IN ('SENT','PARTIAL_FILLED') "
                    "  AND DATE(timestamp) >= DATE('now', ?) "
                    "ORDER BY id",
                    (f"-{int(max(0, lookback_days))} day",),
                ).fetchall()
        except Exception as e:
            logger.warning("startup reconcile DB read failed: {}", e)
            return summary

        if not rows:
            return summary
        summary["checked"] = len(rows)

        # broker open orders (best effort)
        open_ord_nos: set[str] = set()
        if hasattr(self._kw, "get_open_orders"):
            try:
                for o in (self._kw.get_open_orders() or []):
                    n = str(o.get("ord_no") or "").strip()
                    if n:
                        open_ord_nos.add(n)
            except Exception as e:
                logger.warning("startup reconcile open-order lookup failed: {}", e)

        # holdings snapshot
        holdings: list[dict] = []
        try:
            holdings = self._kw.get_holdings() if hasattr(self._kw, "get_holdings") else []
        except Exception as e:
            logger.warning("startup reconcile holdings lookup failed: {}", e)
        qty_map = self._holdings_to_qty_map(holdings)

        # detect ambiguous duplicates (same ticker+side, multiple still-open rows)
        from collections import Counter
        bucket = Counter((r["ticker"], r["order_type"]) for r in rows)

        for row in rows:
            order_id = row["order_id"]
            ticker   = row["ticker"]
            side     = row["order_type"]
            qty      = int(row["qty"] or 0)
            ord_no   = str(row["broker_ord_no"] or "").strip()

            # 1) still listed as open by broker -> leave it
            if ord_no and ord_no in open_ord_nos:
                summary["kept_open"] += 1
                continue

            # 2) duplicate rows for same (ticker,side) -> too risky to auto-resolve
            if bucket[(ticker, side)] > 1:
                summary["ambiguous"] += 1
                logger.warning(
                    "startup reconcile: ambiguous duplicate {} {} rows for {} -- left as-is",
                    bucket[(ticker, side)], side, ticker,
                )
                continue

            cur_qty = int(qty_map.get(ticker, 0))
            avg     = float(self._holdings_avg_price(holdings, ticker) or 0.0)

            if side == "BUY":
                if cur_qty >= qty and qty > 0:
                    self._update_order_fields(
                        order_id, only_if_open=True,
                        status="FILLED",
                        filled_qty=qty,
                        avg_fill_price=avg,
                        reject_msg="reconciled from holdings on startup",
                    )
                    summary["filled"] += 1
                    logger.info("[reconcile] {} BUY {}주 -> FILLED (holdings={})",
                                ticker, qty, cur_qty)
                elif cur_qty > 0:
                    self._update_order_fields(
                        order_id, only_if_open=True,
                        status="PARTIAL_FILLED",
                        filled_qty=cur_qty,
                        avg_fill_price=avg,
                        reject_msg="partial reconciled from holdings on startup",
                    )
                    summary["partial"] += 1
                    logger.info("[reconcile] {} BUY {}/{}주 -> PARTIAL_FILLED",
                                ticker, cur_qty, qty)
                else:
                    self._update_order_fields(
                        order_id, only_if_open=True,
                        status="UNFILLED",
                        reject_msg="not in broker open orders + no holdings on startup",
                    )
                    summary["unfilled"] += 1
                    logger.info("[reconcile] {} BUY {}주 -> UNFILLED (holdings=0)",
                                ticker, qty)
            elif side == "SELL":
                if cur_qty == 0:
                    # exit price 추정: row.price (시장가 주문이면 송신 시점 현재가, 지정가면 한도가).
                    exit_price = float(row["price"] or 0.0)
                    pnl_est, matched_qty = self._estimate_sell_realized_pnl(
                        ticker, qty, exit_price, row["timestamp"],
                    )
                    note = "reconciled (no remaining holdings) on startup"
                    realized_to_store = float(pnl_est) if matched_qty >= qty else 0.0
                    if matched_qty >= qty:
                        note += f"; pnl≈{pnl_est:+,.0f}원 (FIFO est, gross)"
                    elif matched_qty > 0:
                        note += (
                            f"; pnl≈{pnl_est:+,.0f}원 "
                            f"(not booked: partial FIFO match {matched_qty}/{qty})"
                        )
                    elif exit_price <= 0:
                        note += "; pnl unknown (no exit price recorded)"
                    else:
                        note += "; pnl unknown (no prior FILLED BUY in DB)"
                    self._update_order_fields(
                        order_id, only_if_open=True,
                        status="FILLED",
                        filled_qty=qty,
                        avg_fill_price=exit_price,
                        realized_pnl=realized_to_store,
                        reject_msg=note,
                    )
                    summary["filled"] += 1
                    logger.info("[reconcile] {} SELL {}주 -> FILLED (holdings=0, pnl≈{:+,.0f})",
                                ticker, qty, pnl_est)
                else:
                    self._update_order_fields(
                        order_id, only_if_open=True,
                        status="UNFILLED",
                        reject_msg="not in broker open orders; holdings still present",
                    )
                    summary["unfilled"] += 1
                    logger.info("[reconcile] {} SELL {}주 -> UNFILLED (holdings still {})",
                                ticker, qty, cur_qty)
            else:
                summary["ambiguous"] += 1

        logger.info(
            "[reconcile] checked={} filled={} partial={} unfilled={} kept_open={} ambiguous={}",
            summary["checked"], summary["filled"], summary["partial"],
            summary["unfilled"], summary["kept_open"], summary["ambiguous"],
        )
        return summary

    @staticmethod
    def _estimate_sell_realized_pnl(
        ticker: str, sell_qty: int, exit_price: float, before_ts: str,
    ) -> tuple[float, int]:
        """FIFO 기반 추정 실현손익 (gross, 수수료/세금 미반영).

        broker 가 체결한 SELL 의 정확한 체결가를 startup 시점에는 알 수 없으므로,
        DB 의 모든 과거 BUY/SELL 체결행을 시간순으로 재생해 잔여 lot 을 만든 뒤,
        이번 SELL qty 만큼 FIFO 로 소진해 평균 entry 를 구한다.
        반환: (추정손익, 매칭수량). 자료가 부족하면 (0.0, 0).
        """
        try:
            sell_qty = int(sell_qty or 0)
            exit_price = float(exit_price or 0.0)
        except Exception:
            return 0.0, 0
        if sell_qty <= 0 or exit_price <= 0:
            return 0.0, 0

        try:
            with sqlite3.connect(DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT order_type, "
                    "       COALESCE(NULLIF(filled_qty,0), qty) AS fq, "
                    "       COALESCE(NULLIF(avg_fill_price,0), price) AS fp "
                    "FROM orders "
                    "WHERE ticker=? "
                    "  AND status IN ('PAPER_FILLED','FILLED','PARTIAL_FILLED','PARTIAL_CANCELLED') "
                    "  AND timestamp < ? "
                    "ORDER BY timestamp ASC, id ASC",
                    (ticker, before_ts),
                ).fetchall()
        except Exception:
            return 0.0, 0

        # 과거 체결행을 차례로 재생해 잔여 BUY lot 을 누적, SELL 은 FIFO 로 차감.
        lots: list[list[float]] = []  # [[remaining_qty, entry_price], ...]
        for r in rows:
            try:
                fq = int(r["fq"] or 0)
                fp = float(r["fp"] or 0.0)
            except Exception:
                continue
            if fq <= 0:
                continue
            if r["order_type"] == "BUY" and fp > 0:
                lots.append([fq, fp])
            elif r["order_type"] == "SELL":
                need = fq
                idx = 0
                while need > 0 and idx < len(lots):
                    take = min(lots[idx][0], need)
                    lots[idx][0] -= take
                    need -= take
                    if lots[idx][0] <= 0:
                        idx += 1
                lots = [lot for lot in lots if lot[0] > 0]

        # 이번 SELL 분량을 잔여 lot 에서 FIFO 로 매칭
        remaining = sell_qty
        cost = 0.0
        matched = 0
        for lot in lots:
            if remaining <= 0:
                break
            take = min(int(lot[0]), remaining)
            cost += take * float(lot[1])
            matched += take
            remaining -= take
        if matched <= 0:
            return 0.0, 0
        avg_entry = cost / matched
        return (exit_price - avg_entry) * matched, matched

    # -- internal: BUY -----------------------------------------------------

    def _buy(self, ticker, price, available_cash, hoga, verdict, style, atr) -> OrderResult:
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            reason = "foreign ticker not supported (Kiwoom KR-only)"
            logger.warning("BUY blocked [foreign][{}]: {}", ticker, reason)
            self._save_order(ticker, "BUY", 0, price, "BLOCKED", reason,
                             strategy=style, reject_msg=reason)
            return OrderResult(ok=False, action="BLOCKED", ticker=ticker, style=style, reason=reason)

        if available_cash <= 0:
            available_cash = self._refresh_buying_power()
            logger.info("buying power re-fetched [{}]: {:,} KRW", ticker, available_cash)

        check = self._rm.check_buy(ticker, price, verdict.confidence, available_cash,
                                   style=style, atr=atr)
        if not check.allowed:
            logger.warning("BUY blocked [{}][{}]: {}", style, ticker, check.reason)
            self._save_order(ticker, "BUY", 0, price, "BLOCKED", check.reason,
                             strategy=style, reject_msg=check.reason)
            return OrderResult(ok=False, action="BLOCKED", ticker=ticker, style=style, reason=check.reason)

        qty = check.qty
        order_id = str(uuid.uuid4())[:8]
        try:
            pos_before_qty = self._holdings_to_qty_map(self._kw.get_holdings()).get(ticker, 0)
        except Exception:
            pos_before_qty = 0

        self._sending.add(ticker)
        ret = self._kw.send_order(
            rq_name    = f"BUY_{ticker}_{order_id}",
            scr_no     = "2000",
            acc_no     = self._acc,
            order_type = 1,
            code       = ticker,
            qty        = qty,
            price      = int(price) if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        broker_ord_no = getattr(self._kw, "last_ord_no", "") or ""
        broker_msg    = getattr(self._kw, "last_reject_msg", "") or ""

        if ret != 0:
            reject = broker_msg or f"ret={ret}"
            logger.error("BUY send failed: {} | ret={} | {}", ticker, ret, reject)
            self._save_order(ticker, "BUY", qty, price, "ERROR", f"ret={ret}", order_id,
                             strategy=style, reject_msg=reject)
            self._sending.discard(ticker)
            return OrderResult(ok=False, action="ERROR", ticker=ticker, qty=qty, price=price,
                               order_id=order_id, style=style, reason=reject)

        logger.success("BUY sent [{}]: {} x{} @{:,.0f} | ATR={:.0f} | broker_ord={}",
                       style, ticker, qty, price, atr, broker_ord_no)
        self._save_order(ticker, "BUY", qty, price, "SENT", verdict.reason, order_id,
                         strategy=style, broker_ord_no=broker_ord_no)

        pending = _Pending(
            side="BUY", ticker=ticker, qty=qty, price=price, order_id=order_id,
            style=style, verdict=verdict, atr=float(atr or 0),
            pos_before_qty=pos_before_qty, sent_at=time.time(),
            broker_ord_no=broker_ord_no,
        )

        snap = self._poll_for_fill(pending)
        if snap.filled_qty > 0:
            self._apply_buy_increment(pending, snap.filled_qty, snap.avg_price or pending.price)

        if snap.filled:
            self._update_order_fields(
                order_id, status="FILLED",
                filled_qty=snap.filled_qty,
                avg_fill_price=float(snap.avg_price or price),
            )
            self._sending.discard(ticker)
            return OrderResult(
                ok=True, action="BUY", ticker=ticker, qty=qty,
                filled_qty=snap.filled_qty, price=snap.avg_price or price,
                order_id=order_id, style=style, reason=verdict.reason or "",
                filled=True, is_partial=False,
            )

        self._pending[ticker] = pending
        self._sending.discard(ticker)
        is_partial = snap.filled_qty > 0
        if is_partial:
            self._update_order_fields(
                order_id, status="PARTIAL_FILLED",
                filled_qty=pending.filled_qty,
                avg_fill_price=float(pending.avg_fill_price or pending.price),
            )
        logger.info(
            "BUY post-send {} - pending registered: {} | filled {}/{} (timeout {}s)",
            "partial" if is_partial else "no fill",
            ticker, pending.filled_qty, qty, pending.timeout_sec,
        )
        return OrderResult(
            ok=True, action="BUY", ticker=ticker, qty=qty,
            filled_qty=pending.filled_qty,
            price=pending.avg_fill_price or price,
            order_id=order_id, style=style, reason=verdict.reason or "",
            filled=False, pending=True, is_partial=is_partial,
        )

    # -- internal: SELL ----------------------------------------------------

    def _sell(self, ticker, price, hoga, verdict) -> OrderResult:
        pos_before = self._rm.get_positions().get(ticker)
        check = self._rm.check_sell(ticker)
        if not check.allowed and self._restore_position_from_broker(ticker):
            check = self._rm.check_sell(ticker)
            pos_before = self._rm.get_positions().get(ticker)
        if not check.allowed:
            logger.warning("SELL blocked [{}]: {}", ticker, check.reason)
            return OrderResult(ok=False, action="BLOCKED", ticker=ticker,
                               style=(pos_before.style if pos_before else ""),
                               reason=check.reason)

        qty = check.qty
        entry_price_at_send = float(pos_before.avg_price) if pos_before else 0.0
        order_id = str(uuid.uuid4())[:8]
        try:
            pos_before_qty = self._holdings_to_qty_map(self._kw.get_holdings()).get(ticker, 0)
        except Exception:
            pos_before_qty = qty

        self._sending.add(ticker)
        ret = self._kw.send_order(
            rq_name    = f"SELL_{ticker}_{order_id}",
            scr_no     = "2001",
            acc_no     = self._acc,
            order_type = 2,
            code       = ticker,
            qty        = qty,
            price      = int(price) if hoga == "00" else 0,
            hoga_gb    = hoga,
        )
        broker_ord_no = getattr(self._kw, "last_ord_no", "") or ""
        broker_msg    = getattr(self._kw, "last_reject_msg", "") or ""

        if ret != 0:
            reject = broker_msg or f"ret={ret}"
            logger.error("SELL send failed: {} | ret={} | {}", ticker, ret, reject)
            self._save_order(ticker, "SELL", qty, price, "ERROR", f"ret={ret}", order_id,
                             strategy=(pos_before.style if pos_before else ""),
                             reject_msg=reject)
            self._sending.discard(ticker)
            return OrderResult(ok=False, action="ERROR", ticker=ticker, qty=qty, price=price,
                               order_id=order_id,
                               style=(pos_before.style if pos_before else ""),
                               reason=reject)

        logger.success("SELL sent: {} x{} @{:,} | broker_ord={}",
                       ticker, qty, price, broker_ord_no)
        self._save_order(ticker, "SELL", qty, price, "SENT", verdict.reason, order_id,
                         strategy=(pos_before.style if pos_before else ""),
                         broker_ord_no=broker_ord_no)

        pending = _Pending(
            side="SELL", ticker=ticker, qty=qty, price=price, order_id=order_id,
            style=(pos_before.style if pos_before else ""), verdict=verdict, atr=0.0,
            pos_before_qty=pos_before_qty, sent_at=time.time(),
            entry_price_at_send=entry_price_at_send,
            broker_ord_no=broker_ord_no,
        )

        snap = self._poll_for_fill(pending)
        if snap.filled_qty > 0:
            self._apply_sell_increment(pending, snap.filled_qty, price)

        if snap.filled:
            self._record_sell_exit(pending)
            self._update_order_fields(
                order_id, status="FILLED",
                filled_qty=snap.filled_qty,
                avg_fill_price=float(price),
                realized_pnl=float(pending.realized_pnl_so_far or 0.0),
            )
            self._sending.discard(ticker)
            return OrderResult(
                ok=True, action="SELL", ticker=ticker, qty=qty,
                filled_qty=snap.filled_qty, price=price, order_id=order_id,
                style=pending.style, reason=verdict.reason or "",
                filled=True, is_partial=False,
                pnl=pending.realized_pnl_so_far,
            )

        self._pending[ticker] = pending
        self._sending.discard(ticker)
        is_partial = snap.filled_qty > 0
        if is_partial:
            self._update_order_fields(
                order_id, status="PARTIAL_FILLED",
                filled_qty=pending.filled_qty,
                avg_fill_price=float(price),
                realized_pnl=float(pending.realized_pnl_so_far or 0.0),
            )
        logger.info(
            "SELL post-send {} - pending registered: {} | filled {}/{} (timeout {}s)",
            "partial" if is_partial else "no fill",
            ticker, pending.filled_qty, qty, pending.timeout_sec,
        )
        return OrderResult(
            ok=True, action="SELL", ticker=ticker, qty=qty,
            filled_qty=pending.filled_qty, price=price,
            order_id=order_id, style=pending.style,
            reason=verdict.reason or "",
            filled=False, pending=True, is_partial=is_partial,
            pnl=pending.realized_pnl_so_far if is_partial else None,
        )

    # -- fill increments / record helpers ----------------------------------

    def _apply_buy_increment(self, p: _Pending, new_total_filled: int, fill_price: float) -> None:
        delta = new_total_filled - p.filled_qty
        if delta <= 0:
            return
        self._rm.increment_position(p.ticker, p.ticker, delta, fill_price,
                                    style=p.style, atr=p.atr)
        # update pending counter BEFORE first-fill recording so the recorded qty
        # reflects the actual cumulative filled amount (fixes partial-fill journal qty)
        p.filled_qty     = new_total_filled
        p.avg_fill_price = fill_price
        self._update_order_fields(
            p.order_id, filled_qty=p.filled_qty, avg_fill_price=float(fill_price or 0),
        )
        if not p.notified_first_fill:
            self._record_first_buy_fill(p, fill_price)
            p.notified_first_fill = True

    def _apply_sell_increment(self, p: _Pending, new_total_filled: int, sell_price: float) -> None:
        delta = new_total_filled - p.filled_qty
        if delta <= 0:
            return
        pnl_inc = self._rm.partial_close(p.ticker, delta, sell_price)
        p.filled_qty          = new_total_filled
        p.realized_pnl_so_far = (p.realized_pnl_so_far or 0.0) + (pnl_inc or 0.0)
        self._update_order_fields(
            p.order_id,
            filled_qty=p.filled_qty,
            avg_fill_price=float(sell_price or 0),
            realized_pnl=float(p.realized_pnl_so_far or 0.0),
        )

    def _record_first_buy_fill(self, p: _Pending, fill_price: float) -> None:
        """
        First-fill side effects (DayTradeJournal entry, AI tracker entry).
        Uses p.filled_qty (cumulative filled at this point) instead of p.qty,
        so partial fills are journaled with the actual filled amount.
        """
        v = p.verdict
        if p.style == STYLE_DAY:
            try:
                self._day_journal.record_entry(
                    ticker=p.ticker, qty=p.filled_qty, price=fill_price,
                    strategy=getattr(v, "position_size", "") or "",
                    confidence=float(v.confidence or 0),
                    atr=float(p.atr or 0),
                    reason=v.reason or "",
                )
            except Exception as e:
                logger.warning("DayTradeJournal record_entry failed: {}", e)
        try:
            from core.ai_accuracy_tracker import AIAccuracyTracker, AISignalRecord
            AIAccuracyTracker().record_entry(AISignalRecord(
                ticker=p.ticker, name=p.ticker,
                entry_at=datetime.now().isoformat(),
                entry_price=fill_price,
                ai_action=v.action,
                ai_confidence=float(v.confidence or 0),
                ai_reason=(v.reason or "")[:200],
                setup_type=getattr(v, "setup_type", ""),
                composite=getattr(v, "composite", 0.0),
                tech_score=getattr(v, "tech_score", 0.0),
                fund_passed=bool(getattr(v, "fund_passed", True)),
                regime=getattr(v, "regime", ""),
            ))
        except Exception:
            pass

    def _record_sell_exit(self, p: _Pending) -> None:
        if p.recorded_exit or p.filled_qty <= 0:
            return
        if p.style == STYLE_DAY:
            try:
                self._day_journal.record_exit(
                    ticker=p.ticker, qty=p.filled_qty,
                    entry_price=p.entry_price_at_send,
                    exit_price=p.price,
                    pnl=float(p.realized_pnl_so_far or 0),
                    converted=False,
                    reason=p.verdict.reason or "",
                )
            except Exception as e:
                logger.warning("DayTradeJournal record_exit failed: {}", e)
        try:
            from core.ai_accuracy_tracker import AIAccuracyTracker
            AIAccuracyTracker().record_exit(p.ticker, p.price)
        except Exception:
            pass
        p.recorded_exit = True

    # -- short polling -----------------------------------------------------

    def _poll_for_fill(self, p: _Pending) -> FillSnapshot:
        deadline = time.time() + self.POLL_MAX_WAIT
        last_filled = 0
        last_avg    = p.price
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            try:
                h = self._kw.get_holdings()
            except Exception:
                continue
            qmap = self._holdings_to_qty_map(h)
            cur = qmap.get(p.ticker, 0)
            if p.side == "BUY":
                filled = max(0, cur - p.pos_before_qty)
            else:
                filled = max(0, p.pos_before_qty - cur)
            if filled > last_filled:
                last_filled = filled
                avg = self._holdings_avg_price(h, p.ticker)
                if avg > 0:
                    last_avg = avg
            if filled >= p.qty:
                return FillSnapshot(filled=True, filled_qty=filled, avg_price=last_avg)
        return FillSnapshot(filled=False, filled_qty=last_filled, avg_price=last_avg)

    # -- classification helpers --------------------------------------------

    @staticmethod
    def _classify_cancel(
        *,
        broker_called: bool,
        broker_ok:     bool,
        filled_qty:    int,
        total_qty:     int,
        remaining:     int,
    ) -> tuple[str, str]:
        """Map (called, ok, filled, remaining) -> (db_status, OrderResult.action)."""
        if remaining <= 0:
            if filled_qty >= total_qty:
                return ("FILLED", "FILLED")
            if filled_qty > 0:
                return ("PARTIAL_FILLED", "PARTIAL")
            return ("CANCELLED", "CANCELLED")
        if not broker_called:
            return ("MEMORY_CLEARED", "MEMORY_CLEARED")
        if broker_ok:
            return (("PARTIAL_CANCELLED" if filled_qty > 0 else "CANCELLED"),
                    "CANCELLED")
        return ("CANCEL_FAILED", "CANCEL_FAILED")

    @staticmethod
    def _cancel_reason(*, has_api: bool, has_ord_no: bool,
                        broker_ok: bool, remaining: int) -> str:
        if remaining <= 0:
            return "no remainder (already filled or nothing to cancel)"
        if not has_api:
            return "kw.cancel_order missing - broker side may still be open"
        if not has_ord_no:
            return "broker ord_no unknown - broker side may still be open"
        if broker_ok:
            return "broker cancel ok"
        return "broker cancel FAILED - manual check required"

    # -- helpers -----------------------------------------------------------

    def _holdings_to_qty_map(self, holdings: list[dict]) -> dict[str, int]:
        result: dict[str, int] = {}
        for h in holdings or []:
            t = h.get("ticker") or h.get("code") or ""
            if not t:
                continue
            qty = int(h.get("qty") or 0)
            result[t] = qty
            short = t.replace(".KS", "").replace(".KQ", "")
            if short and short != t:
                result[short] = qty
        return result

    def _holdings_avg_price(self, holdings: list[dict], ticker: str) -> float:
        short = ticker.replace(".KS", "").replace(".KQ", "")
        for h in holdings or []:
            t = h.get("ticker") or h.get("code") or ""
            if t == ticker or t == short:
                return float(h.get("avg_price") or h.get("cur_price") or 0)
        return 0.0

    def _refresh_buying_power(self) -> int:
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
            logger.warning("buying-power re-fetch failed: {}", e)
            return 0

    def _restore_position_from_broker(self, ticker: str) -> bool:
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
                    ticker, h.get("name") or ticker, qty,
                    avg or float(h.get("cur_price") or 0),
                    style=self._infer_style_for_ticker(ticker),
                )
                logger.warning("position restored from broker [{}]: {}x @{:,.0f}",
                               ticker, qty, avg)
                return True
        except Exception as e:
            logger.warning("position restore failed [{}]: {}", ticker, e)
        return False

    def _infer_style_for_ticker(self, ticker: str) -> str:
        try:
            with sqlite3.connect(DB_PATH) as con:
                row = con.execute(
                    "SELECT strategy, reason FROM orders WHERE ticker=? ORDER BY id DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
        except Exception:
            row = None
        strategy = str(row[0] or "").lower() if row else ""
        reason   = str(row[1] or "").lower() if row else ""
        merged = f"{strategy} {reason}"
        if "longterm" in merged or "[longterm]" in merged:
            return "longterm"
        if "daytrading" in merged or "[daytrading]" in merged:
            return STYLE_DAY
        return "longterm"

    # -- DB ----------------------------------------------------------------

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
            # broker round-trip details (idempotent ALTER for older DBs)
            existing = {r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()}
            for col, ddl in (
                ("broker_ord_no",  "TEXT DEFAULT ''"),
                ("filled_qty",     "INTEGER DEFAULT 0"),
                ("avg_fill_price", "REAL DEFAULT 0"),
                ("reject_msg",     "TEXT DEFAULT ''"),
                # SELL 체결 시 누적 실현손익. BUY 행은 항상 0.
                ("realized_pnl",   "REAL DEFAULT 0"),
            ):
                if col not in existing:
                    con.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
        logger.debug("orders table initialized")

    def _save_order(
        self,
        ticker: str,
        order_type: str,
        qty: int,
        price: float,
        status: str,
        reason: str,
        order_id: str = "",
        strategy: str = "",
        broker_ord_no: str = "",
        reject_msg: str = "",
    ) -> None:
        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
        stored_price = int(round(price)) if is_kr else round(float(price), 4)
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO orders "
                "(order_id,timestamp,ticker,order_type,qty,price,status,reason,strategy,"
                " broker_ord_no,filled_qty,avg_fill_price,reject_msg) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order_id, datetime.now().isoformat(),
                    ticker, order_type, qty, stored_price,
                    status, reason, strategy,
                    broker_ord_no or "", 0, 0.0, reject_msg or "",
                ),
            )

    _ALLOWED_UPDATE_FIELDS = (
        "status", "filled_qty", "avg_fill_price",
        "broker_ord_no", "reject_msg", "realized_pnl",
    )
    _STATUS_UPDATABLE = ("SENT", "PARTIAL_FILLED", "UNFILLED")

    def _update_order_status(self, order_id: str, new_status: str) -> None:
        if not order_id:
            return
        try:
            with sqlite3.connect(DB_PATH) as con:
                con.execute(
                    "UPDATE orders SET status=? "
                    "WHERE order_id=? AND status IN ('SENT','PARTIAL_FILLED','UNFILLED')",
                    (new_status, order_id),
                )
        except Exception as e:
            logger.warning("orders status update failed: {}", e)

    def _update_order_fields(
        self, order_id: str, *, only_if_open: bool = True, **fields,
    ) -> None:
        """Update one or more orders columns by order_id.

        Fields are whitelisted via _ALLOWED_UPDATE_FIELDS. By default a
        defensive WHERE clause prevents overwriting terminal rows; the
        startup reconciler passes only_if_open=False to override.
        """
        if not order_id or not fields:
            return
        clean = {k: v for k, v in fields.items() if k in self._ALLOWED_UPDATE_FIELDS}
        if not clean:
            return
        sql = "UPDATE orders SET " + ",".join(f"{k}=?" for k in clean) + " WHERE order_id=?"
        params = [*clean.values(), order_id]
        if only_if_open:
            placeholders = ",".join("?" for _ in self._STATUS_UPDATABLE)
            sql += f" AND status IN ({placeholders})"
            params.extend(self._STATUS_UPDATABLE)
        try:
            with sqlite3.connect(DB_PATH) as con:
                con.execute(sql, params)
        except Exception as e:
            logger.warning("orders fields update failed [{}]: {}", order_id, e)
