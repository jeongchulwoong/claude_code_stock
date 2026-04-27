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


@app.route("/client")
def client_dashboard():
    """비밀번호 없이 접근 가능한 공개 투자자 리포트 페이지.

    데이터는 모두 /api/public/* 만 사용한다. 어드민 API 호출은 페이지 안에 존재하지 않는다.
    """
    return render_template("client_dashboard.html")


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


# ── 포트폴리오 / 잔고 공통 인프라 ─────────────────
# /api/balance, /api/portfolio, /api/public/* 가 모두 같은 snapshot 을 사용해
# 화면에 표시되는 평가금액·평가손익이 어디서나 일치하도록 한다.

_kiwoom_singleton = {"kw": None}

def _get_kiwoom():
    """프로세스 단일 KiwoomRestAPI 핸들. 재로그인 시 토큰 자동 갱신은 _ensure_token 에서 수행."""
    if _kiwoom_singleton["kw"] is None:
        from core.kiwoom_api import KiwoomRestAPI
        kw = KiwoomRestAPI()
        if not kw.login():
            return None
        _kiwoom_singleton["kw"] = kw
    return _kiwoom_singleton["kw"]


# 섹터 매핑 — 보유 종목 평가금액 기준 비중 계산용.
SECTOR_MAP = {
    "005930": "반도체", "000660": "반도체",
    "035420": "IT",     "035720": "IT",     "036570": "IT",
    "051910": "화학",   "006400": "화학",   "096770": "화학",
    "005380": "자동차", "000270": "자동차",
    "068270": "바이오", "207940": "바이오", "302440": "바이오",
    "105560": "금융",   "055550": "금융",   "086790": "금융",
    "017670": "통신",   "030200": "통신",
    "015760": "유틸",   "034730": "유틸",
    "066570": "전자",   "009150": "전자",
}

_portfolio_cache = {"t": 0, "data": None}
_PORTFOLIO_TTL_SEC = 30

# Public API 응답에서 절대 노출되어선 안 되는 키. _strip_public 가 재귀로 검증한다.
# - reason 은 AI 판단/주문 내부 근거 전체 원문이라 공개 절대 금지.
_PUBLIC_FORBIDDEN_KEYS = frozenset({
    "buying_power", "entr", "d2_entra",
    "account", "broker_ord_no", "reject_msg",
    "raw", "config", "token", "reason",
})


