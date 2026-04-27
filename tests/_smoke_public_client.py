"""Public-client + admin separation regression tests.

Run from repo root:
    PYTHONIOENCODING=utf-8 python tests/_smoke_public_client.py

Verifies:
  1. /client serves without auth (200), and the rendered HTML never references
     admin URLs (/api/balance, /api/portfolio, /api/orders, /api/config).
  2. All /api/public/* endpoints reachable without auth (200) AND a recursive
     scan finds no forbidden sensitive keys (incl. reason / broker_ord_no /
     reject_msg / buying_power / entr / d2_entra / account / raw / config / token).
  3. Anonymous and client-role sessions are blocked from admin APIs
     (/api/portfolio, /api/balance, /api/orders, /api/summary -> 401 / 403).
  4. With a fake Kiwoom snapshot, the public summary math holds:
       total_value     == sum(holdings.eval_amt)
       unrealized_pnl  == sum(holdings.pnl)
       realized_pnl    matches DB summary
       total_pnl       == unrealized_pnl + realized_pnl
  5. Buy-only day: /api/public/performance still emits today=0 row.
  6. /api/public/recent-fills filters out non-fills:
       ERROR / BLOCKED / SENT / UNFILLED / CANCEL_FAILED / MEMORY_CLEARED
     and never exposes broker_ord_no / reject_msg / reason at the row level.
  7. When the upstream Kiwoom call is unavailable the public endpoints still
     return ok=false in a generic shape with no internal error string leaked.
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


# ── Sensitive-field allowlist guard (matches dashboard.app._PUBLIC_FORBIDDEN_KEYS) ─
FORBIDDEN_KEYS = (
    "buying_power", "entr", "d2_entra",
    "account", "broker_ord_no", "reject_msg",
    "raw", "config", "token", "reason",
)


def _scan(obj, path="$"):
    """Walk a JSON-shaped value and return any (path, key) hit on FORBIDDEN_KEYS."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                hits.append((f"{path}.{k}", k))
            hits.extend(_scan(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_scan(v, f"{path}[{i}]"))
    return hits


# ── Bootstrap with a private temp DB so we can seed orders ──────
_tmp_db = tempfile.mkstemp(suffix=".db")[1]

# Pre-import dashboard.db_reader and core.order_manager and rebind DB_PATH
# BEFORE importing dashboard.app so Flask uses our test DB throughout.
import dashboard.db_reader as rd_mod  # noqa: E402
import core.order_manager as om_mod   # noqa: E402
rd_mod.DB_PATH = _tmp_db
om_mod.DB_PATH = _tmp_db


class _StubKW:
    last_ord_no = ""
    last_reject_msg = ""
    def get_holdings(self): return []
    def get_open_orders(self): return []


class _StubRM:
    def increment_position(self, *a, **k): pass
    def partial_close(self, *a, **k): return 0.0
    def add_position(self, *a, **k): pass
    def get_positions(self): return {}
    def get_positions_by_style(self, *a): return {}


om_mod.OrderManager(_StubKW(), _StubRM())  # creates schema in _tmp_db

# Seed: a representative cross-section of statuses.
# - 2 BUY (FILLED) + 1 SELL (FILLED, realized +1000)  → must appear in fills
# - ERROR, BLOCKED, SENT, UNFILLED, CANCEL_FAILED, MEMORY_CLEARED → must NOT appear
INSERT = (
    "INSERT INTO orders (order_id,timestamp,ticker,order_type,qty,price,status,reason,strategy,"
    " broker_ord_no,filled_qty,avg_fill_price,reject_msg,realized_pnl) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
TODAY = datetime.now().strftime("%Y-%m-%d")
con = sqlite3.connect(_tmp_db)
con.executemany(INSERT, [
    # FILLED rows that should be exposed via /api/public/recent-fills
    ("seed-b1", "2026-04-25T09:00:00", "005930.KS", "BUY",  5, 1000, "FILLED",         "tech setup",       "daytrading", "OPEN-1", 5, 1000, "",                 0),
    ("seed-b2", "2026-04-26T09:00:00", "005930.KS", "BUY",  5, 1100, "FILLED",         "tech setup",       "daytrading", "OPEN-2", 5, 1100, "",                 0),
    ("seed-s1", "2026-04-27T09:00:00", "005930.KS", "SELL", 5, 1200, "FILLED",         "take profit",      "daytrading", "OPEN-3", 5, 1200, "",                 1000),
    # Status rows that MUST be filtered out from public fills
    ("seed-er", "2026-04-27T09:05:00", "068270.KS", "BUY",  2,  208000, "ERROR",       "ret=-1",           "longterm",   "",       0, 0,    "매수증거금 부족",  0),
    ("seed-bl", "2026-04-27T09:06:00", "051910.KS", "BUY",  0,  385000, "BLOCKED",     "risk block",       "longterm",   "",       0, 0,    "available_cash<=0", 0),
    ("seed-sn", "2026-04-27T09:07:00", "105560.KS", "BUY",  1,  155900, "SENT",        "buy",              "longterm",   "OPEN-4", 0, 0,    "",                 0),
    ("seed-un", "2026-04-27T09:08:00", "010140.KS", "BUY",  1,    7000, "UNFILLED",    "timeout",          "daytrading", "OPEN-5", 0, 0,    "",                 0),
    ("seed-cf", "2026-04-27T09:09:00", "012450.KS", "BUY",  1,   80000, "CANCEL_FAILED", "broker rejected", "daytrading", "OPEN-6", 0, 0,    "broker err",       0),
    ("seed-mc", "2026-04-27T09:10:00", "066570.KS", "BUY",  1,   90000, "MEMORY_CLEARED", "shutdown",       "daytrading", "",       0, 0,    "",                 0),
])
con.commit()
con.close()


# ── Now bring up the Flask app with stubs for Kiwoom ────────────
os.environ.setdefault("DASHBOARD_ADMIN_PASSWORD",  "admin-pw-test")
os.environ.setdefault("DASHBOARD_CLIENT_PASSWORD", "client-pw-test")
import importlib  # noqa: E402
import config     # noqa: E402
importlib.reload(config)
import dashboard.app as dapp  # noqa: E402
importlib.reload(dapp)
dapp.app.config["TESTING"] = True
# Force the admin/portfolio snapshot helpers to use the test DB too.
dapp.rd_mod = rd_mod  # type: ignore[attr-defined]


class FakeKW:
    """Mimics KiwoomRestAPI; supplies one held position aligned to the seeded BUYs."""
    def login(self): return True
    def get_balance(self):
        return {"output2": [{
            "tot_evlu_amt": 12500, "tot_pur_amt": 11000, "tot_evlt_pl": 1500,
            "buying_power": 50000, "entr": 50000, "d2_entra": 50000,
        }]}
    def get_holdings(self):
        return [{
            "ticker": "005930.KS", "name": "삼성전자",
            "qty": 5, "avg_price": 1100, "cur_price": 1300,
            "eval_amt": 6500, "pnl": 1000, "pnl_rate": 18.18,
        }, {
            "ticker": "105560.KS", "name": "KB금융",
            "qty": 1, "avg_price": 155900, "cur_price": 157000,
            "eval_amt": 157000, "pnl": 1100, "pnl_rate": 0.71,
        }]


def _bind_kiwoom(impl):
    """Force the dashboard to use a specific Kiwoom-shaped stub. Resets cache too."""
    dapp._kiwoom_singleton["kw"] = impl
    dapp._portfolio_cache.update({"t": 0, "data": None})


_bind_kiwoom(FakeKW())


# ── 1) /client unauth + HTML scan for forbidden URLs ────────────
print("--- /client unauth + HTML forbidden-URL scan ---")
FORBIDDEN_URLS_IN_CLIENT_HTML = (
    "/api/balance", "/api/portfolio", "/api/orders",
    "/api/config", "/api/summary", "/api/strategy_stats",
    "/api/run_screener", "/api/run_composite_screener",
    "/api/ai_accuracy", "/api/attribution", "/api/alerts",
    "/api/foreign_watchlist", "/api/run_foreign_ai",
)
with dapp.app.test_client() as c:
    r = c.get("/client")
    print(f"  GET /client -> {r.status_code}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    for forbidden_url in FORBIDDEN_URLS_IN_CLIENT_HTML:
        assert forbidden_url not in body, f"client HTML leaks admin URL: {forbidden_url}"
    print(f"  client HTML contains none of {len(FORBIDDEN_URLS_IN_CLIENT_HTML)} admin URLs OK")


# ── 2) /api/public/* unauth + recursive sensitive-field scan ────
print()
print("--- /api/public/* unauth access + recursive sensitive scan ---")
PUBLIC_ENDPOINTS = (
    "/api/public/summary",
    "/api/public/holdings",
    "/api/public/sectors",
    "/api/public/performance",
    "/api/public/recent-fills",
)
public_payloads: dict = {}
with dapp.app.test_client() as c:
    for ep in PUBLIC_ENDPOINTS:
        r = c.get(ep)
        print(f"  GET {ep} -> {r.status_code}")
        assert r.status_code == 200, f"{ep} not 200"
        public_payloads[ep] = r.get_json()

for ep, payload in public_payloads.items():
    hits = _scan(payload)
    assert not hits, f"{ep} leaked sensitive keys: {hits}"
    print(f"  scan {ep}: clean")
print(f"  none of {len(FORBIDDEN_KEYS)} forbidden keys present in any payload OK")


# ── 3) Admin endpoints reject anon + client roles ──────────────
print()
print("--- admin endpoints gated against anon + client ---")
ADMIN_ENDPOINTS = (
    "/api/portfolio", "/api/balance", "/api/orders", "/api/summary",
    "/api/run_screener", "/api/run_composite_screener",
    "/api/strategy_stats", "/api/ai_accuracy", "/api/attribution", "/api/alerts",
    "/api/daily_pnl", "/api/ticker_stats", "/api/ai_log",
)
with dapp.app.test_client() as c:
    for ep in ADMIN_ENDPOINTS:
        r = c.get(ep)
        print(f"  anon   {ep} -> {r.status_code}")
        assert r.status_code == 401, f"{ep} should 401 for anon"
with dapp.app.test_client() as c:
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "client"
    for ep in ADMIN_ENDPOINTS:
        r = c.get(ep)
        print(f"  client {ep} -> {r.status_code}")
        assert r.status_code == 403, f"{ep} should 403 for client"
# admin role still works
with dapp.app.test_client() as c:
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "admin"
    r = c.get("/api/portfolio")
    print(f"  admin  /api/portfolio -> {r.status_code}")
    assert r.status_code == 200
print("  admin APIs blocked for anon+client; admin retains access OK")


# ── 4) Public summary math ─────────────────────────────────────
print()
print("--- public summary math (total_pnl = unrealized + realized) ---")
summary = public_payloads["/api/public/summary"]
holdings = public_payloads["/api/public/holdings"]["holdings"]

expected_total_value    = sum(h["eval_amt"] for h in holdings)
expected_unrealized_pnl = sum(h["pnl"] for h in holdings)
expected_realized       = 1000           # SELL row's realized_pnl in seed
expected_total          = expected_unrealized_pnl + expected_realized

print(f"  expected: total={expected_total_value} unreal={expected_unrealized_pnl} "
      f"real={expected_realized} total_pnl={expected_total}")
print(f"  actual  : total={summary['total_value']} unreal={summary['unrealized_pnl']} "
      f"real={summary['realized_pnl']} total_pnl={summary['total_pnl']}")
assert summary["total_value"]    == expected_total_value
assert summary["unrealized_pnl"] == expected_unrealized_pnl
assert summary["realized_pnl"]   == expected_realized
assert summary["total_pnl"]      == expected_total
print("  totals match holdings sum + DB realized OK")


# ── 5) Performance pads today even on a buy-only day ───────────
print()
print("--- performance pads today ---")
perf = public_payloads["/api/public/performance"]["performance"]
print(f"  performance rows: {perf}")
dates = [r["date"] for r in perf]
assert TODAY in dates, f"performance must include today ({TODAY}); got dates={dates}"
print(f"  today ({TODAY}) is present OK")


# ── 6) recent-fills excludes ALL non-fill statuses & strips sensitive fields ──
print()
print("--- recent-fills filters out non-fills ---")
fills = public_payloads["/api/public/recent-fills"]["fills"]
print(f"  exposed: {[(f['ticker'], f['status_label']) for f in fills]}")
tickers_seen = {f.get("ticker") for f in fills}

# Every row exposed must be a fill / partial fill
assert all(f.get("status_label") in ("체결", "부분체결") for f in fills), "non-fill leaked"

# Each non-fill seed must have been suppressed
NONFILL_TICKERS = {
    "068270.KS": "ERROR",
    "051910.KS": "BLOCKED",
    "105560.KS": "SENT",
    "010140.KS": "UNFILLED",
    "012450.KS": "CANCEL_FAILED",
    "066570.KS": "MEMORY_CLEARED",
}
for tk, status in NONFILL_TICKERS.items():
    assert tk not in tickers_seen, f"{status} row leaked into public fills: {tk}"

# Row-level field guard
ROW_FORBIDDEN = ("broker_ord_no", "reject_msg", "reason", "strategy", "raw")
for f in fills:
    for bad in ROW_FORBIDDEN:
        assert bad not in f, f"recent-fills row leaked '{bad}'"
print("  6 non-fill statuses (ERROR/BLOCKED/SENT/UNFILLED/CANCEL_FAILED/MEMORY_CLEARED) all filtered OK")
print("  row-level sensitive keys absent OK")


# ── 7) Public endpoints fail safely when Kiwoom is down ─────────
print()
print("--- public endpoints stay sanitized on upstream failure ---")

class _BrokenKW:
    """Simulates a broker that fails on every call. Public APIs must still return
    a generic ok=false shape with no internal error string and no sensitive keys."""
    def login(self): return True
    def get_balance(self):  raise RuntimeError("internal: token expired @ proxy 10.0.0.5 secret=ABCD")
    def get_holdings(self): raise RuntimeError("internal: account 5581-5936 lookup failed")


_bind_kiwoom(_BrokenKW())
with dapp.app.test_client() as c:
    for ep in PUBLIC_ENDPOINTS:
        r = c.get(ep)
        body = r.get_json() or {}
        print(f"  {ep} -> http={r.status_code} ok={body.get('ok')} keys={sorted(body.keys())}")
        # Recursive forbidden-key scan still applies
        hits = _scan(body)
        assert not hits, f"{ep} leaked sensitive keys on failure: {hits}"
        # The internal RuntimeError text must NOT appear anywhere in the response
        text = r.get_data(as_text=True)
        for leak in ("token expired", "10.0.0.5", "ABCD", "5581-5936", "RuntimeError"):
            assert leak not in text, f"{ep} leaked internal error fragment: {leak}"
print("  failure responses sanitized — no internal error strings, no sensitive keys")


# ── 8) DB-backed public endpoints also use the generic failure shape ──
print()
print("--- db-backed public endpoints use generic failure shape ---")
_orig_daily_pnl = dapp.get_daily_pnl
_orig_get_orders = dapp.get_orders

def _raise_db_failure(*_a, **_k):
    raise RuntimeError("sqlite internal failure account=5581-5936 token=SECRET")

try:
    dapp.get_daily_pnl = _raise_db_failure
    dapp.get_orders = _raise_db_failure
    with dapp.app.test_client() as c:
        for ep in ("/api/public/performance", "/api/public/recent-fills"):
            r = c.get(ep)
            body = r.get_json() or {}
            print(f"  {ep} -> http={r.status_code} body={body}")
            assert r.status_code == 200
            assert body == {
                "ok": False,
                "updated_at": body.get("updated_at"),
                "error": "데이터 일시 조회 불가",
            }, f"{ep} must return generic public failure shape"
            assert sorted(body.keys()) == ["error", "ok", "updated_at"]
            assert not _scan(body), f"{ep} leaked forbidden keys on DB failure"
            text = r.get_data(as_text=True)
            for leak in ("sqlite internal", "5581-5936", "SECRET", "RuntimeError"):
                assert leak not in text, f"{ep} leaked DB error fragment: {leak}"
finally:
    dapp.get_daily_pnl = _orig_daily_pnl
    dapp.get_orders = _orig_get_orders
print("  DB exceptions sanitized into the same public failure shape OK")


# ── Cleanup ────────────────────────────────────────────────────
try:
    os.unlink(_tmp_db)
except OSError:
    pass

print()
print("ALL PUBLIC-CLIENT REGRESSION CHECKS PASSED")
