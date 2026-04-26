"""
dashboard/app.py — Flask 모니터링 대시보드 서버

실행:
    python dashboard/app.py
    http://localhost:5000 에서 확인

외부 접근:
    http://YOUR_IP:5001/advanced
    비밀번호: config.py의 DASHBOARD_PASSWORD
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, render_template, request, session, redirect, url_for

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from dashboard.db_reader import (
    get_ai_judge_log,
    get_daily_pnl,
    get_orders,
    get_summary_stats,
    get_ticker_stats,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
# 세션 암호화 키 — 환경변수로 오버라이드 가능 (배포 환경)
import os as _os, secrets as _secrets
app.secret_key = _os.getenv("DASHBOARD_SECRET_KEY") or _secrets.token_hex(32)

# 비밀번호 설정 (config.py에서 가져오기) — admin/client 분리
try:
    from config import DASHBOARD_ADMIN_PASSWORD, DASHBOARD_CLIENT_PASSWORD
except ImportError:
    DASHBOARD_ADMIN_PASSWORD  = "admin123"
    DASHBOARD_CLIENT_PASSWORD = ""


# ── 인증 데코레이터 ─────────────────────────────

def login_required(f):
    """로그인된 사용자(admin 또는 client)면 접근 허용."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            # API 호출이면 401 JSON, 페이지면 로그인 리다이렉트
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "인증 필요"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """admin 만 접근 — client 는 403."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "인증 필요"}), 401
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "관리자 권한 필요"}), 403
            return render_template("login.html",
                                   error="이 페이지는 관리자 전용입니다"), 403
        return f(*args, **kwargs)
    return decorated_function


# ── 페이지 라우트 ─────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        # admin 비번 우선 매칭, 그 다음 client (client 비번이 설정돼 있을 때만)
        if password == DASHBOARD_ADMIN_PASSWORD:
            session['authenticated'] = True
            session['role']          = 'admin'
            return redirect(url_for('advanced_dashboard'))
        if DASHBOARD_CLIENT_PASSWORD and password == DASHBOARD_CLIENT_PASSWORD:
            session['authenticated'] = True
            session['role']          = 'client'
            return redirect(url_for('advanced_dashboard'))
        return render_template("login.html", error="비밀번호가 틀렸습니다")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('authenticated', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route("/")
@login_required
def index():
    return render_template("index.html",
                           is_admin=(session.get('role') == 'admin'),
                           role=session.get('role', 'client'))


# ── API 엔드포인트 ────────────────────────────

@app.route("/api/summary")
@admin_required
def api_summary():
    return jsonify(get_summary_stats())


@app.route("/api/orders")
@admin_required
def api_orders():
    return jsonify(get_orders(limit=100))


@app.route("/api/daily_pnl")
@admin_required
def api_daily_pnl():
    return jsonify(get_daily_pnl())


@app.route("/api/ticker_stats")
@admin_required
def api_ticker_stats():
    return jsonify(get_ticker_stats())


@app.route("/api/ai_log")
@admin_required
def api_ai_log():
    return jsonify(get_ai_judge_log())


@app.route("/api/health")
def api_health():
    # health 는 로그인 없이 — 모니터링용
    return jsonify({
        "status": "ok",
        "mode":   "LIVE",
        "time":   datetime.now().isoformat(),
    })

@app.route("/api/strategy_stats")
@admin_required
def api_strategy_stats():
    try:
        from core.strategy_tracker import StrategyTracker
        tracker = StrategyTracker()
        return jsonify(tracker.get_all_stats_dict())
    except Exception:
        return jsonify({})

@app.route("/api/screener")
@login_required
def api_screener():
    import sqlite3
    market = request.args.get("market", "all")  # all | domestic | foreign
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT ticker, name, price, score, reasons, screened_at "
                "FROM screener_results WHERE rowid IN ("
                "  SELECT MAX(rowid) FROM screener_results GROUP BY ticker"
                ") ORDER BY score DESC, screened_at DESC LIMIT 500"
            ).fetchall()
        def parse_reasons(raw):
            if not raw:
                return []
            try:
                return json.loads(raw)
            except Exception:
                return [s.strip() for s in raw.split(",") if s.strip()]
        results = []
        for r in rows:
            ticker = r[0]
            score = r[3] or 0
            # 통합 점수(Tech×0.4 + Fund + AI×0.35 + bonus) 70 이상만 노출
            if score < 70:
                continue
            is_domestic = ticker.endswith(".KS") or ticker.endswith(".KQ")
            if market == "domestic" and not is_domestic:
                continue
            if market == "foreign" and is_domestic:
                continue
            results.append({
                "ticker": ticker, "name": r[1], "price": r[2],
                "score": score, "reasons": parse_reasons(r[4]),
                "screened_at": r[5]
            })
        return jsonify(results)
    except Exception:
        return jsonify([])


@app.route("/advanced")
@login_required
def advanced_dashboard():
    return render_template(
        "advanced_dashboard.html",
        is_admin=(session.get('role') == 'admin'),
        role=session.get('role', 'client'),
    )


# ── 잔고 API ─────────────────────────────────────
_balance_cache = {"t": 0, "data": None}
_BAL_TTL_SEC = 30

@app.route("/api/stocks")
@login_required
def api_stocks():
    """전체 종목 카테고리 목록 — 차트 사이드바용."""
    from stock_universe import CATEGORIES, ALL
    cats = {k: [{"name": n, "ticker": ALL.get(n, n)} for n in v]
            for k, v in CATEGORIES.items()}
    return jsonify({"categories": cats, "total": len(ALL)})


@app.route("/api/ai_accuracy")
@admin_required
def api_ai_accuracy():
    """AI 신뢰도 vs 실제 결과 통계."""
    try:
        from core.ai_accuracy_tracker import AIAccuracyTracker
        t = AIAccuracyTracker()
        return jsonify({
            "overall":     t.overall_stats(),
            "by_confidence": t.stats_by_confidence_bucket(),
            "by_setup":    t.stats_by_setup(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/balance")
@admin_required
def api_balance():
    """키움 kt00004 잔고 조회 (30초 캐시) — 민감정보, admin 전용."""
    import time as _t
    now = _t.time()
    if _balance_cache["data"] and now - _balance_cache["t"] < _BAL_TTL_SEC:
        return jsonify(_balance_cache["data"])
    try:
        from core.kiwoom_api import KiwoomRestAPI
        kw = KiwoomRestAPI()
        if not kw.login():
            return jsonify({"ok": False, "error": "키움 로그인 실패"}), 500
        bal = kw.get_balance()
        out = (bal.get("output2", [{}]) or [{}])[0]
        data = {
            "ok":           True,
            "buying_power": out.get("buying_power", 0),
            "entr":         out.get("entr", 0),
            "d2_entra":     out.get("d2_entra", 0),
            "tot_evlu_amt": out.get("tot_evlu_amt", 0),
            "tot_pur_amt":  out.get("tot_pur_amt", 0),
            "tot_evlt_pl":  out.get("tot_evlt_pl", 0),
        }
        _balance_cache["t"] = now
        _balance_cache["data"] = data
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 차트 API ─────────────────────────────────────
_chart_cache: dict = {}     # {(ticker, type): (timestamp, payload)}
_CHART_TTL_SEC = 60         # 동일 종목 1분 캐시

@app.route("/api/chart")
@login_required
def api_chart():
    """
    종목 차트 데이터 — 키움 ka10081(일봉) / ka10080(분봉) → 실패 시 yfinance 폴백.
    Query: ticker (예: 005930.KS, AAPL), type=daily|min, scope=1|3|5|...

    응답: {ok, ticker, type, candles:[{t,o,h,l,c,v}, ...]}
    """
    import time as _t
    from stock_universe import resolve as _resolve

    raw_ticker = request.args.get("ticker", "").strip()
    chart_type = request.args.get("type", "daily").lower()
    scope      = request.args.get("scope", "5")
    if not raw_ticker:
        return jsonify({"ok": False, "error": "ticker 파라미터 필요"}), 400

    ticker, _ = _resolve(raw_ticker)
    cache_key = (ticker, chart_type, scope)
    now = _t.time()
    cached = _chart_cache.get(cache_key)
    if cached and now - cached[0] < _CHART_TTL_SEC:
        return jsonify(cached[1])

    candles: list = []
    is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
    source = ""

    # 1) 키움 (국내만)
    if is_kr:
        try:
            from core.kiwoom_api import KiwoomRestAPI
            kw = KiwoomRestAPI()
            if kw.login():
                if chart_type == "min":
                    res = kw.get_minute_chart(ticker, count=120, tic_scope=scope)
                else:
                    res = kw.get_daily_chart(ticker, count=120)
                df = res.get("df")
                if df is not None and not df.empty:
                    tcol = "time" if chart_type == "min" else "date"
                    for _, row in df.iterrows():
                        candles.append({
                            "t": str(row[tcol]),
                            "o": float(row["open"]), "h": float(row["high"]),
                            "l": float(row["low"]),  "c": float(row["close"]),
                            "v": int(row["volume"]),
                        })
                    source = "kiwoom"
        except Exception as e:
            print(f"[chart] kiwoom 실패: {e}", flush=True)

    # 2) yfinance 폴백 (해외 또는 키움 실패)
    if not candles:
        try:
            import math
            import yfinance as yf
            interval = "1d" if chart_type == "daily" else f"{scope}m"
            period   = "6mo" if chart_type == "daily" else "5d"
            raw = yf.download(ticker, period=period, interval=interval,
                              progress=False, auto_adjust=True)
            if raw is not None and not raw.empty:
                if hasattr(raw.columns, "levels"):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [c.lower() for c in raw.columns]
                raw = raw.dropna(subset=["close"]).reset_index()
                tcol = raw.columns[0]
                _safe = lambda v, d=0.0: float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else d
                _safei = lambda v: int(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else 0
                for _, row in raw.iterrows():
                    ts = row[tcol]
                    candles.append({
                        "t": str(ts).split("+")[0],
                        "o": _safe(row.get("open")),  "h": _safe(row.get("high")),
                        "l": _safe(row.get("low")),   "c": _safe(row.get("close")),
                        "v": _safei(row.get("volume")),
                    })
                source = "yfinance"
        except Exception as e:
            print(f"[chart] yfinance 실패: {e}", flush=True)

    payload = {
        "ok":     bool(candles),
        "ticker": ticker,
        "type":   chart_type,
        "scope":  scope,
        "source": source,
        "candles": candles,
    }
    if candles:
        _chart_cache[cache_key] = (now, payload)
    return jsonify(payload)

@app.route("/api/foreign_signals")
@login_required
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
@admin_required
def api_run_screener():
    """레거시: fetch_real_stocks.py 로 빠른 단순 점수 (Tech only)."""
    try:
        import subprocess, sys
        script = str(Path(__file__).parent.parent / "scripts" / "fetch_real_stocks.py")
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=300
        )
        with sqlite3.connect(DB_PATH) as con:
            cnt = con.execute("SELECT COUNT(*) FROM screener_results").fetchone()[0]
        return jsonify({"scanned": cnt, "ok": proc.returncode == 0,
                        "stderr": proc.stderr[-500:] if proc.returncode != 0 else ""})
    except Exception as e:
        return jsonify({"error": str(e)})


_composite_lock = __import__("threading").Lock()
_composite_state = {"running": False, "started_at": None, "elapsed": 0, "saved": 0}

@app.route("/api/run_composite_screener")
@admin_required
def api_run_composite_screener():
    """
    통합 스크리너 (Tech + Fund + AI) 실시간 실행.
    Query: market=domestic|foreign|all (default: all)
    """
    import time as _t
    if not _composite_lock.acquire(blocking=False):
        return jsonify({
            "ok": False, "error": "이미 실행 중입니다",
            "started_at": _composite_state.get("started_at"),
        }), 409
    try:
        _composite_state.update({"running": True, "started_at": datetime.now().isoformat()})
        market = request.args.get("market", "all")

        # 의존성 import
        from core.kiwoom_api import get_kiwoom_api
        from core.data_collector import DataCollector
        from core.fundamental_gate import FundamentalGate
        from core.integrated_judge import IntegratedJudge
        from core.screener import MarketScreener
        from config import WATCH_LIST, get_foreign_watch_names

        # 가벼운 조립 — 매번 새로 (요청-스코프드)
        kw  = get_kiwoom_api()
        try: kw.login()
        except Exception: pass

        dc        = DataCollector(kw)
        fund_gate = FundamentalGate()
        int_judge = IntegratedJudge()
        screener  = MarketScreener(dc, fundamental_gate=fund_gate, integrated_judge=int_judge)

        if market == "domestic":
            universe = list(WATCH_LIST)
        elif market == "foreign":
            universe = list(get_foreign_watch_names())
        else:
            universe = list(WATCH_LIST) + list(get_foreign_watch_names())

        t0 = _t.monotonic()
        # AI top_n 50: tech>=70 사전필터 + 펀더멘탈 통과만 호출 (실제 호출은 보통 5~15회)
        # → 강한 후보는 모두 AI 분석, 약한 종목엔 자동으로 호출 안 함
        result = screener.run(
            universe=universe, use_mock=False,
            min_score=60.0, ai_top_n=50, composite_min=70.0,
        )
        elapsed = round(_t.monotonic() - t0, 1)
        _composite_state["elapsed"] = elapsed
        _composite_state["saved"]   = len(result.candidates)

        return jsonify({
            "ok": True,
            "elapsed_sec": elapsed,
            "scanned":     len(universe),
            "passed":      len(result.candidates),
            "tickers":     [c.ticker for c in result.candidates[:20]],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _composite_state.update({"running": False})
        _composite_lock.release()

@app.route("/api/attribution")
@admin_required
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
@admin_required
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

@app.route("/api/foreign_watchlist", methods=["GET"])
@login_required
def api_foreign_watchlist_get():
    import config as cfg
    from stock_universe import FOREIGN
    return jsonify({
        "watch_names": cfg.get_foreign_watch_names(),
        "all_stocks":  list(FOREIGN.keys()),
        "ticker_map":  dict(FOREIGN),    # {name: ticker} — 자동완성 + ticker 변환용
    })

@app.route("/api/foreign_watchlist", methods=["POST"])
@admin_required
def api_foreign_watchlist_post():
    """
    foreign_watch_names 저장.
    각 입력값을 name 또는 ticker 로 받아서 canonical name 으로 정규화.
    """
    import config as cfg
    from stock_universe import FOREIGN
    data = request.get_json(force=True)
    current = cfg._load_user_config()
    if "foreign_watch_names" in data:
        # 역참조 (ticker → name)
        ticker_to_name = {v: k for k, v in FOREIGN.items()}
        normalized = []
        for entry in data["foreign_watch_names"]:
            if entry in FOREIGN:
                normalized.append(entry)                      # 정확히 이름
            elif entry in ticker_to_name:
                normalized.append(ticker_to_name[entry])      # ticker 입력
            elif entry.upper() in ticker_to_name:
                normalized.append(ticker_to_name[entry.upper()])
            # 둘 다 아니면 무시
        # 중복 제거 (순서 유지)
        seen = set(); valid = []
        for n in normalized:
            if n not in seen:
                seen.add(n); valid.append(n)
        current["foreign_watch_names"] = valid
    cfg._save_user_config(current)
    return jsonify({"ok": True, "saved_count": len(current.get("foreign_watch_names", []))})

_foreign_ai_lock = __import__("threading").Lock()
_foreign_ai_state = {"running": False, "started_at": None}

@app.route("/api/run_foreign_ai")
@admin_required
def api_run_foreign_ai():
    """
    해외주식 AI 분석 스크립트 실행 — 블로킹.
    동시 실행 차단(이미 돌면 409), 5분 타임아웃, 종료 후 신호 개수 반환.
    """
    import subprocess, sys, time
    if not _foreign_ai_lock.acquire(blocking=False):
        return jsonify({
            "ok": False,
            "error": "이미 실행 중입니다",
            "started_at": _foreign_ai_state.get("started_at"),
        }), 409
    try:
        _foreign_ai_state.update({"running": True, "started_at": datetime.now().isoformat()})
        script = str(Path(__file__).parent.parent / "scripts" / "generate_foreign_signals_ai.py")
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=300,  # 5분
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "분석 타임아웃 (5분 초과)"}), 504

        elapsed = round(time.monotonic() - t0, 1)
        if proc.returncode != 0:
            return jsonify({
                "ok": False,
                "error": f"스크립트 실패 (exit={proc.returncode})",
                "stderr": (proc.stderr or "")[-1500:],
                "elapsed_sec": elapsed,
            }), 500

        # 새로 저장된 신호 개수 확인
        try:
            with sqlite3.connect(str(DB_PATH)) as con:
                count = con.execute("SELECT COUNT(*) FROM foreign_signals").fetchone()[0]
        except Exception:
            count = None
        return jsonify({"ok": True, "elapsed_sec": elapsed, "signal_count": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _foreign_ai_state.update({"running": False})
        _foreign_ai_lock.release()

# ── Config API ────────────────────────────────

@app.route("/api/config", methods=["GET"])
@admin_required
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
@admin_required
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

def _print_access_urls(port: int = 5000) -> None:
    """접속 URL 출력 (로컬 + LAN). 외부 접속은 start_tunnel.bat 으로 Cloudflare Tunnel 실행."""
    import socket as _sock
    print("\n" + "="*60)
    print("  ⚡ Quant Desk — 대시보드 시작")
    print("="*60)
    print(f"  🏠 로컬:  http://localhost:{port}/advanced")

    # LAN IP (같은 와이파이)
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        print(f"  📶 LAN:   http://{lan_ip}:{port}/advanced")
    except Exception:
        pass

    print("  🌐 외부:  start_tunnel.bat 실행 → Cloudflare Tunnel URL 발급")

    # 비밀번호 보안 경고 (admin/client 분리)
    try:
        from config import DASHBOARD_ADMIN_PASSWORD, DASHBOARD_CLIENT_PASSWORD
        if DASHBOARD_ADMIN_PASSWORD in ("admin123", "wjd..dk33?"):
            print()
            print("  ⚠️  DASHBOARD_ADMIN_PASSWORD 가 기본값입니다!")
            print("      외부 접속 전 .env 에 강력한 비번 설정:")
            print("      DASHBOARD_ADMIN_PASSWORD=긴_랜덤_문자열")
        if not DASHBOARD_CLIENT_PASSWORD:
            print("  ℹ️  DASHBOARD_CLIENT_PASSWORD 미설정 — 친구 공유 비활성")
    except Exception:
        pass

    print("="*60 + "\n")


if __name__ == "__main__":
    _print_access_urls(5000)
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
