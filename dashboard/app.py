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

from flask import Flask, jsonify, make_response, render_template, request, send_from_directory, session, redirect, url_for

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
# 세션 암호화 키 — 환경변수로 오버라이드 가능 (배포 환경).
# 의도적으로 모듈 상단이 아닌 Flask 인스턴스 직후에 import — 다른 import 가 import 실패 시
# secret_key 만 따로 생성되어도 dashboard 가 부팅하지 않도록.
import os as _os         # noqa: E402  -- intentional below Flask init
import secrets as _secrets  # noqa: E402  -- intentional below Flask init
app.secret_key = _os.getenv("DASHBOARD_SECRET_KEY") or _secrets.token_hex(32)

_ROOT_DIR = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = _ROOT_DIR / "frontend" / "dist"


def _frontend_react_enabled() -> bool:
    """F3/F4 admin React island flag. 기본 OFF — Jinja /advanced 가 그대로 동작."""
    return _os.getenv("FRONTEND_REACT_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _frontend_client_react_enabled() -> bool:
    """F5 client React island flag. admin flag 와 독립. 기본 OFF."""
    return _os.getenv("FRONTEND_CLIENT_REACT_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _frontend_asset_url(path: str) -> str:
    return f"/frontend-dist/{path.lstrip('/')}"


def _frontend_public_asset_url(path: str) -> str:
    """F5.5 — public-only client asset prefix. admin chunk 는 절대 노출 X."""
    return f"/frontend-public-dist/{path.lstrip('/')}"


def _read_manifest() -> dict | None:
    manifest_path = _FRONTEND_DIST / ".vite" / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _vite_admin_assets() -> dict[str, list[str]]:
    """F3/F4 — admin React entry 의 JS/CSS URL list. flag OFF 거나 manifest 부재면 빈 list."""
    if not _frontend_react_enabled():
        return {"js": [], "css": []}
    manifest = _read_manifest()
    if manifest is None:
        return {"js": [], "css": []}
    entry = None
    for key in ("apps/admin/index.html", "apps/admin/main.tsx"):
        if key in manifest:
            entry = manifest[key]
            break
    if not entry:
        for value in manifest.values():
            if value.get("isEntry") and "admin" in str(value.get("file", "")):
                entry = value
                break
    if not entry:
        return {"js": [], "css": []}
    js_files: list[str] = []
    css_files: list[str] = []
    seen: set[str] = set()

    def collect(item: dict) -> None:
        file = item.get("file")
        if isinstance(file, str) and file.endswith(".js") and file not in seen:
            seen.add(file)
            js_files.append(_frontend_asset_url(file))
        for css in item.get("css", []) or []:
            if isinstance(css, str) and css not in css_files:
                css_files.append(_frontend_asset_url(css))
        for imp in item.get("imports", []) or []:
            child = manifest.get(imp)
            if isinstance(child, dict):
                collect(child)

    collect(entry)
    return {"js": js_files, "css": css_files}


def _vite_client_assets() -> dict[str, list[str]]:
    """F5 — client React entry 의 JS/CSS URL list (public route prefix). admin chunk 절대 미포함."""
    if not _frontend_client_react_enabled():
        return {"js": [], "css": []}
    manifest = _read_manifest()
    if manifest is None:
        return {"js": [], "css": []}
    entry = None
    for key in ("apps/client/index.html", "apps/client/main.tsx"):
        if key in manifest:
            entry = manifest[key]
            break
    if not entry:
        for value in manifest.values():
            file = str(value.get("file", ""))
            if value.get("isEntry") and "client" in file and "admin" not in file:
                entry = value
                break
    if not entry:
        return {"js": [], "css": []}
    js_files: list[str] = []
    css_files: list[str] = []
    seen: set[str] = set()

    def collect(item: dict) -> None:
        file = item.get("file")
        if isinstance(file, str) and file.endswith(".js") and file not in seen:
            if "admin" in file:
                return
            seen.add(file)
            js_files.append(_frontend_public_asset_url(file))
        for css in item.get("css", []) or []:
            if isinstance(css, str) and css not in css_files and "admin" not in css:
                css_files.append(_frontend_public_asset_url(css))
        for imp in item.get("imports", []) or []:
            child = manifest.get(imp)
            if isinstance(child, dict):
                collect(child)

    collect(entry)
    return {"js": js_files, "css": css_files}


def _vite_client_public_allowlist() -> set[str]:
    """F5.5 — `/frontend-public-dist/<path>` 에서 서빙 가능한 dist-relative path set.
    admin 접두 / .map 는 set 에서 제외. manifest 없거나 client entry 없으면 빈 set.
    """
    manifest = _read_manifest()
    if manifest is None:
        return set()
    entry = None
    for key in ("apps/client/index.html", "apps/client/main.tsx"):
        if key in manifest:
            entry = manifest[key]
            break
    if not entry:
        for value in manifest.values():
            file = str(value.get("file", ""))
            if value.get("isEntry") and "client" in file and "admin" not in file:
                entry = value
                break
    if not entry:
        return set()
    allow: set[str] = set()
    visited: set[str] = set()

    def visit(item: dict) -> None:
        file = item.get("file")
        if isinstance(file, str):
            if file in visited:
                return
            visited.add(file)
            if "admin" in file or file.endswith(".map"):
                return
            allow.add(file)
        for css in item.get("css", []) or []:
            if isinstance(css, str) and "admin" not in css and not css.endswith(".map"):
                allow.add(css)
        for imp in item.get("imports", []) or []:
            child = manifest.get(imp)
            if isinstance(child, dict):
                visit(child)

    visit(entry)
    return allow


# Phase 10 — 모듈 로드 시 risk_events 90일 retention prune (best-effort 1회).
# 매매 로직과 무관. 실패해도 dashboard 가 죽지 않는다.
try:
    from core.risk_event_logger import prune_risk_events as _prune
    _deleted = _prune(days=90)
    if _deleted:
        import logging as _lg
        _lg.getLogger(__name__).info("[risk_events] startup prune: %d rows older than 90d removed", _deleted)
except Exception:
    pass

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


@app.route("/frontend-dist/<path:filename>")
@admin_required
def frontend_dist(filename: str):
    """F3/F4 — admin React asset 라우트. admin 세션만 통과."""
    return send_from_directory(_FRONTEND_DIST, filename)


@app.route("/frontend-public-dist/<path:filename>")
def frontend_public_dist(filename: str):
    """F5.5 — public client React asset 라우트.
    flag OFF 거나 allowlist 외 경로면 404. .map / path traversal 차단.
    """
    if not _frontend_client_react_enabled():
        return jsonify({"ok": False, "error": "client react disabled"}), 404
    norm = filename.replace("\\", "/").lstrip("/")
    if ".." in norm.split("/") or norm.endswith(".map"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    allow = _vite_client_public_allowlist()
    if norm not in allow:
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_from_directory(_FRONTEND_DIST, norm)


@app.route("/")
@login_required
def index():
    if session.get('role') == 'admin':
        return redirect(url_for('advanced_dashboard'))
    return redirect(url_for('client_dashboard'))


# ── API 엔드포인트 ────────────────────────────

@app.route("/api/summary")
@admin_required
def api_summary():
    stats = get_summary_stats()
    try:
        snap = _get_portfolio_snapshot()
    except Exception:
        snap = {}

    if isinstance(snap, dict) and snap.get("ok"):
        stats.update({
            "ok": True,
            "mode": "LIVE",
            "updated_at": snap.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
            "buying_power": snap.get("buying_power", 0),
            "entr": snap.get("entr", 0),
            "d2_entra": snap.get("d2_entra", 0),
            "tot_evlu_amt": snap.get("tot_evlu_amt", 0),
            "tot_pur_amt": snap.get("tot_pur_amt", 0),
            "tot_evlt_pl": snap.get("tot_evlt_pl", 0),
            "tot_evlt_pl_pct": snap.get("tot_evlt_pl_rate", 0),
            "tot_evlt_pl_rate": snap.get("tot_evlt_pl_rate", 0),
            "holdings_count": snap.get("holdings_count", 0),
            "portfolio_source": snap.get("source", "kiwoom"),
            # realized_pnl / today_realized_pnl remain DB/order-manager based,
            # because Kiwoom balance snapshot is evaluation state, not realized trade PnL.
        })
    else:
        stats.update({
            "ok": False,
            "mode": "LIVE",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "error": (snap or {}).get("error") if isinstance(snap, dict) else "portfolio unavailable",
        })
    return jsonify(stats)


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
def advanced_dashboard():
    """운영자 콘솔 (admin 전용).

    인증 정책:
      - 익명 → /login 으로 리다이렉트
      - client 세션 → 공개 페이지 /client 로 리다이렉트 (관리 권한 없음)
      - admin 세션 → 운영자 콘솔 렌더
    """
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    if session.get('role') != 'admin':
        # client 세션은 공개 보고서만 본다.
        return redirect(url_for('client_dashboard'))
    resp = make_response(render_template(
        "advanced_dashboard.html",
        is_admin=True,
        role='admin',
        frontend_react_enabled=_frontend_react_enabled(),
        frontend_react_assets=_vite_admin_assets(),
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/client")
def client_dashboard():
    """비밀번호 없이 접근 가능한 공개 페이지.

    노출 범위: 국내·해외 스크리너 결과만 (보유 종목·평가/청산 손익 등 계좌 정보 비공개).
    데이터는 /api/public/screener 만 사용한다.

    F5: flag ON 일 때 client React island mount root + bundle script 를 같이 내보낸다.
    flag OFF / manifest 누락이면 기존 Jinja 화면만 표시.
    """
    return render_template(
        "client_dashboard.html",
        frontend_client_react_enabled=_frontend_client_react_enabled(),
        frontend_client_react_assets=_vite_client_assets(),
    )


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


def _money(v) -> int:
    try:
        return int(str(v or 0).replace(",", "").replace("+", "").strip() or 0)
    except Exception:
        return 0


def _pick_buying_power(deposit: dict) -> int:
    """Return real buyable cash from kt00001 fields."""
    if not isinstance(deposit, dict):
        return 0
    raw = deposit.get("raw") if isinstance(deposit.get("raw"), dict) else {}
    primary = _money(deposit.get("ord_alow_amt"))
    if primary > 0:
        return primary
    for key in ("d2_ord_psbl_amt", "bncr_buy_alowa", "pymn_alow_amt", "100stk_ord_alow_amt"):
        val = _money(deposit.get(key)) or _money(raw.get(key))
        if val > 0:
            return val
    return 0


def _probe_realtime_server() -> tuple[str, str]:
    """Check the local realtime Socket.IO dashboard process on port 5001."""
    try:
        import json as _json
        import urllib.request as _urlreq

        with _urlreq.urlopen("http://127.0.0.1:5001/api/health", timeout=0.7) as r:
            if getattr(r, "status", 200) != 200:
                return "warn", "5001 응답 이상"
            body = _json.loads(r.read().decode("utf-8", errors="replace") or "{}")
        if body.get("status") == "ok":
            return "ok", "연결됨"
        return "warn", "5001 응답 이상"
    except Exception:
        return "idle", "별도 프로세스"


def _runtime_balance_snapshot() -> dict | None:
    """Use main's last safe runtime balance snapshot when direct REST fails."""
    try:
        from core.runtime_state import read_runtime_state, default_path

        state = read_runtime_state(default_path())
    except Exception:
        return None
    if not state.get("ok"):
        return None
    bal = state.get("balance") or {}
    if not isinstance(bal, dict):
        return None
    if not any(_money(bal.get(k)) for k in ("buying_power", "tot_evlu_amt", "tot_pur_amt", "tot_evlt_pl")):
        return None
    realized_pnl = 0
    today_realized_pnl = 0
    try:
        stats = get_summary_stats()
        realized_pnl = int(stats.get("realized_pnl", 0) or 0)
        today_realized_pnl = int(stats.get("today_realized_pnl", 0) or 0)
    except Exception:
        pass
    return {
        "ok": True,
        "updated_at": state.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
        "buying_power": _money(bal.get("buying_power")),
        "entr": _money(bal.get("entr")),
        "d2_entra": _money(bal.get("d2_entra")),
        "tot_evlu_amt": _money(bal.get("tot_evlu_amt")),
        "tot_pur_amt": _money(bal.get("tot_pur_amt")),
        "tot_evlt_pl": int(float(bal.get("tot_evlt_pl") or 0)),
        "tot_evlt_pl_rate": float(bal.get("tot_evlt_pl_rate") or 0.0),
        "realized_pnl": realized_pnl,
        "today_realized_pnl": today_realized_pnl,
        "holdings_count": int(bal.get("holdings_count") or 0),
        "holdings": [],
        "sectors": [],
        "error": None,
        "source": "runtime_state",
    }

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
        runtime_snap = _runtime_balance_snapshot()
        if runtime_snap:
            _portfolio_cache["t"] = now
            _portfolio_cache["data"] = runtime_snap
            return runtime_snap
        snap["error"] = "키움 로그인 실패"
        return snap

    try:
        deposit = kw.get_deposit_detail() if hasattr(kw, "get_deposit_detail") else {}
        bal = kw.get_balance() or {}
    except Exception:
        runtime_snap = _runtime_balance_snapshot()
        if runtime_snap:
            _portfolio_cache["t"] = now
            _portfolio_cache["data"] = runtime_snap
            return runtime_snap
        snap["error"] = "잔고 조회 실패"
        return snap
    out = (bal.get("output2", [{}]) or [{}])[0]
    snap["buying_power"] = _pick_buying_power(deposit)
    snap["entr"]         = int((deposit or {}).get("entr") or out.get("entr", 0) or 0)
    snap["d2_entra"]     = int((deposit or {}).get("d2_entra") or out.get("d2_entra", 0) or 0)
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


@app.route("/api/admin/system_status")
@admin_required
def api_admin_system_status():
    """운영자 콘솔용 시스템 상태 종합 (Operator Status Row 가 사용).

    응답:
      ok, updated_at,
      kiwoom / telegram / websocket / rest : "ok" | "warn" | "fail" | "idle"
      kiwoom_label / telegram_label / ... : 화면용 짧은 한국어 문구
      daily_loss: { limit, used, used_pct, today_pnl, halted, limit_text, used_text }
    """
    from config import RISK_CONFIG, TELEGRAM_CONFIG

    # Kiwoom — 30초 캐시되는 portfolio snapshot 의 ok 필드를 그대로 신호로 사용.
    kw_state, kw_label = "fail", "연결 실패"
    try:
        snap = _get_portfolio_snapshot()
        if snap.get("ok"):
            kw_state = "ok"
            kw_label = "연결됨"
        else:
            err = (snap.get("error") or "").strip()
            kw_label = (err[:30] or "오류")
    except Exception:
        kw_state, kw_label = "fail", "오류"

    # Telegram — bot_token / chat_id 둘 다 채워져 있으면 ok, 아니면 idle.
    tg_token = (TELEGRAM_CONFIG.get("bot_token") or "").strip()
    tg_chat  = (TELEGRAM_CONFIG.get("chat_id")  or "").strip()
    if tg_token and tg_chat:
        tg_state, tg_label = "ok", "설정됨"
    else:
        tg_state, tg_label = "idle", "미설정"

    # WebSocket/Realtime dashboard status. The live feed connects to the
    # separate 5001 Socket.IO server, so probe that health endpoint here.
    ws_state, ws_label = _probe_realtime_server()

    # REST fallback — Kiwoom REST 핸들이 살아있으면 사용 가능.
    if kw_state == "ok":
        rest_state, rest_label = "ok", "사용 가능"
    else:
        rest_state, rest_label = "fail", "Kiwoom 실패"

    # 일일 손실 한도 — DB 기반 today_realized_pnl 과 RISK_CONFIG 한도 비교.
    # 손실(음수)만 한도에 카운트, 이익은 0으로 본다 (한도 사용률은 깎이지 않음).
    daily_limit = abs(int(RISK_CONFIG.get("daily_loss_limit", -10000) or -10000))
    today_pnl = 0
    try:
        stats = get_summary_stats()
        today_pnl = int(stats.get("today_realized_pnl", 0) or 0)
    except Exception:
        pass
    used = max(0, -today_pnl)
    used_pct = (used / daily_limit * 100.0) if daily_limit > 0 else 0.0
    halted = used_pct >= 100.0

    return jsonify({
        "ok":          True,
        "updated_at":  datetime.now().isoformat(timespec="seconds"),
        "kiwoom":      kw_state,   "kiwoom_label":   kw_label,
        "telegram":    tg_state,   "telegram_label": tg_label,
        "websocket":   ws_state,   "websocket_label":ws_label,
        "rest":        rest_state, "rest_label":     rest_label,
        "daily_loss": {
            "limit":      daily_limit,
            "used":       used,
            "used_pct":   round(used_pct, 1),
            "today_pnl":  today_pnl,
            "halted":     halted,
            "limit_text": (f"{daily_limit:,}원" if daily_limit > 0 else "—"),
            "used_text":  (f"{used:,}원"        if used        > 0 else "0원"),
        },
    })


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
        "tot_evlt_pl_pct":  snap.get("tot_evlt_pl_rate", 0),
        "tot_evlt_pl_rate": snap.get("tot_evlt_pl_rate", 0),
    }), (200 if snap.get("ok") else 500)


@app.route("/api/portfolio")
@admin_required
def api_portfolio():
    """관리자 전용 — 키움 잔고 + 보유 + 섹터 + 실현손익 합본."""
    snap = _get_portfolio_snapshot()
    if not snap.get("ok"):
        return jsonify({"ok": False, "error": snap.get("error") or "조회 실패"}), 500
    return jsonify(snap)


def _time_hhmm(value, default: str) -> str:
    """Return HH:MM if valid, otherwise default."""
    try:
        raw = str(value or "").strip()
        hh, mm = raw.split(":", 1)
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception:
        pass
    return default


@app.route("/api/admin/minute_daytrade")
@admin_required
def api_admin_minute_daytrade():
    """Read-only minute daytrade control snapshot."""
    try:
        import config as cfg
    except Exception as e:
        return jsonify({"ok": False, "error": f"config import failed: {e}"}), 500

    risk = cfg.get_risk_config()
    try:
        portfolio = _get_portfolio_snapshot()
    except Exception:
        portfolio = {"ok": False, "buying_power": 0}
    buying_power = int(_money((portfolio or {}).get("buying_power")))

    cash_buffer = float(risk.get("daytrade_one_share_cash_buffer_pct", 0.95) or 0.95)
    slip_buffer = float(risk.get("daytrade_one_share_slippage_buffer_pct", 0.01) or 0.01)
    max_trade = int(risk.get("max_invest_per_trade", 0) or 0)
    capital_limit = int(risk.get("capital_limit", 0) or 0)
    day_bucket = int(min(capital_limit, int(buying_power * 0.5))) if buying_power > 0 else 0
    orderable = int(max(0, buying_power * cash_buffer))
    if max_trade > 0:
        orderable = min(orderable, max_trade)
    max_one_share_price = int(orderable / (1.0 + slip_buffer)) if orderable > 0 else 0

    return jsonify({
        "ok": True,
        "mode": "minute_scalp",
        "description": "1-minute daytrade control; orders still pass risk_manager and order_manager.",
        "buying_power": buying_power,
        "portfolio_source": (portfolio or {}).get("source") or "kiwoom",
        "capital_limit": capital_limit,
        "daytrade_bucket": day_bucket,
        "max_invest_per_trade": max_trade,
        "max_one_share_price": max_one_share_price,
        "scan_interval_minutes": int(cfg.get_scan_interval()),
        "entry_start": str(risk.get("entry_start", "09:40")),
        "entry_end": str(risk.get("entry_end", "11:00")),
        "no_hold_after": str(risk.get("daytrade_no_hold_after", "14:50")),
        "force_close_time": str(risk.get("force_close_time", "15:10")),
        "max_daily_entries": int(risk.get("daytrade_max_per_day", 2) or 2),
        "min_confidence": int(risk.get("min_confidence", 75) or 75),
        "min_strategies": int(risk.get("min_strategies", 3) or 3),
        "min_volume_ratio": float(risk.get("min_volume_ratio", 1.5) or 1.5),
        "rsi_min": float(risk.get("rsi_min", 45) or 45),
        "rsi_max": float(risk.get("rsi_max", 68) or 68),
        "min_price": int(risk.get("daytrade_min_price", 20_000) or 20_000),
        "cash_buffer_pct": cash_buffer,
        "slippage_buffer_pct": slip_buffer,
        "stop_loss_pct": float(risk.get("stop_loss_pct", -0.02) or -0.02),
        "take_profit_pct": float(risk.get("take_profit_pct", 0.02) or 0.02),
        "time_stop_soft_min": int(risk.get("daytrade_time_stop_soft_min", 30) or 30),
        "time_stop_hard_min": int(risk.get("daytrade_time_stop_hard_min", 60) or 60),
        "requires_restart": True,
    })


# ── Phase 8: 운영 콘솔 실시간 상태 데이터 ─────────────
#
# 두 admin-only endpoint:
#   GET /api/admin/daytrade_state  - main 가 db/runtime_state.json 에 저장한 단타 상태 snapshot
#   GET /api/admin/risk_events     - core/risk_event_logger 가 SQLite 에 누적한 위험 이벤트
#
# 두 API 모두 매매 로직과 분리. 읽기만 한다.
# /client 에는 절대 노출되지 않으며 (admin_required), 응답에는 raw API 응답/계좌번호/token 이 포함되지 않는다.

@app.route("/api/admin/daytrade_state")
@admin_required
def api_admin_daytrade_state():
    """단타 엔진 상태 snapshot — main 가 30초마다 db/runtime_state.json 에 기록.

    snapshot 이 없거나 읽기 실패 시 안전 기본값을 반환한다 (UI 가 깨지지 않도록).
    """
    try:
        from core.runtime_state import read_runtime_state, default_path, default_state
    except Exception as e:
        return jsonify({"ok": False, "error": f"runtime_state import failed: {e}"}), 500

    snap = read_runtime_state(default_path())
    if not snap.get("ok"):
        # snapshot 부재 — 안전 기본값을 ok=True 로 돌려준다 (UI placeholder).
        fb = default_state()
        fb["snapshot_missing"] = True
        return jsonify(fb)
    return jsonify(snap)


@app.route("/api/admin/process_status")
@admin_required
def api_admin_process_status():
    """Phase 13 §5.1 — main 트레이더 프로세스 상태 종합.

    응답:
      {
        "main": {
          "status": "running|stale_lock|stopped|unknown",
          "pid": 12345, "started_at": "...", "heartbeat_at": "...",
          "heartbeat_age_sec": 8.0, "phase": "13",
          "pricing_mode": "limit_repricing", "stale": false
        }
      }
    민감 키(token/account_no/raw 등)는 read_process_lock 단계에서 차단되어 들어올 수 없다.
    """
    try:
        from core.process_lock import read_process_lock, is_process_alive
    except Exception as e:
        return jsonify({"ok": False, "error": f"process_lock import failed: {e}"}), 500

    raw = read_process_lock("main") or {}
    if not raw:
        return jsonify({"main": {"status": "stopped"}})

    pid = int(raw.get("pid") or 0)
    alive = bool(pid and is_process_alive(pid))
    # heartbeat 경과 초
    hb_age = None
    ts = raw.get("heartbeat_at") or raw.get("started_at")
    if ts:
        try:
            from datetime import datetime
            hb_age = max(0.0, (datetime.now() - datetime.fromisoformat(str(ts))).total_seconds())
        except Exception:
            hb_age = None
    if not alive:
        status = "stale_lock"
    elif alive:
        status = "running"
    else:
        status = "unknown"
    stale_flag = bool(hb_age is not None and hb_age > 60)
    return jsonify({
        "main": {
            "status":            status,
            "pid":               pid,
            "started_at":        raw.get("started_at"),
            "heartbeat_at":      raw.get("heartbeat_at"),
            "heartbeat_age_sec": (round(hb_age, 1) if hb_age is not None else None),
            "phase":             raw.get("phase"),
            "pricing_mode":      raw.get("pricing_mode"),
            "hostname":          raw.get("hostname"),
            "stale":             stale_flag,
        }
    })


@app.route("/api/admin/risk_events")
@admin_required
def api_admin_risk_events():
    """위험 이벤트 영구 저장 조회 — risk_events 테이블 (level/category/ticker/message/meta)."""
    try:
        limit = int(request.args.get("limit", "50") or 50)
    except Exception:
        limit = 50
    limit = max(1, min(200, limit))
    try:
        from core.risk_event_logger import get_risk_events
        events = get_risk_events(limit=limit)
    except Exception as e:
        return jsonify({"ok": False, "error": f"risk_events read failed: {e}",
                        "events": [], "retention_days": 90}), 200
    return jsonify({
        "ok": True,
        "count": len(events),
        "limit": limit,
        "retention_days": 90,
        "events": events,
    })


# ── /api/public/* — 공개 / 관리자 분리 ───────────
#
# 공개 보고 정책 변경 (2026-04 운영자 결정):
#   - 진정한 공개 엔드포인트는 /api/public/screener 하나뿐.
#     국내·해외 스크리너 결과(score≥70)만 비로그인으로 노출한다.
#   - 그 외 portfolio/holdings/sectors/performance/recent-fills 는
#     계좌·체결 정보를 함께 보여주기 때문에 admin 전용으로 유지한다.
#   - 과거 호환을 위해 URL prefix /api/public/ 는 유지하되 admin 게이트 적용.

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


@app.route("/api/public/screener")
def api_public_screener():
    """비로그인 공개 — 국내/해외 통합 스크리너 결과 (score>=70).

    Query: market = all | domestic | foreign (default: all)
    응답: {ok, updated_at, count, market, items:[{ticker,name,price,score,reasons,is_domestic,screened_at}]}
    민감 키(buying_power/account/raw 등) 절대 포함 금지 — _strip_public 으로 한 번 더 거른다.
    """
    market = request.args.get("market", "all").lower()
    if market not in ("all", "domestic", "foreign"):
        market = "all"
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT ticker, name, price, score, reasons, screened_at "
                "FROM screener_results WHERE rowid IN ("
                "  SELECT MAX(rowid) FROM screener_results GROUP BY ticker"
                ") ORDER BY score DESC, screened_at DESC LIMIT 500"
            ).fetchall()
    except Exception:
        return jsonify(_strip_public(_public_failure())), 200

    def _parse_reasons(raw):
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return [s.strip() for s in str(raw).split(",") if s.strip()]

    items = []
    last_seen = ""
    for r in rows:
        ticker = r[0] or ""
        score  = float(r[3] or 0)
        if score < 70:
            continue
        is_domestic = ticker.endswith(".KS") or ticker.endswith(".KQ")
        if market == "domestic" and not is_domestic:
            continue
        if market == "foreign" and is_domestic:
            continue
        screened_at = r[5] or ""
        if screened_at and (not last_seen or screened_at > last_seen):
            last_seen = screened_at
        items.append({
            "ticker":      ticker,
            "name":        r[1] or "",
            "price":       r[2],
            "score":       round(score, 1),
            "reasons":     _parse_reasons(r[4])[:6],     # 최대 6개로 자름
            "is_domestic": bool(is_domestic),
            "screened_at": screened_at,
        })
    return jsonify(_strip_public({
        "ok":         True,
        "market":     market,
        "count":      len(items),
        "updated_at": last_seen or datetime.now().isoformat(timespec="seconds"),
        "items":      items,
    }))


@app.route("/api/public/summary")
@admin_required
def api_public_summary():
    """[admin] 공개 prefix 호환용 — 운영자 보고서 요약."""
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
@admin_required
def api_public_holdings():
    """[admin] 공개 prefix 호환용 — 운영자 보유 목록."""
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
@admin_required
def api_public_sectors():
    """[admin] 평가금액 기준 섹터 비중."""
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
@admin_required
def api_public_performance():
    """[admin] 일별 청산 손익."""
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
@admin_required
def api_public_recent_fills():
    """[admin] 체결/부분체결 행만. 거절·차단·미체결·취소·SENT·broker_ord_no·reject_msg·reason 비노출."""
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
                def _safe(v, d=0.0):
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        return d
                    return float(v)
                def _safei(v):
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        return 0
                    return int(v)
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
        import subprocess
        import sys
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
        try:
            kw.login()
        except Exception:
            pass

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
        seen = set()
        valid = []
        for n in normalized:
            if n not in seen:
                seen.add(n)
                valid.append(n)
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
    import subprocess
    import sys
    import time
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
        allowed = {
            "max_positions", "max_invest_per_trade", "stop_loss_pct",
            "take_profit_pct", "daily_loss_limit", "min_confidence",
            "capital_limit", "daytrade_max_per_day", "min_strategies",
            "min_volume_ratio", "rsi_min", "rsi_max", "entry_start",
            "entry_end", "daytrade_no_hold_after", "force_close_time",
            "daytrade_min_price", "daytrade_one_share_cash_buffer_pct",
            "daytrade_one_share_slippage_buffer_pct",
            "daytrade_time_stop_soft_min", "daytrade_time_stop_hard_min",
        }
        patch = {k: v for k, v in data["risk_config"].items() if k in allowed}
        if "capital_limit" in patch:
            patch["capital_limit"] = max(0, min(int(patch["capital_limit"] or 0), 2_000_000))
        if "max_invest_per_trade" in patch:
            patch["max_invest_per_trade"] = max(1, min(int(patch["max_invest_per_trade"] or 0), 1_000_000))
        if "daytrade_max_per_day" in patch:
            patch["daytrade_max_per_day"] = max(1, min(int(patch["daytrade_max_per_day"] or 0), 5))
        if "min_strategies" in patch:
            patch["min_strategies"] = max(1, min(int(patch["min_strategies"] or 0), 5))
        if "min_confidence" in patch:
            patch["min_confidence"] = max(65, min(int(patch["min_confidence"] or 0), 90))
        if "min_volume_ratio" in patch:
            patch["min_volume_ratio"] = max(1.0, min(float(patch["min_volume_ratio"] or 1.5), 5.0))
        if "rsi_min" in patch:
            patch["rsi_min"] = max(20.0, min(float(patch["rsi_min"] or 45), 60.0))
        if "rsi_max" in patch:
            patch["rsi_max"] = max(50.0, min(float(patch["rsi_max"] or 68), 80.0))
        if "stop_loss_pct" in patch:
            patch["stop_loss_pct"] = max(-0.05, min(float(patch["stop_loss_pct"] or -0.02), -0.005))
        if "take_profit_pct" in patch:
            patch["take_profit_pct"] = max(0.005, min(float(patch["take_profit_pct"] or 0.02), 0.08))
        if "daytrade_min_price" in patch:
            patch["daytrade_min_price"] = max(1_000, min(int(patch["daytrade_min_price"] or 20_000), 100_000))
        if "daytrade_one_share_cash_buffer_pct" in patch:
            patch["daytrade_one_share_cash_buffer_pct"] = max(
                0.5, min(float(patch["daytrade_one_share_cash_buffer_pct"] or 0.95), 0.98)
            )
        if "daytrade_one_share_slippage_buffer_pct" in patch:
            patch["daytrade_one_share_slippage_buffer_pct"] = max(
                0.0, min(float(patch["daytrade_one_share_slippage_buffer_pct"] or 0.01), 0.03)
            )
        if "daytrade_time_stop_soft_min" in patch:
            patch["daytrade_time_stop_soft_min"] = max(5, min(int(patch["daytrade_time_stop_soft_min"] or 30), 60))
        if "daytrade_time_stop_hard_min" in patch:
            patch["daytrade_time_stop_hard_min"] = max(10, min(int(patch["daytrade_time_stop_hard_min"] or 60), 120))
        if "rsi_min" in patch and "rsi_max" in patch and patch["rsi_min"] >= patch["rsi_max"]:
            patch["rsi_min"] = max(20.0, float(patch["rsi_max"]) - 5.0)
        if (
            "daytrade_time_stop_soft_min" in patch
            and "daytrade_time_stop_hard_min" in patch
            and patch["daytrade_time_stop_soft_min"] >= patch["daytrade_time_stop_hard_min"]
        ):
            patch["daytrade_time_stop_soft_min"] = max(5, int(patch["daytrade_time_stop_hard_min"]) - 5)
        for key, default in (
            ("entry_start", "09:40"),
            ("entry_end", "11:00"),
            ("daytrade_no_hold_after", "14:50"),
            ("force_close_time", "15:10"),
        ):
            if key in patch:
                patch[key] = _time_hhmm(patch[key], default)
        current.setdefault("risk_config", {}).update(patch)

    if "scan_interval_minutes" in data:
        v = int(data["scan_interval_minutes"])
        current["scan_interval_minutes"] = max(1, min(v, 1440))

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