def _get_portfolio_snapshot(force_refresh: bool = False) -> dict:
    """공통 포트폴리오 snapshot — admin 전용 (민감 필드 포함). 30초 캐시.

    /api/balance, /api/portfolio, /api/public/* 모두 이 함수를 베이스로 사용한다.
    public API 는 _public_snapshot() 로 한 번 더 거른 뒤 노출한다.

    반환: {
      ok, updated_at, error?,
      buying_power, entr, d2_entra,                       # 민감 (admin only)
      tot_evlu_amt, tot_pur_amt, tot_evlt_pl, tot_evlt_pl_rate,
      realized_pnl, today_realized_pnl,
      holdings_count, holdings: [...], sectors: [...],
    }
    """
    import time as _t
    now = _t.time()
    if (not force_refresh) and _portfolio_cache["data"] \
            and now - _portfolio_cache["t"] < _PORTFOLIO_TTL_SEC:
        return _portfolio_cache["data"]

    snap: dict = {
        "ok":               False,
        "updated_at":       datetime.now().isoformat(timespec="seconds"),
        "buying_power":     0, "entr": 0, "d2_entra": 0,
        "tot_evlu_amt":     0, "tot_pur_amt": 0,
        "tot_evlt_pl":      0, "tot_evlt_pl_rate": 0.0,
        "realized_pnl":     0, "today_realized_pnl": 0,
        "holdings_count":   0, "holdings": [], "sectors": [],
        "error":            None,
    }

    kw = _get_kiwoom()
    if kw is None:
        snap["error"] = "키움 로그인 실패"
        return snap

    try:
        bal = kw.get_balance() or {}
    except Exception:
        snap["error"] = "잔고 조회 실패"
        return snap
    out = (bal.get("output2", [{}]) or [{}])[0]
    snap["buying_power"] = int(out.get("buying_power", 0) or 0)
    snap["entr"]         = int(out.get("entr", 0) or 0)
    snap["d2_entra"]     = int(out.get("d2_entra", 0) or 0)
    # kt00004 의 계좌 요약은 kt00018 보유 합과 다를 수 있어 우선 임시 저장 후
    # 보유 합산이 양수면 그 쪽으로 덮어쓴다 (화면 일관성).
    snap["tot_evlu_amt"] = int(out.get("tot_evlu_amt", 0) or 0)
    snap["tot_pur_amt"]  = int(out.get("tot_pur_amt", 0) or 0)
    snap["tot_evlt_pl"]  = int(out.get("tot_evlt_pl", 0) or 0)

    try:
        holdings_raw = kw.get_holdings() or []
    except Exception:
        holdings_raw = []

    holdings: list[dict] = []
    sector_amt: dict[str, int] = {}
    for h in holdings_raw:
        raw_code = h.get("code") or h.get("ticker") or ""
        code = str(raw_code).replace(".KS", "").replace(".KQ", "")
        ticker = h.get("ticker") or (f"{code}.KS" if code else "")
        qty = int(h.get("qty") or 0)
        if qty <= 0:
            continue
        avg_price = float(h.get("avg_price") or 0)
        cur_price = float(h.get("cur_price") or 0)
        eval_amt  = int(h.get("eval_amt") or (qty * cur_price) or 0)
        invested  = int(qty * avg_price)
        pnl       = int(h.get("pnl") or (eval_amt - invested) or 0)
        pnl_rate  = float(h.get("pnl_rate") or ((pnl / invested * 100.0) if invested else 0.0))
        sector    = SECTOR_MAP.get(code, "기타")
        sector_amt[sector] = sector_amt.get(sector, 0) + eval_amt
        holdings.append({
            "ticker":    ticker,
            "code":      code,
            "name":      h.get("name") or code,
            "qty":       qty,
            "avg_price": avg_price,
            "cur_price": cur_price,
            "eval_amt":  eval_amt,
            "pnl":       pnl,
            "pnl_rate":  round(pnl_rate, 2),
            "sector":    sector,
            "weight":    0.0,
        })

    # 보유 합산이 양수일 때만 계좌 요약을 덮는다 (보유 0인 경우 kt00004 그대로).
    holdings_eval = sum(h["eval_amt"] for h in holdings)
    holdings_pur  = int(sum(h["qty"] * h["avg_price"] for h in holdings))
    holdings_pnl  = sum(h["pnl"] for h in holdings)
    if holdings_eval > 0:
        snap["tot_evlu_amt"] = holdings_eval
        snap["tot_pur_amt"]  = holdings_pur
        snap["tot_evlt_pl"]  = holdings_pnl
    if snap["tot_pur_amt"]:
        snap["tot_evlt_pl_rate"] = round(snap["tot_evlt_pl"] / snap["tot_pur_amt"] * 100.0, 2)
    else:
        snap["tot_evlt_pl_rate"] = 0.0

    total_eval_for_weight = holdings_eval or snap["tot_evlu_amt"] or 1
    for h in holdings:
        h["weight"] = round(h["eval_amt"] / total_eval_for_weight * 100.0, 2) \
            if total_eval_for_weight else 0.0
    holdings.sort(key=lambda x: x["eval_amt"], reverse=True)

    sectors = [
        {"sector": k, "eval_amt": v,
         "weight": round(v / total_eval_for_weight * 100.0, 2) if total_eval_for_weight else 0.0}
        for k, v in sorted(sector_amt.items(), key=lambda kv: kv[1], reverse=True)
        if v > 0
    ]

    # DB 기반 실현손익 (매도 청산 누적 / 오늘분)
    try:
        stats = get_summary_stats()
        snap["realized_pnl"]       = int(stats.get("realized_pnl", 0) or 0)
        snap["today_realized_pnl"] = int(stats.get("today_realized_pnl", 0) or 0)
    except Exception:
        pass

    snap["holdings"]       = holdings
    snap["sectors"]        = sectors
    snap["holdings_count"] = len(holdings)
    snap["ok"]             = True
    snap["error"]          = None

    _portfolio_cache["t"]    = now
    _portfolio_cache["data"] = snap
    return snap


