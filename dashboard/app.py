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
    seed_demo_data,
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
        import json
        return jsonify([{
            "ticker": r[0], "name": r[1], "price": r[2],
            "score": r[3], "reasons": json.loads(r[4] or '[]'),
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
        from core.screener import MarketScreener
        screener = MarketScreener()
        result   = screener.run(use_mock=True, min_score=20.0)
        return jsonify({"candidates": len(result.candidates), "scanned": result.total_scanned})
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

# ── 진입점 ────────────────────────────────────

if __name__ == "__main__":
    seed_demo_data()   # DB가 비어있으면 데모 데이터 삽입
    print("\n" + "="*50)
    print("  🤖 AI 주식 모니터링 대시보드")
    print("  http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
