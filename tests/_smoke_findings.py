"""Three-finding regression check (run from repo root).

Verifies:
  1. /api/portfolio is admin-only.
  2. SELL recovery during reconcile estimates realized_pnl via FIFO over DB history.
  3. get_daily_pnl always emits today (0 if no SELL closures), preventing blank chart.
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Finding 1 -------------------------------------------------------
print("--- Finding 1: /api/portfolio auth gate ---")
os.environ.setdefault("DASHBOARD_ADMIN_PASSWORD",  "admin-pw-test")
os.environ.setdefault("DASHBOARD_CLIENT_PASSWORD", "client-pw-test")
import importlib, config  # noqa: E402
importlib.reload(config)
import dashboard.app as dapp  # noqa: E402
importlib.reload(dapp)
dapp.app.config["TESTING"] = True

with dapp.app.test_client() as c:
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "client"
    r = c.get("/api/portfolio")
    print("  client -> /api/portfolio:", r.status_code, (r.get_json() or {}).get("error"))
    assert r.status_code == 403

with dapp.app.test_client() as c:
    r = c.get("/api/portfolio")
    print("  anon   -> /api/portfolio:", r.status_code)
    assert r.status_code == 401


class FakeKW:
    def login(self): return True
    def get_balance(self):
        return {"output2": [{
            "tot_evlu_amt": 100000, "tot_pur_amt": 95000, "tot_evlt_pl": 5000,
            "buying_power": 50000, "entr": 50000, "d2_entra": 50000,
        }]}
    def get_holdings(self):
        return [{
            # KiwoomRestAPI.get_holdings() returns ticker but not code; /api/portfolio
            # must derive code from ticker for sector mapping and display.
            "ticker": "105560.KS", "name": "KB금융",
            "qty": 1, "avg_price": 155900, "cur_price": 157000,
            "eval_amt": 157046, "pnl": 1146, "pnl_rate": 0.73,
        }]


dapp._kiwoom_singleton["kw"] = FakeKW()
dapp._portfolio_cache.update({"t": 0, "data": None})
with dapp.app.test_client() as c:
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "admin"
    r = c.get("/api/portfolio")
    body = r.get_json() or {}
    print("  admin  -> /api/portfolio:", r.status_code,
          "tot_evlt_pl=", body.get("tot_evlt_pl"),
          "holdings_count=", body.get("holdings_count"))
    assert r.status_code == 200 and body.get("ok")
    assert body["holdings"][0]["code"] == "105560"
    assert body["sectors"][0]["sector"] == "금융"


# Finding 2 -------------------------------------------------------
print()
print("--- Finding 2: SELL recovery estimates realized_pnl via FIFO ---")
tmp = tempfile.mkstemp(suffix=".db")[1]
import core.order_manager as om_mod  # noqa: E402
import dashboard.db_reader as rd_mod  # noqa: E402
om_mod.DB_PATH = tmp
rd_mod.DB_PATH = tmp


class FakeKW2:
    last_ord_no = ""
    last_reject_msg = ""
    def get_holdings(self): return []
    def get_open_orders(self): return []


class FakeRM2:
    def increment_position(self, *a, **k): pass
    def partial_close(self, *a, **k): return 0.0
    def add_position(self, *a, **k): pass
    def get_positions(self): return {}
    def get_positions_by_style(self, *a): return {}


om = om_mod.OrderManager(FakeKW2(), FakeRM2())

INSERT = (
    "INSERT INTO orders (order_id,timestamp,ticker,order_type,qty,price,status,reason,strategy,"
    " broker_ord_no,filled_qty,avg_fill_price,reject_msg,realized_pnl) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def seed(rows):
    con = sqlite3.connect(tmp)
    con.executemany(INSERT, rows)
    con.commit()
    con.close()


def reset():
    con = sqlite3.connect(tmp)
    con.execute("DELETE FROM orders")
    con.commit()
    con.close()


# Scenario A: BUY 5@1000 + BUY 5@1100 + recovered SELL 5@1200 -> FIFO consumes @1000 lot.
seed([
    ("seed-b1", "2026-04-25T09:00:00", "005930.KS", "BUY", 5, 1000, "FILLED", "", "daytrading", "", 5, 1000, "", 0),
    ("seed-b2", "2026-04-26T09:00:00", "005930.KS", "BUY", 5, 1100, "FILLED", "", "daytrading", "", 5, 1100, "", 0),
    ("seed-s1", "2026-04-27T09:00:00", "005930.KS", "SELL", 5, 1200, "SENT",   "", "daytrading", "OPEN-100", 0, 0, "", 0),
])
print("  summary:", om.reconcile_persisted_orders(lookback_days=10))
con = sqlite3.connect(tmp)
row = con.execute(
    "SELECT status, filled_qty, avg_fill_price, realized_pnl, reject_msg "
    "FROM orders WHERE order_id='seed-s1'"
).fetchone()
con.close()
print("  recovered SELL row -> status=%s qty=%s avg=%s pnl=%s msg=%s" % row)
assert row[0] == "FILLED"
assert row[1] == 5
assert abs(row[2] - 1200) < 0.5
assert abs(row[3] - 1000.0) < 0.5, "expected ~1000, got %s" % row[3]
print("  -> FIFO @1000 lot consumed first; realized_pnl=+1,000 OK")

# Scenario B: orphan SELL (no prior BUY).
con = sqlite3.connect(tmp)
con.execute(
    INSERT,
    ("seed-orphan", "2026-04-27T10:00:00", "999999.KS", "SELL", 3, 500, "SENT",
     "", "daytrading", "", 0, 0, "", 0),
)
con.commit(); con.close()
om.reconcile_persisted_orders(lookback_days=10)
con = sqlite3.connect(tmp)
row = con.execute(
    "SELECT status, realized_pnl, reject_msg FROM orders WHERE order_id='seed-orphan'"
).fetchone()
con.close()
print("  orphan SELL row -> status=%s pnl=%s msg=%s" % row)
assert row[0] == "FILLED" and row[1] == 0.0 and "no prior FILLED BUY" in row[2]
print("  -> orphan handled with realized_pnl=0 + note OK")

# Scenario C: prior FILLED SELL already consumed the @1000 lot. New recovered SELL must match @1100.
print()
print("--- Finding 2b: layered SELL respects prior FIFO consumption ---")
reset()
seed([
    ("lb1", "2026-04-20T09:00:00", "005930.KS", "BUY",  5, 1000, "FILLED", "", "daytrading", "", 5, 1000, "", 0),
    ("lb2", "2026-04-21T09:00:00", "005930.KS", "BUY",  5, 1100, "FILLED", "", "daytrading", "", 5, 1100, "", 0),
    ("ls1", "2026-04-22T09:00:00", "005930.KS", "SELL", 5, 1200, "FILLED", "", "daytrading", "", 5, 1200, "", 1000),
    ("ls2", "2026-04-23T09:00:00", "005930.KS", "SELL", 5, 1300, "SENT",   "", "daytrading", "OPEN-200", 0, 0, "", 0),
])
om.reconcile_persisted_orders(lookback_days=10)
con = sqlite3.connect(tmp)
row = con.execute(
    "SELECT status, realized_pnl FROM orders WHERE order_id='ls2'"
).fetchone()
con.close()
print("  ls2 ->", row)
assert row[0] == "FILLED"
assert abs(row[1] - 1000.0) < 0.5, "expected ~1000 (matched @1100 lot), got %s" % row[1]
print("  -> FIFO correctly skipped the @1000 lot already consumed by ls1 OK")

# Scenario D: breakeven SELL must be treated as known pnl=0, not "unknown".
print()
print("--- Finding 2c: breakeven SELL is known zero pnl ---")
reset()
seed([
    ("be-b1", "2026-04-20T09:00:00", "005930.KS", "BUY",  5, 1000, "FILLED", "", "daytrading", "", 5, 1000, "", 0),
    ("be-s1", "2026-04-23T09:00:00", "005930.KS", "SELL", 5, 1000, "SENT",   "", "daytrading", "OPEN-300", 0, 0, "", 0),
])
om.reconcile_persisted_orders(lookback_days=10)
con = sqlite3.connect(tmp)
row = con.execute(
    "SELECT status, realized_pnl, reject_msg FROM orders WHERE order_id='be-s1'"
).fetchone()
con.close()
print("  be-s1 ->", row)
assert row[0] == "FILLED"
assert abs(row[1]) < 0.5
assert "FIFO est" in row[2] and "unknown" not in row[2]
print("  -> breakeven recognized as known realized_pnl=0 OK")

# Scenario E: partial FIFO match is not reliable enough to book into realized_pnl.
print()
print("--- Finding 2d: partial FIFO match is noted but not booked ---")
reset()
seed([
    ("pm-b1", "2026-04-20T09:00:00", "005930.KS", "BUY",  3, 1000, "FILLED", "", "daytrading", "", 3, 1000, "", 0),
    ("pm-s1", "2026-04-23T09:00:00", "005930.KS", "SELL", 5, 1200, "SENT",   "", "daytrading", "OPEN-400", 0, 0, "", 0),
])
om.reconcile_persisted_orders(lookback_days=10)
con = sqlite3.connect(tmp)
row = con.execute(
    "SELECT status, realized_pnl, reject_msg FROM orders WHERE order_id='pm-s1'"
).fetchone()
con.close()
print("  pm-s1 ->", row)
assert row[0] == "FILLED"
assert abs(row[1]) < 0.5
assert "partial FIFO match 3/5" in row[2]
print("  -> incomplete estimate not booked into realized_pnl OK")


# Finding 3 -------------------------------------------------------
print()
print("--- Finding 3: get_daily_pnl pads today ---")
reset()
result = rd_mod.get_daily_pnl()
print("  empty DB ->", result)
assert len(result) == 1 and result[0]["pnl"] == 0
assert result[0]["date"] == datetime.now().strftime("%Y-%m-%d")
print("  -> single today=0 entry OK")

# Buy-only day: no SELL rows.
seed([
    ("only-buy", "2026-04-27T09:00:00", "005930.KS", "BUY", 1, 1000, "FILLED",
     "", "daytrading", "", 1, 1000, "", 0),
])
result = rd_mod.get_daily_pnl()
print("  buy-only ->", result)
assert any(r["date"] == datetime.now().strftime("%Y-%m-%d") and r["pnl"] == 0 for r in result)
print("  -> today=0 still emitted with buy-only DB OK")

# Past SELL day, today no SELL: today should appear with 0 alongside historical bars.
seed([
    ("past-sell", "2026-04-22T09:00:00", "005930.KS", "SELL", 1, 1200, "FILLED",
     "", "daytrading", "", 1, 1200, "", 250),
])
result = rd_mod.get_daily_pnl()
print("  past sell + today no sell ->", result)
dates = [r["date"] for r in result]
assert "2026-04-22" in dates
assert datetime.now().strftime("%Y-%m-%d") in dates
print("  -> historical bars retained AND today=0 padded OK")

try:
    os.unlink(tmp)
except OSError:
    # Windows may hold the sqlite file briefly after the last connection closes.
    pass
print()
print("ALL THREE FINDINGS VERIFIED")