def _strip_public(payload):
    """공개 응답 마지막 방어선 — _PUBLIC_FORBIDDEN_KEYS 를 재귀로 제거.

    explicit allowlist 로 dict 를 만들고 있더라도, 향후 누군가 nested 구조에 민감
    필드를 실수로 끼워 넣을 때를 대비한 가드. dict / list 어떤 깊이든 검사한다.
    """
    if isinstance(payload, dict):
        cleaned = {}
        removed = []
        for k, v in payload.items():
            if k in _PUBLIC_FORBIDDEN_KEYS:
                removed.append(k)
                continue
            cleaned[k] = _strip_public(v)
        if removed:
            print(f"[security] /api/public 응답에서 금지 키 제거: {removed}", flush=True)
        return cleaned
    if isinstance(payload, list):
        return [_strip_public(item) for item in payload]
    return payload


@app.route("/api/balance")
@admin_required
def api_balance():
    """키움 잔고 + 보유 합산 — 민감정보. /api/portfolio 와 동일 snapshot 사용."""
    snap = _get_portfolio_snapshot()
    return jsonify({
        "ok":           bool(snap.get("ok")),
        "error":        snap.get("error"),
        "buying_power": snap.get("buying_power", 0),
        "entr":         snap.get("entr", 0),
        "d2_entra":     snap.get("d2_entra", 0),
        "tot_evlu_amt": snap.get("tot_evlu_amt", 0),
        "tot_pur_amt":  snap.get("tot_pur_amt", 0),
        "tot_evlt_pl":  snap.get("tot_evlt_pl", 0),
    }), (200 if snap.get("ok") else 500)


@app.route("/api/portfolio")
@admin_required
def api_portfolio():
    """관리자 전용 — 키움 잔고 + 보유 + 섹터 + 실현손익 합본."""
    snap = _get_portfolio_snapshot()
    if not snap.get("ok"):
        return jsonify({"ok": False, "error": snap.get("error") or "조회 실패"}), 500
    return jsonify(snap)


# ── /api/public/* — 비로그인 공개 엔드포인트 ──────
# 공개 페이지 (/client) 가 사용한다. 민감정보(매수가능/예수금/계좌/주문 거절 사유)는 절대 포함 금지.

def _public_snapshot_or_none() -> dict:
    """공통 snapshot 을 가져오되 실패 사유는 일반화해 외부에 누설 금지."""
    snap = _get_portfolio_snapshot()
    if not snap.get("ok"):
        return {"ok": False, "updated_at": snap.get("updated_at"),
                "error": "데이터 일시 조회 불가"}
    return snap


def _public_failure(updated_at: str | None = None):
    """모든 public 엔드포인트가 실패 시 동일하게 사용하는 무해한 응답."""
    return {
        "ok": False,
        "updated_at": updated_at or datetime.now().isoformat(timespec="seconds"),
        "error": "데이터 일시 조회 불가",
    }


