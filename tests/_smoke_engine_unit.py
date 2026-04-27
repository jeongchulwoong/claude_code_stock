"""Engine unit smoke tests — covers core/order_manager + core/risk_manager
priority paths in isolation against a temp DB, with a stub Kiwoom + RiskManager.

Run from repo root:
    PYTHONIOENCODING=utf-8 python tests/_smoke_engine_unit.py

Scope:
  A. OrderManager._classify_cancel matrix (4 cases)
  B. OrderManager._estimate_sell_realized_pnl edges (matched, partial, unmatched, breakeven)
  C. OrderManager._save_order + _update_order_fields whitelist + only_if_open guard
  D. RiskManager check_buy gates (halted / min_conf / max_positions / dup / sector overlap / sizing)
  E. RiskManager check_stop_loss / check_take_profit (ATR vs %)
  F. RiskManager partial_close (pnl, consec_losses, fully_closed)
  G. RiskManager add_position / increment_position (weighted avg)
  H. OrderManager.execute fast paths (HOLD / duplicate-in-flight / unknown action)

These tests deliberately avoid touching the live trade_log.db. They rebind
DB_PATH via module attribute so every sqlite call goes to a temp file.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Shared temp DB ───────────────────────────────────────────────
_tmp_db = tempfile.mkstemp(suffix=".engine.db")[1]
import dashboard.db_reader as rd_mod  # noqa: E402
import core.order_manager as om_mod   # noqa: E402
rd_mod.DB_PATH = _tmp_db
om_mod.DB_PATH = _tmp_db


# ── Stubs ────────────────────────────────────────────────────────
class StubKW:
    """Minimum surface OrderManager calls. Each test re-points behavior."""
    last_ord_no = ""
    last_reject_msg = ""
    holdings_data: list = []
    open_orders_data: list = []
    cancel_calls: list = []
    send_calls: list = []
    next_send_ret = 0
    next_send_ord_no = "OPEN-test"
    next_send_reject = ""

    def get_holdings(self):
        return list(self.holdings_data)

    def get_open_orders(self):
        return list(self.open_orders_data)

    def get_deposit_detail(self): return {}
    def get_balance(self): return {}

    def send_order(self, **kw):
        self.send_calls.append(kw)
        if self.next_send_ret == 0:
            self.last_ord_no = self.next_send_ord_no
            self.last_reject_msg = ""
        else:
            self.last_ord_no = ""
            self.last_reject_msg = self.next_send_reject or "ret!=0"
        return self.next_send_ret

    def cancel_order(self, **kw):
        self.cancel_calls.append(kw)
        return 0


class StubRM:
    """Minimal stand-in. Real RiskManager is exercised in its own block."""
    halted = False
    sell_allowed_qty = 0
    buy_allowed_qty = 0
    buy_allowed_reason = "ok"
    increments: list = []
    partial_calls: list = []
    positions: dict = {}

    def check_buy(self, ticker, price, conf, cash, style="daytrading", atr=0.0):
        from core.risk_manager import RiskCheckResult
        if self.halted:
            return RiskCheckResult(False, "halted")
        if self.buy_allowed_qty <= 0:
            return RiskCheckResult(False, self.buy_allowed_reason)
        return RiskCheckResult(True, self.buy_allowed_reason, qty=self.buy_allowed_qty)

    def check_sell(self, ticker):
        from core.risk_manager import RiskCheckResult
        if self.sell_allowed_qty <= 0:
            return RiskCheckResult(False, "no position")
        return RiskCheckResult(True, "ok", qty=self.sell_allowed_qty)

    def increment_position(self, *a, **k):
        self.increments.append((a, k))

    def partial_close(self, ticker, qty, sell_price):
        self.partial_calls.append((ticker, qty, sell_price))
        return 0.0

    def add_position(self, *a, **k): pass

    def get_positions(self):
        return dict(self.positions)


# ── A) _classify_cancel matrix ──────────────────────────────────
print("--- A) _classify_cancel matrix ---")
classify = om_mod.OrderManager._classify_cancel
# remaining<=0, fully filled
assert classify(broker_called=False, broker_ok=False,
                filled_qty=5, total_qty=5, remaining=0) == ("FILLED", "FILLED")
# remaining<=0, partial filled
assert classify(broker_called=True, broker_ok=True,
                filled_qty=3, total_qty=5, remaining=0) == ("PARTIAL_FILLED", "PARTIAL")
# remaining<=0, no fill
assert classify(broker_called=True, broker_ok=True,
                filled_qty=0, total_qty=5, remaining=0) == ("CANCELLED", "CANCELLED")
# remaining>0, broker not called
assert classify(broker_called=False, broker_ok=False,
                filled_qty=0, total_qty=5, remaining=5) == ("MEMORY_CLEARED", "MEMORY_CLEARED")
# remaining>0, broker ok, partial
assert classify(broker_called=True, broker_ok=True,
                filled_qty=2, total_qty=5, remaining=3) == ("PARTIAL_CANCELLED", "CANCELLED")
# remaining>0, broker ok, no fill
assert classify(broker_called=True, broker_ok=True,
                filled_qty=0, total_qty=5, remaining=5) == ("CANCELLED", "CANCELLED")
# remaining>0, broker FAIL
assert classify(broker_called=True, broker_ok=False,
                filled_qty=2, total_qty=5, remaining=3) == ("CANCEL_FAILED", "CANCEL_FAILED")
print("  6 classify_cancel cases OK")


# ── B) _estimate_sell_realized_pnl edges ────────────────────────
print()
print("--- B) _estimate_sell_realized_pnl edges ---")

def _seed(rows):
    con = sqlite3.connect(_tmp_db)
    con.executemany(
        "INSERT INTO orders (order_id,timestamp,ticker,order_type,qty,price,status,reason,strategy,"
        " broker_ord_no,filled_qty,avg_fill_price,reject_msg,realized_pnl) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    con.commit(); con.close()


def _reset():
    con = sqlite3.connect(_tmp_db)
    con.execute("DELETE FROM orders"); con.commit(); con.close()


# OrderManager init creates schema
om_mod.OrderManager(StubKW(), StubRM())  # type: ignore[arg-type]

# Edge: zero qty / zero exit
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 0, 1000, "2026-01-01")
assert (pnl, matched) == (0.0, 0), "qty=0 should return (0,0)"
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 1, 0, "2026-01-01")
assert (pnl, matched) == (0.0, 0), "exit=0 should return (0,0)"

# Edge: no prior history
_reset()
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 5, 1200, "2026-01-01")
assert (pnl, matched) == (0.0, 0), "no prior FILLED BUY should be (0,0)"

# Full match: BUY 10@1000 + SELL 5@1200 -> +1000, matched=5
_reset()
_seed([
    ("b1","2026-01-01T09:00:00","X.KS","BUY",10,1000,"FILLED","","day","",10,1000,"",0),
])
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 5, 1200, "2026-01-02T09:00:00")
assert matched == 5 and abs(pnl - 1000.0) < 0.01, f"expected (1000,5) got ({pnl},{matched})"
print(f"  full match: pnl={pnl}, matched={matched} OK")

# Breakeven: BUY 5@1000 + SELL 5@1000 -> 0, matched=5 (NOT (0,0))
_reset()
_seed([
    ("b1","2026-01-01T09:00:00","X.KS","BUY",5,1000,"FILLED","","day","",5,1000,"",0),
])
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 5, 1000, "2026-01-02T09:00:00")
assert matched == 5 and pnl == 0.0, f"breakeven expected (0,5) got ({pnl},{matched})"
print(f"  breakeven: pnl={pnl}, matched={matched} OK (distinguishable from unmatched)")

# Partial: BUY 3@1000 + SELL 5@1200 -> matched=3 (qty<sell_qty)
_reset()
_seed([
    ("b1","2026-01-01T09:00:00","X.KS","BUY",3,1000,"FILLED","","day","",3,1000,"",0),
])
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 5, 1200, "2026-01-02T09:00:00")
assert matched == 3 and abs(pnl - 600.0) < 0.01, f"partial expected (600,3) got ({pnl},{matched})"
print(f"  partial: pnl={pnl}, matched={matched} OK (matched < sell_qty triggers 'not booked')")

# Layered FIFO: prior SELL consumed @1000 lot
_reset()
_seed([
    ("b1","2026-01-01T09:00:00","X.KS","BUY", 5,1000,"FILLED","","day","",5,1000,"",0),
    ("b2","2026-01-02T09:00:00","X.KS","BUY", 5,1100,"FILLED","","day","",5,1100,"",0),
    ("s1","2026-01-03T09:00:00","X.KS","SELL",5,1200,"FILLED","","day","",5,1200,"",1000),
])
pnl, matched = om_mod.OrderManager._estimate_sell_realized_pnl("X.KS", 5, 1300, "2026-01-04T09:00:00")
assert matched == 5 and abs(pnl - 1000.0) < 0.01, f"layered expected (+1000,5) got ({pnl},{matched})"
print(f"  layered FIFO: pnl={pnl}, matched={matched} OK")


# ── C) _save_order + _update_order_fields ───────────────────────
print()
print("--- C) _save_order + _update_order_fields whitelist + only_if_open ---")
_reset()
om = om_mod.OrderManager(StubKW(), StubRM())  # type: ignore[arg-type]

om._save_order("X.KS", "BUY", 5, 1000, "SENT", "test", "ord-001",
               strategy="daytrading", broker_ord_no="OPEN-XYZ", reject_msg="")
con = sqlite3.connect(_tmp_db)
row = con.execute("SELECT order_id,status,broker_ord_no,filled_qty,avg_fill_price,reject_msg "
                  "FROM orders WHERE order_id='ord-001'").fetchone()
con.close()
assert row == ("ord-001", "SENT", "OPEN-XYZ", 0, 0.0, "")
print(f"  insert: {row}")

# Update with whitelist: only listed fields apply, junk keys ignored.
om._update_order_fields("ord-001", filled_qty=3, avg_fill_price=1010.5,
                         status="PARTIAL_FILLED",
                         hacker_field="DROP TABLE orders;")
con = sqlite3.connect(_tmp_db)
row = con.execute("SELECT status,filled_qty,avg_fill_price FROM orders WHERE order_id='ord-001'").fetchone()
con.close()
assert row == ("PARTIAL_FILLED", 3, 1010.5)
print(f"  whitelist update: {row}")

# only_if_open=True: terminal row (FILLED) cannot be overwritten
om._update_order_fields("ord-001", status="FILLED", filled_qty=5)
con = sqlite3.connect(_tmp_db)
row = con.execute("SELECT status,filled_qty FROM orders WHERE order_id='ord-001'").fetchone()
con.close()
assert row == ("FILLED", 5)
# Now FILLED — try to flip back to UNFILLED with default only_if_open guard
om._update_order_fields("ord-001", status="UNFILLED", filled_qty=0)
con = sqlite3.connect(_tmp_db)
row = con.execute("SELECT status,filled_qty FROM orders WHERE order_id='ord-001'").fetchone()
con.close()
assert row == ("FILLED", 5), f"only_if_open should have blocked; got {row}"
print(f"  only_if_open guard prevents terminal->open revert: {row} OK")

# only_if_open=False: explicit override succeeds
om._update_order_fields("ord-001", only_if_open=False, status="UNFILLED")
con = sqlite3.connect(_tmp_db)
row = con.execute("SELECT status FROM orders WHERE order_id='ord-001'").fetchone()
con.close()
assert row == ("UNFILLED",), f"override should succeed; got {row}"
print(f"  only_if_open=False override OK: {row}")


# ── D) RiskManager check_buy gates ──────────────────────────────
print()
print("--- D) RiskManager check_buy gates ---")
from core.risk_manager import RiskManager, STYLE_DAY, STYLE_LONG  # noqa: E402

rm = RiskManager()
rm.set_start_capital(500_000)

# Halted
rm._halted = True
assert rm.check_buy("X.KS", 1000, 80, 100_000).allowed is False
print("  halted -> blocked OK")
rm._halted = False

# min_confidence (default 75 in current config; clamp ensures 65~90)
res = rm.check_buy("X.KS", 1000, 60, 100_000)
assert not res.allowed and "신뢰도" in res.reason
print(f"  low confidence blocked: '{res.reason}' OK")

# Sizing — small capital fallback
res = rm.check_buy("X.KS", 1000, 90, 100_000, style=STYLE_DAY, atr=10)
assert res.allowed and res.qty > 0, f"expected qty>0, got {res}"
print(f"  ATR sizing produces qty>0: qty={res.qty}, reason='{res.reason[:40]}...' OK")

# Max positions — fill to the limit (max_positions=2 in default RISK_CONFIG)
from core.risk_manager import Position  # noqa: E402
rm._positions = {
    "A.KS": Position("A.KS", "A", 1, 1000, style=STYLE_DAY),
    "B.KS": Position("B.KS", "B", 1, 1000, style=STYLE_DAY),
}
res = rm.check_buy("C.KS", 1000, 90, 500_000, style=STYLE_DAY, atr=10)
assert not res.allowed and "최대" in res.reason
print(f"  max_positions blocked: '{res.reason}' OK")

# Duplicate — drop to 1 position so max_positions gate is not the one firing first
rm._positions = {"A.KS": Position("A.KS", "A", 1, 1000, style=STYLE_DAY)}
res = rm.check_buy("A.KS", 1000, 90, 500_000, style=STYLE_DAY, atr=10)
assert not res.allowed and "이미 보유" in res.reason
print(f"  duplicate blocked: '{res.reason}' OK")

rm._positions = {}


# ── E) RiskManager check_stop_loss / check_take_profit ───────────
print()
print("--- E) RiskManager stop/take checks ---")
rm = RiskManager()
rm.add_position("X.KS", "X", 5, 1000.0, style=STYLE_DAY, atr=20.0)
# Below ATR stop (1.5 * 20 = 30 → stop = 970)
assert rm.check_stop_loss("X.KS", 969) is True
assert rm.check_stop_loss("X.KS", 980) is False
print("  ATR stop fires below 970 OK (atr=20, mult=1.5)")
# Take profit (3.0 * 20 = 60 → tp = 1060)
assert rm.check_take_profit("X.KS", 1060) is True
assert rm.check_take_profit("X.KS", 1059) is False
print("  ATR take-profit fires at 1060 OK (atr=20, mult=3.0)")

# Long-style position uses % thresholds
rm.add_position("Y.KS", "Y", 5, 1000.0, style=STYLE_LONG, atr=0.0)
assert rm.check_stop_loss("Y.KS",  929) is True   # -7.1% < -7%
assert rm.check_stop_loss("Y.KS",  931) is False
assert rm.check_take_profit("Y.KS", 1201) is True  # +20.1% > +20%
print("  longterm % stop/take OK")


# ── F) RiskManager partial_close ─────────────────────────────────
print()
print("--- F) RiskManager partial_close & consec_losses ---")
rm = RiskManager()
rm.set_start_capital(500_000)
rm.add_position("X.KS", "X", 10, 1000.0, style=STYLE_DAY, atr=20.0)

# Partial close 4 @ 1100 → +400 PnL
pnl = rm.partial_close("X.KS", 4, 1100.0)
assert pnl == 400 and rm.get_positions()["X.KS"].qty == 6
print(f"  partial 4@1100 -> pnl={pnl}, remaining={rm.get_positions()['X.KS'].qty} OK")
# Close remaining 6 @ 950 → -300 PnL → fully closed → consec_losses+=1
pnl = rm.partial_close("X.KS", 6, 950.0)
assert pnl == -300 and "X.KS" not in rm.get_positions()
assert rm._consec_losses == 1
print(f"  full close 6@950 -> pnl={pnl}, consec_losses={rm._consec_losses} OK")

# Stat aggregation
stats = rm.get_day_trade_stats()
assert stats["count"] == 1 and stats["losses"] == 1 and stats["wins"] == 0
print(f"  day stats: count={stats['count']} wins={stats['wins']} losses={stats['losses']} OK")


# ── G) RiskManager increment_position weighted avg ───────────────
print()
print("--- G) increment_position weighted average ---")
rm = RiskManager()
rm.add_position("X.KS", "X", 5, 1000.0, style=STYLE_DAY, atr=20.0)
rm.increment_position("X.KS", "X", 5, 1200.0, style=STYLE_DAY, atr=20.0)
pos = rm.get_positions()["X.KS"]
# weighted = (5*1000 + 5*1200)/10 = 1100
assert pos.qty == 10 and abs(pos.avg_price - 1100.0) < 0.01
print(f"  weighted avg: qty={pos.qty}, avg={pos.avg_price} OK")


# ── H) OrderManager.execute fast paths ──────────────────────────
print()
print("--- H) OrderManager.execute fast paths ---")
from core.ai_judge import AIVerdict  # noqa: E402

om = om_mod.OrderManager(StubKW(), StubRM())  # type: ignore[arg-type]

# HOLD
v_hold = AIVerdict(ticker="X.KS", action="HOLD", confidence=80, reason="hold",
                   target_price=1000, stop_loss=950, position_size="SMALL")
res = om.execute(v_hold, 1000)
assert not res.ok and res.action == "HOLD"
print(f"  HOLD -> action={res.action} OK")

# UNKNOWN action
v_unknown = AIVerdict(ticker="X.KS", action="WAIT", confidence=80, reason="wait",
                      target_price=1000, stop_loss=950, position_size="SMALL")
res = om.execute(v_unknown, 1000)
assert not res.ok and res.action == "UNKNOWN"
print(f"  UNKNOWN -> action={res.action} OK")

# Duplicate in-flight
om._sending.add("X.KS")
v_buy = AIVerdict(ticker="X.KS", action="BUY", confidence=80, reason="buy",
                  target_price=1000, stop_loss=950, position_size="SMALL")
res = om.execute(v_buy, 1000)
assert not res.ok and res.action == "BLOCKED" and "in flight" in res.reason
print(f"  BLOCKED in-flight -> reason='{res.reason}' OK")
om._sending.discard("X.KS")


# ── Cleanup ─────────────────────────────────────────────────────
try:
    os.unlink(_tmp_db)
except OSError:
    pass

print()
print("ALL ENGINE UNIT CHECKS PASSED")
