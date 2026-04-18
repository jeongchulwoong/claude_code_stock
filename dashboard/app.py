"""
dashboard/app.py — Flask 모니터링 대시보드 서버

실행:
    python dashboard/app.py
    http://localhost:5000 에서 확인
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.db_reader import (
    get_ai_judge_log,
    get_daily_pnl,
    get_orders,
    get_summary_stats,
    get_ticker_stats,
)

app = Flask(__name__, template_folder="templates", static_folder="static")


# ── 페이지 라우트 ─────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API 엔드포인트 ────────────────────────────

@app.route("/api/summary")
def api_summary():
    return jsonify(get_summary_stats())


@app.route("/api/orders")
def api_orders():
    return jsonify(get_orders(limit=100))


@app.route("/api/daily_pnl")
def api_daily_pnl():
    return jsonify(get_daily_pnl())


@app.route("/api/ticker_stats")
def api_ticker_stats():
    return jsonify(get_ticker_stats())


@app.route("/api/ai_log")
def api_ai_log():
    return jsonify(get_ai_judge_log())


@app.route("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "mode":   "PAPER",
        "time":   datetime.now().isoformat(),
    })

@app.route("/api/strategy_stats")
def api_strategy_stats():
    try:
        from core.strategy_tracker import StrategyTracker
        tracker = StrategyTracker()
        return jsonify(tracker.get_all_stats_dict())
    except Exception:
        return jsonify({})

@app.route("/api/screener")
def api_screener():
    import sqlite3
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT ticker, name, price, score, reasons, screened_at "
                "FROM screener_results ORDER BY screened_at DESC, score DESC LIMIT 20"
            ).fetchall()
        def parse_reasons(raw):
            if not raw:
                return []
            try:
                return json.loads(raw)
            except Exception:
                return [s.strip() for s in raw.split(",") if s.strip()]
        return jsonify([{
            "ticker": r[0], "name": r[1], "price": r[2],
            "score": r[3], "reasons": parse_reasons(r[4]),
            "screened_at": r[5]
        } for r in rows])
    except Exception:
        return jsonify([])


@app.route("/advanced")
def advanced():
    return render_template("advanced_dashboard.html")

@app.route("/api/foreign_signals")
def api_foreign_signals():
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT ticker, action, confidence, reason, current_price, change_pct, news_sentiment, generated_at "
                "FROM foreign_signals ORDER BY generated_at DESC LIMIT 20"
            ).fetchall()
        return jsonify([{
            "ticker": r[0], "action": r[1], "confidence": r[2],
            "reason": r[3], "current_price": r[4], "change_pct": r[5],
            "news_sentiment": r[6], "generated_at": r[7]
        } for r in rows])
    except Exception:
        return jsonify([])

@app.route("/api/run_screener")
def api_run_screener():
    try:
        import subprocess, sys
        script = str(Path(__file__).parent.parent / "scripts" / "fetch_real_stocks.py")
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=300
        )
        # count saved rows
        with sqlite3.connect(DB_PATH) as con:
            cnt = con.execute("SELECT COUNT(*) FROM screener_results").fetchone()[0]
        return jsonify({"scanned": cnt, "ok": proc.returncode == 0,
                        "stderr": proc.stderr[-500:] if proc.returncode != 0 else ""})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/attribution")
def api_attribution():
    try:
        from core.performance_attribution import PerformanceAttributor
        pa = PerformanceAttributor()
        r  = pa.analyze()
        return jsonify({
            "total_pnl": r.total_pnl,
            "by_strategy": r.by_strategy,
            "by_ticker": r.by_ticker,
            "by_sector": r.by_sector,
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/alerts")
def api_alerts():
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT rule_id, ticker, name, alert_type, threshold, active "
                "FROM alert_rules ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return jsonify([{
            "rule_id": r[0], "ticker": r[1], "name": r[2],
            "alert_type": r[3], "threshold": r[4], "active": bool(r[5])
        } for r in rows])
    except Exception:
        return jsonify([])

from config import DB_PATH as DB_PATH
import sqlite3

# ── Config API ────────────────────────────────
from flask import request

@app.route("/api/config", methods=["GET"])
def api_config_get():
    import config as cfg
    from stock_universe import ALL
    return jsonify({
        "watch_names":   cfg.get_watch_names(),
        "risk_config":   cfg.get_risk_config(),
        "scan_interval": cfg.get_scan_interval(),
        "all_stocks":    list(ALL.keys()),
    })

@app.route("/api/config", methods=["POST"])
def api_config_post():
    import config as cfg
    data = request.get_json(force=True)
    current = cfg._load_user_config()

    if "watch_names" in data:
        from stock_universe import ALL
        valid = [n for n in data["watch_names"] if n in ALL]
        current["watch_names"] = valid

    if "risk_config" in data:
        allowed = {"max_positions","max_invest_per_trade","stop_loss_pct",
                   "take_profit_pct","daily_loss_limit","min_confidence"}
        patch = {k: v for k, v in data["risk_config"].items() if k in allowed}
        current.setdefault("risk_config", {}).update(patch)

    if "scan_interval_minutes" in data:
        v = int(data["scan_interval_minutes"])
        current["scan_interval_minutes"] = max(5, min(v, 1440))

    cfg._save_user_config(current)
    return jsonify({"ok": True, "saved": current})

# ── 진입점 ────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  🤖 AI 주식 모니터링 대시보드")
    print("  http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