@app.route("/api/public/summary")
def api_public_summary():
    """공개 요약 — total_pnl = unrealized_pnl + realized_pnl."""
    snap = _public_snapshot_or_none()
    if not snap.get("ok"):
        return jsonify(_strip_public(_public_failure(snap.get("updated_at")))), 200
    unrealized = int(snap.get("tot_evlt_pl", 0) or 0)
    realized   = int(snap.get("realized_pnl", 0) or 0)
    payload = {
        "ok":                  True,
        "updated_at":          snap.get("updated_at"),
        "total_value":         int(snap.get("tot_evlu_amt", 0) or 0),
        "unrealized_pnl":      unrealized,
        "unrealized_pnl_rate": float(snap.get("tot_evlt_pl_rate", 0.0) or 0.0),
        "realized_pnl":        realized,
        "today_realized_pnl":  int(snap.get("today_realized_pnl", 0) or 0),
        "total_pnl":           unrealized + realized,
        "holdings_count":      int(snap.get("holdings_count", 0) or 0),
    }
    return jsonify(_strip_public(payload))


@app.route("/api/public/holdings")
def api_public_holdings():
    """공개 보유 목록 — admin 식별자(broker_ord_no/code 등)는 응답에서 의도적으로 제외."""
    snap = _public_snapshot_or_none()
    if not snap.get("ok"):
        return jsonify(_strip_public(_public_failure(snap.get("updated_at")))), 200
    holdings = []
    for h in snap.get("holdings", []) or []:
        holdings.append({
            "ticker":    h.get("ticker"),
            "name":      h.get("name"),
            "qty":       h.get("qty"),
            "avg_price": h.get("avg_price"),
            "cur_price": h.get("cur_price"),
            "eval_amt":  h.get("eval_amt"),
            "pnl":       h.get("pnl"),
            "pnl_rate":  h.get("pnl_rate"),
            "weight":    h.get("weight"),
            "sector":    h.get("sector"),
        })
    return jsonify(_strip_public(
        {"ok": True, "updated_at": snap.get("updated_at"), "holdings": holdings}
    ))


@app.route("/api/public/sectors")
def api_public_sectors():
    """평가금액 기준 섹터 비중 (sector / eval_amt / weight 만)."""
    snap = _public_snapshot_or_none()
    if not snap.get("ok"):
        return jsonify(_strip_public(_public_failure(snap.get("updated_at")))), 200
    sectors = [
        {"sector": s.get("sector"), "eval_amt": s.get("eval_amt"), "weight": s.get("weight")}
        for s in (snap.get("sectors") or [])
    ]
    return jsonify(_strip_public(
        {"ok": True, "updated_at": snap.get("updated_at"), "sectors": sectors}
    ))


@app.route("/api/public/performance")
def api_public_performance():
    """일별 청산 손익. 매수만 있는 날에도 차트가 비지 않도록 오늘=0 패딩 (db_reader 가 처리)."""
    try:
        rows = get_daily_pnl()
    except Exception:
        return jsonify(_strip_public(_public_failure())), 200
    perf = [
        {"date": r.get("date"), "pnl": r.get("pnl", 0), "count": r.get("count", 0)}
        for r in rows
    ]
    return jsonify(_strip_public({"ok": True, "performance": perf}))


@app.route("/api/public/recent-fills")
def api_public_recent_fills():
    """체결/부분체결만 공개. 거절·차단·미체결·취소·SENT 모두 제외, 내부 판단 근거(reason)·broker_ord_no·reject_msg 전부 비노출."""
    try:
        all_orders = get_orders(limit=100) or []
    except Exception:
        return jsonify(_strip_public(_public_failure())), 200
    fills = []
    for o in all_orders:
        cat = (o.get("status_category") or "").lower()
        if cat not in ("filled", "partial"):
            continue
        fills.append({
            "timestamp":      o.get("timestamp"),
            "ticker":         o.get("ticker"),
            "order_type":     o.get("order_type"),
            "qty":            o.get("qty"),
            "filled_qty":     o.get("filled_qty"),
            "price":          o.get("price"),
            "avg_fill_price": o.get("avg_fill_price"),
            "status_label":   o.get("status_label"),
        })
        if len(fills) >= 30:
            break
    return jsonify(_strip_public({"ok": True, "fills": fills}))


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
@login_required
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
