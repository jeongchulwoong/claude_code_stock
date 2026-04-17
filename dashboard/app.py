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


# ── 진입점 ────────────────────────────────────

if __name__ == "__main__":
    seed_demo_data()   # DB가 비어있으면 데모 데이터 삽입
    print("\n" + "="*50)
    print("  🤖 AI 주식 모니터링 대시보드")
    print("  http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
