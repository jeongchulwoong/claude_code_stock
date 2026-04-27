"""
main_v2.py - AI Auto Trader (full integrated version, LIVE)

Included modules:
  - DB migration (auto on startup)
  - Health monitor (per-cycle health check)
  - Stock screener (auto-run at market open 09:05)
  - News sentiment analysis (per scan)
  - Multi-timeframe + integrated AI judgment
  - Kelly Criterion position sizing
  - Sector rotation strategy
  - Price/condition alert system
  - Portfolio VaR/CVaR
  - Auto daily/weekly reports
  - Telegram remote commands
  - Strategy performance tracking
  - Performance attribution analysis (P&L breakdown)

Run:
    python main_v2.py
"""

from __future__ import annotations

import signal
import sys
import time

# Windows 콘솔 UTF-8 강제 설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, time as dtime

from loguru import logger

from config import (LOG_DIR, LONG_RISK_CONFIG, RISK_CONFIG,
                    SCHEDULE_CONFIG, WATCH_LIST, WATCH_LIST_LONG,
                    WATCH_LIST_PRIORITY)

# 로깅 설정
logger.remove()
# 1) 콘솔: INFO 이상 컬러 출력
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
# 2) 종합 로그: DEBUG 전체, 90일 보관
logger.add(LOG_DIR / "trade_{time:YYYYMMDD}.log",
           level="DEBUG", rotation="1 day", retention="90 days", encoding="utf-8")
# 3) 에러 전용 로그: ERROR/CRITICAL, 180일 보관
logger.add(LOG_DIR / "errors_{time:YYYYMMDD}.log",
           level="ERROR", rotation="1 day", retention="180 days", encoding="utf-8",
           format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}")
# 4) 거래 전용 로그: 매수/매도/청산/전환/체결/손절/익절, 365일 보관
_TRADE_KEYWORDS = (
    "BUY", "SELL", "ORDER", "FILLED", "STOP", "TAKE", "FORCE_CLOSE",
    "CONVERT", "POSITION", "BLOCKED", "AI",
)
logger.add(LOG_DIR / "trades_{time:YYYYMMDD}.log",
           level="INFO", rotation="1 day", retention="365 days", encoding="utf-8",
           filter=lambda r: any(k in r["message"] for k in _TRADE_KEYWORDS),
           format="{time:HH:mm:ss} | {level:<7} | {message}")

# ???? ?ル굝利?筌ｌ꼶????????????????????????????????????????????????????????????????????
_running = True
def _sig(s, f):
    global _running; logger.warning("Termination signal received"); _running = False
signal.signal(signal.SIGINT,  _sig)
signal.signal(signal.SIGTERM, _sig)

# ?? ?댁쁺 媛???????????????????????????????????????
# ?좉퇋 留ㅼ닔 李⑤떒 ?ъ쑀. None ?대㈃ 留ㅼ닔 ?덉슜. 臾몄옄?댁씠硫?洹??ъ쑀濡?紐⑤뱺 ?좉퇋 留ㅼ닔 寃뚯씠??
# 留ㅻ룄(?먯젅/?듭젅/媛뺤젣泥?궛)?????뚮옒洹몄? 臾닿??섍쾶 怨꾩냽 ?숈옉.
_buy_disabled_reason: "Optional[str]" = None
# 留덉?留됱쑝濡??쒖꽭 ?곗씠?곕? ?뺤긽?곸쑝濡?諛쏆? ?쒓컖 (epoch sec). 0 = ?꾩쭅 ??諛쏆쓬.
_last_data_ok_at:      float = 0.0
# stale ?뚮┝ ?꾨같 諛⑹? ??1?뚮쭔 ?뚮━怨??뺤긽 蹂듦뎄 ??1?뚮쭔 蹂듦뎄 ?뚮┝.
_stale_notified:       bool  = False
DATA_STALE_SEC = 90    # 60~120 ?ъ씠; price ?쒖꽭媛 ???쒓컙 ?댁긽 媛깆떊 ???섎㈃ stale 濡?媛꾩＜

# ???? ??뽰삢 ??볦퍢 ??????????????????????????????????????????????????????????????????
def is_market(t=None):
    t = t or datetime.now().time()
    o = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    c = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    return o <= t <= c

def is_close_window():
    t = datetime.now().time()
    return dtime(15,30) <= t <= dtime(15,36)

def is_force_close_window():
    """Force-close window (force_close_time ~ 15:29). Default 15:10."""
    t = datetime.now().time()
    fc = RISK_CONFIG.get("force_close_time", "15:10").split(":")
    fc_time = dtime(int(fc[0]), int(fc[1]))
    return fc_time <= t < dtime(15, 30)


def is_after_hours_close():
    """After-hours close trading (15:40~16:00). No new buys, sells/forced close only."""
    t = datetime.now().time()
    return dtime(15, 40) <= t <= dtime(16, 0)


def is_after_hours_single():
    """After-hours single-price (16:00~18:00). Closing only."""
    t = datetime.now().time()
    return dtime(16, 0) < t <= dtime(18, 0)


def is_us_market_session():
    """
    NYSE/NASDAQ regular hours (KST).
    Standard (EST): 23:30~06:00 KST
    Daylight (EDT): 22:30~05:00 KST
    DST exact dates ambiguous - monitoring 22:30~06:00 broadly.
    """
    t = datetime.now().time()
    return t >= dtime(22, 30) or t < dtime(6, 0)


def main():
    logger.info("="*65)
    logger.info("  AI Auto Trader v2 - LIVE")
    logger.info("  Tickers:{} | {}", len(WATCH_LIST),
                datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("="*65)

    logger.critical("[!] LIVE trading starts in 10 seconds")
    for i in range(10, 0, -1): logger.critical("  {}s...", i); time.sleep(1)

    # ???? 1. DB ?λ뜃由????????????????????????????????????????????????????
    from core.db_manager import init_db
    db_mgr = init_db()

    # ???? 2. ?뚮똾猷??곕뱜 ?λ뜃由????????????????????????????????????????
    from core.kiwoom_api import get_kiwoom_api
    from core.data_collector import DataCollector
    from core.integrated_judge import IntegratedJudge
    from core.order_manager import OrderManager
    from core.portfolio_manager import PortfolioManager
    from core.position_sizer import PositionSizer
    from core.report_generator import ReportGenerator
    from core.risk_manager import RiskManager, STYLE_DAY, STYLE_LONG
    from core.screener import MarketScreener
    from core.health_monitor import HealthMonitor
    from core.alert_manager import AlertManager
    from core.fundamental_gate import FundamentalGate
    from core.strategy_tracker import StrategyTracker
    from core.performance_attribution import PerformanceAttributor
    from core.telegram_bot import TelegramBot
    from core.telegram_commander import TelegramCommander
    from core.adaptive_tuner import AdaptiveTuner

    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.breakout import BreakoutStrategy
    from strategies.volume_surge import VolumeSurgeStrategy
    from strategies.sector_rotation import SectorRotationStrategy

    kw = get_kiwoom_api()
    dc = DataCollector(kw)
    rm      = RiskManager()
    om      = OrderManager(kw, rm)
    pm      = PortfolioManager(rm)
    ps      = PositionSizer(rm)
    rg      = ReportGenerator()
    hm      = HealthMonitor(kw, rm)
    am         = AlertManager()
    fund_gate  = FundamentalGate()
    int_judge  = IntegratedJudge()
    # ??쎄쾿?귐됯섐?????遺얠컲??AI 雅뚯눘???????? ?癒?땾 ?怨쀭뀱
    screener   = MarketScreener(dc, fundamental_gate=fund_gate, integrated_judge=int_judge)
    tracker    = StrategyTracker()
    attrib     = PerformanceAttributor()
    tg         = TelegramBot()
    cmd        = TelegramCommander(rm, rg, om)
    tuner      = AdaptiveTuner()

    try:
        tune_result = tuner.tune(force=False)
        logger.info(
            "Adaptive tuning applied | sample:{} winrate:{:.1%} avg_net:{:+.2f}% PF:{:.2f} | min_conf:{} R:R:{:.2f}",
            tune_result.trades, tune_result.winrate, tune_result.avg_net_pnl_pct,
            tune_result.profit_factor, tune_result.min_confidence,
            tune_result.min_effective_rr,
        )
    except Exception as e:
        logger.warning("Adaptive tuning failed: {}", e)

    sr_strategy = SectorRotationStrategy(market_phase="unknown")
    strategies  = [
        MomentumStrategy(),
        MeanReversionStrategy(),
        BreakoutStrategy(),
        VolumeSurgeStrategy(),
        sr_strategy,
    ]

    available_cash      = 10_000_000
    daily_reported      = False
    health_checked      = time.time()
    scan_count          = 0
    long_scan_count     = 0
    interval            = SCHEDULE_CONFIG["scan_interval_minutes"] * 60   # ???: 10??
    long_interval       = 30 * 60                                          # longterm: 30 min
    last_long_scan      = 0.0   # last longterm scan ts
    last_hot_scan       = 0.0   # last 70+ alert scan ts
    # ??덉읅 雅뚯눊由? ??볥럢 ?類?뇣??沃섎㈇???類?뇣?關?좑쭖?10?? 域???30??
    HOT_INTERVAL_ACTIVE = 10 * 60
    HOT_INTERVAL_QUIET  = 30 * 60
    _alerted_today: set[str] = set()                                       # already alerted today
    _alert_date: str    = ""                                               # alert list date tracker

    def _style_invested(style: str) -> float:
        return sum(p.invested_amount for p in rm.get_positions().values() if p.style == style)

    def _style_cash(style: str) -> int:
        cfg = RISK_CONFIG if style == "daytrading" else LONG_RISK_CONFIG
        limit = int(cfg.get("capital_limit", 0) or 0)
        invested = _style_invested(style)
        budget_left = max(0.0, limit - invested) if limit > 0 else float(available_cash)
        return int(max(0.0, min(float(available_cash), budget_left)))

    # 嚥≪뮄???+ ?遺쏀?鈺곌퀬??
    try:
        login_ok = kw.login()
        if login_ok is False:
            # ?ㅼ? ?좏겙 諛쒓툒/?좎? ?ㅽ뙣 ??吏꾪뻾?섎㈃ 紐⑤뱺 二쇰Ц/議고쉶媛 ?ㅽ뙣?섎?濡?利됱떆 醫낅즺.
            logger.critical("Login returned False ??aborting (token/credential issue)")
            try:
                tg.notify_text("???ㅼ? 濡쒓렇???ㅽ뙣 (?좏겙 諛쒓툒 遺덇?) ??遊?醫낅즺")
            except Exception:
                pass
            sys.exit(1)
    except Exception as e:
        logger.critical("Login failed: {}", e)
        try:
            tg.notify_text(f"???ㅼ? 濡쒓렇???덉쇅 ??遊?醫낅즺\n{e}")
        except Exception:
            pass
        sys.exit(1)

    # ?遺쏀?鈺곌퀬??(??브탢??筌뤴뫁?????筌뤴뫀紐? ??kt00001(??됰땾疫뀀뜆湲?? ?怨쀪퐨, ??쎈솭 ??kt00004
    available_cash = 0
    try:
        deposit = kw.get_deposit_detail() if hasattr(kw, "get_deposit_detail") else {}
        # kt00001 ??雅뚯눖揆揶쎛?觀????온???袁⑤궖 ?袁⑤굡 餓?0 ?袁⑤빒 筌ㅼ뮆?롥첎誘れ뱽 ????
        dp_candidates = [
            deposit.get("ord_alow_amt", 0),
            deposit.get("d2_ord_psbl_amt", 0),
            deposit.get("d2_entra", 0),
            deposit.get("entr", 0),
        ]
        best = max((c for c in dp_candidates if c > 0), default=0)

        bal = kw.get_balance()
        output = bal.get("output2", [{}])[0] if bal.get("output2") else {}
        eval_amt   = int(output.get("tot_evlu_amt", 0) or 0)
        entr       = int(output.get("entr", 0) or 0)
        d2_entra   = int(output.get("d2_entra", 0) or 0)
        fallback   = int(output.get("buying_power", 0) or 0) or entr or d2_entra

        available_cash = best or fallback
        logger.info(
            "Balance: total_eval={:,} / cash={:,} / D+2={:,} / buying_power={:,} "
            "(kt00001.ord_alow_amt={:,}, d2_ord_psbl={:,})",
            eval_amt, entr, d2_entra, available_cash,
            deposit.get("ord_alow_amt", 0), deposit.get("d2_ord_psbl_amt", 0),
        )
        # 당일 시작 자본 = 주문가능금액 + 평가금액
        rm.set_start_capital(available_cash + eval_amt)
    except Exception as e:
        logger.warning("Balance lookup failed: {}", e)

    # 자본 규모 안내: 소액 단타는 비용 비중이 커서 검증 모드로 간주
    if 0 < available_cash < 2_000_000:
        logger.warning(
            "[CAPITAL NOTICE] current buying power {:,} KRW. For statistical edge in minute daytrading, "
            "minimum 10M~20M KRW recommended. Trading cost 0.4% (fee+tax+slippage) "
            "takes large share at small capital -- alpha hard to achieve. "
            "Currently in learning/verification mode.", available_cash,
        )

    # 프리플라이트 결과를 누적해 마지막에 장전 점검 메시지로 통합 전송
    _preflight = {
        "login_ok":       True,                              # 위에서 kw.login() 통과
        "deposit_ok":     bool(available_cash > 0),
        "available_cash": int(available_cash or 0),
        "holdings_ok":    False,
        "holdings_count": 0,
        "ws_ok":          False,
        "errors":         [] if available_cash > 0 else ["예수금/주문가능금액이 0이거나 조회 실패"],
    }

    if available_cash <= 0:
        global _buy_disabled_reason
        _buy_disabled_reason = "예수금 조회 실패 또는 주문가능금액 0"
        logger.critical(
            "[!] Buying power 0 -- actual balance empty or API field mapping wrong. "
            "No new buys attempted (existing positions sell/forced close still active)."
        )

    # 보유 종목 복원: 재시작 시 RiskManager에 현재 계좌 보유분 등록
    from core.risk_manager import STYLE_LONG as _SL
    try:
        holdings_init = kw.get_holdings() if hasattr(kw, "get_holdings") else []
        for h in holdings_init:
            # 재시작 직후에는 단타/장투 구분 정보가 없으므로 보수적으로 장투로 등록
            # 단타를 장투로 오인 청산하지 않기 위한 안전장치
            rm.add_position(h["ticker"], h["name"] or h["ticker"],
                            h["qty"], h["avg_price"] or h["cur_price"], style=_SL)
        _preflight["holdings_ok"]    = True
        _preflight["holdings_count"] = len(holdings_init)
        _preflight["holdings_list"]  = holdings_init[:10]   # 통합 알림에는 상위 10개만 노출
        if holdings_init:
            logger.info("Restored {} holdings into RiskManager", len(holdings_init))
    except Exception as e:
        logger.warning("Holdings restore failed: {}", e)
        _preflight["errors"].append(f"보유종목 조회 실패: {e}")

    # 재시작 후 DB 의 SENT/PARTIAL_FILLED 행과 broker 실제 상태를 대조해
    # FILLED / UNFILLED 로 보정한다 (broker open-order + holdings 기반).
    try:
        recon = om.reconcile_persisted_orders()
        _preflight["reconcile"] = recon
        if recon.get("checked"):
            logger.info(
                "[startup-reconcile] checked={} filled={} partial={} unfilled={} kept_open={} ambiguous={}",
                recon.get("checked", 0), recon.get("filled", 0), recon.get("partial", 0),
                recon.get("unfilled", 0), recon.get("kept_open", 0), recon.get("ambiguous", 0),
            )
    except Exception as e:
        logger.warning("startup reconcile failed: {}", e)
        _preflight["errors"].append(f"주문 동기화 실패: {e}")

    # 주문가능금액 갱신 helper: 매 스캔마다 호출
    def _refresh_cash():
        nonlocal available_cash
        try:
            dep2 = kw.get_deposit_detail() if hasattr(kw, "get_deposit_detail") else {}
            candidates = [
                dep2.get("ord_alow_amt", 0),
                dep2.get("d2_ord_psbl_amt", 0),
                dep2.get("d2_entra", 0),
                dep2.get("entr", 0),
            ]
            new_cash = max((c for c in candidates if c > 0), default=0)
            if not new_cash:
                bal2 = kw.get_balance()
                out2 = (bal2.get("output2", [{}]) or [{}])[0]
                new_cash = int(out2.get("buying_power", 0) or 0)
            if new_cash and new_cash != available_cash:
                logger.debug("Buying power updated: {:,} -> {:,}", available_cash, new_cash)
                available_cash = new_cash
        except Exception as _e:
            logger.debug("Buying power refresh failed: {}", _e)

    cmd.start_polling(poll_interval=3.0)
    cmd.send_startup_message()

    # ?몃? ?묒냽??cloudflared quick-tunnel URL???≫엳???濡??붾젅洹몃옩???듬낫.
    # cloudflared??start_all.bat ?꾨컲???곕줈 ?꾩썙吏誘濡?硫붿씤 遊뉖낫????쾶 以鍮꾨맖.
    def _notify_cloudflared_urls() -> None:
        import time as _t
        import requests as _r
        ports = [(20241, "Dashboard 5000"), (20242, "Realtime 5001")]
        found: dict[int, str] = {}
        deadline = _t.time() + 120
        while _t.time() < deadline and len(found) < len(ports):
            for port, _label in ports:
                if port in found:
                    continue
                try:
                    resp = _r.get(f"http://127.0.0.1:{port}/quicktunnel", timeout=2)
                    if resp.ok:
                        host = (resp.json() or {}).get("hostname")
                        if host:
                            found[port] = host
                except Exception:
                    pass
            _t.sleep(2)
        if not found:
            return
        lines = ["[?몃? ?묒냽 URL]"]
        for port, label in ports:
            host = found.get(port)
            if host:
                lines.append(f"- {label}: https://{host}")
        try:
            tg.notify_text("\n".join(lines))
            logger.info("[cloudflared] notified telegram with {} url(s)", len(found))
        except Exception as _e:
            logger.warning("[cloudflared] telegram notify failed: {}", _e)

    import threading as _t_threading
    _t_threading.Thread(
        target=_notify_cloudflared_urls,
        daemon=True,
        name="CloudflaredNotify",
    ).start()

    # WebSocket 실시간 시세 백그라운드 supervisor
    # KiwoomWebSocket은 main process 안에서 실행하고, price_cache를 통해 DataCollector가 사용한다.
    # 별도 core\\kiwoom_ws.py 프로세스는 start_all.bat에서 실행하지 않는다.
    import threading, asyncio

    def _run_ws_bg_supervisor():
        """
        Kiwoom WS 諛깃렇?쇱슫??supervisor ??worker 媛 二쎄굅???뺤긽 醫낅즺?대룄 ?먮룞 ?ъ떆??
        - crash ??exponential backoff (5??0??0??0?믠╈넂300s)
        - ?뺤긽 醫낅즺(??留덇컧 ?? ??吏㏐쾶 ?湲????ъ떆??+ backoff reset
        - _running == False 硫?supervisor ??醫낅즺
        """
        backoff = 5
        max_backoff = 300
        while _running:
            crashed = False
            try:
                from core.kiwoom_ws import KiwoomWebSocket
                from stock_universe import get_ticker
                _ws_tickers = []
                for _n in WATCH_LIST:
                    _t = get_ticker(_n)
                    if _t and (_t.endswith(".KS") or _t.endswith(".KQ")):
                        _ws_tickers.append(_t.replace(".KS","").replace(".KQ",""))
                logger.info("[WS supervisor] starting worker -- {} tickers", len(_ws_tickers))
                _ws = KiwoomWebSocket(tickers=_ws_tickers)
                asyncio.run(_ws.run())
                logger.info("[WS supervisor] worker exited normally")
                backoff = 5   # reset on clean exit
            except Exception as _e:
                crashed = True
                logger.error("[WS supervisor] worker crashed: {}", _e)
            if not _running:
                break
            wait = backoff if crashed else 5
            logger.info("[WS supervisor] restart in {}s", wait)
            time.sleep(wait)
            if crashed:
                backoff = min(backoff * 2, max_backoff)

    _ws_thread = threading.Thread(target=_run_ws_bg_supervisor, daemon=True, name="KiwoomWS-Supervisor")
    _ws_thread.start()
    logger.info("WebSocket supervisor thread started")
    _preflight["ws_ok"] = _ws_thread.is_alive()

    # 장전 점검 결과는 텔레그램 1회 요약으로만 전송한다.
    def _send_preflight_summary(p: dict, buy_disabled: "Optional[str]") -> None:
        from config import RISK_CONFIG as _RC, LONG_RISK_CONFIG as _LRC
        lines = ["[장전 점검]"]
        lines.append(f"키움 로그인: {'OK' if p.get('login_ok') else 'FAIL'}")
        if p.get("deposit_ok"):
            lines.append(f"주문가능금액: {p.get('available_cash', 0):,}원")
        else:
            lines.append("주문가능금액: 조회 실패 또는 0원")
        holdings_status = "" if p.get("holdings_ok") else " (조회 실패)"
        lines.append(f"보유종목 복원: {p.get('holdings_count', 0)}개{holdings_status}")
        for h in (p.get("holdings_list") or [])[:5]:
            lines.append(
                f"  - {h.get('name') or h.get('ticker')} {h.get('qty')}주"
                f"@{h.get('avg_price', 0):,.0f} ({h.get('pnl_rate', 0):+.2f}%)"
            )
        lines.append(f"WebSocket: {'시작됨' if p.get('ws_ok') else '미시작/실패'}")
        lines.append(f"REST fallback: {'가능' if hasattr(kw, 'get_holdings') else '제한'}")
        recon = p.get("reconcile") or {}
        if recon.get("checked"):
            lines.append(
                f"주문 동기화: 검토 {recon.get('checked', 0)}건 / "
                f"체결 {recon.get('filled', 0)} / 부분 {recon.get('partial', 0)} / "
                f"미체결 {recon.get('unfilled', 0)} / 잔존 {recon.get('kept_open', 0)}"
                + (f" / 모호 {recon['ambiguous']}" if recon.get("ambiguous") else "")
            )
        lines.append("신규매수: 09:40부터")
        lines.append(f"단타 한도: {int(_RC.get('capital_limit', 0) or 0):,}원")
        lines.append(f"장투 한도: {int(_LRC.get('capital_limit', 0) or 0):,}원")
        if buy_disabled:
            lines.append(f"신규매수 차단 중: {buy_disabled}")
        if p.get("errors"):
            lines.append("오류: " + " / ".join(p["errors"][:3]))
        try:
            tg.notify_text("\n".join(lines))
        except Exception as _e:
            logger.warning("preflight summary notify failed: {}", _e)
    _send_preflight_summary(_preflight, _buy_disabled_reason)
    # ????????????????????????????????????????????????????????????????????????????????????????????????????????????????

    # 疫꿸퀡?????뵝 ?源낆쨯
    from stock_universe import resolve as _resolve
    for name in WATCH_LIST[:3]:
        _t, _ = _resolve(name)
        am.add_volume_alert(_t, _t, multiplier=3.5)

    logger.info("Dashboard: python dashboard/realtime_app.py -- http://localhost:5001/advanced")

    # ???? 筌롫뗄???룐뫂遊?????????????????????????????????????????????????????????
    # ?쒖꽭 freshness 泥댄겕 ??WATCH_LIST ??泥?醫낅ぉ?쇰줈 snapshot ?쒕룄?댁꽌 媛깆떊 ?쒓컖 異붿쟻.
    # stale (DATA_STALE_SEC ?댁긽 誘멸갚?? ???좉퇋留ㅼ닔 李⑤떒 + 1??寃쎄퀬. ?뺤긽 蹂듦? ??1???뚮┝.
    def _check_data_freshness() -> None:
        global _last_data_ok_at, _stale_notified, _buy_disabled_reason
        if WATCH_LIST:
            try:
                dc.get_snapshot(WATCH_LIST[0])
                _last_data_ok_at = time.time()
            except Exception:
                pass
        if _last_data_ok_at <= 0:
            return  # ?꾩쭅 ??踰덈룄 紐?諛쏆쓬 (?? ?μ쟾) ??stale ?먯젙 蹂대쪟
        age = time.time() - _last_data_ok_at
        if age > DATA_STALE_SEC:
            if not _stale_notified:
                try:
                    tg.notify_text(
                        f"?좑툘 ?쒖꽭 stale {age:.0f}珥?(>{DATA_STALE_SEC}s) ???좉퇋留ㅼ닔 李⑤떒, "
                        f"REST fallback ?쒕룄"
                    )
                except Exception:
                    pass
                _stale_notified = True
            # ?ㅻⅨ ?ъ쑀濡??대? 李⑤떒?섏뼱 ?덉쑝硫???뼱?곗? ?딆쓬. stale ?ъ쑀硫?媛깆떊留?
            if (not _buy_disabled_reason) or ("stale" in _buy_disabled_reason):
                _buy_disabled_reason = f"?쒖꽭 stale {age:.0f}s"
        else:
            if _stale_notified:
                try:
                    tg.notify_text("???쒖꽭 ?섏떊 ?뺤긽?????좉퇋留ㅼ닔 ?ш컻")
                except Exception:
                    pass
                _stale_notified = False
                if _buy_disabled_reason and "stale" in _buy_disabled_reason:
                    _buy_disabled_reason = None

    while _running:
        now = datetime.now()
        _check_data_freshness()

        # 미체결 주문 동기화: broker holdings와 pending 주문을 대조해 체결/취소 상태를 반영한다.
        try:
            for _r in om.reconcile_pending():
                if _r.filled and _r.action == "BUY":
                    tg.notify_text(
                        f"[fill/매수] {_r.ticker} | {_r.style or '-'} {_r.filled_qty}/{_r.qty}주 @{_r.price:,.0f}"
                    )
                elif _r.filled and _r.action == "SELL":
                    tg.notify_text(
                        f"[fill/매도] {_r.ticker} | {_r.style or '-'} {_r.filled_qty}/{_r.qty}주 @{_r.price:,.0f} "
                        f"| 손익:{(_r.pnl or 0):+,.0f}원"
                    )
                elif _r.action == "PARTIAL":
                    pnl_part = f" | 누적손익:{(_r.pnl or 0):+,.0f}원" if _r.pnl is not None else ""
                    tg.notify_text(
                        f"[부분체결 timeout] {_r.ticker} | {_r.style or '-'} "
                        f"{_r.filled_qty}/{_r.qty}주 @{_r.price:,.0f} (broker 잔여 취소){pnl_part}"
                    )
                elif _r.action == "UNFILLED":
                    tg.notify_text(
                        f"[미체결] {_r.ticker} | {_r.style or '-'} {_r.qty}주 @{_r.price:,.0f} "
                        f"(timeout, broker 취소 성공)"
                    )
                elif _r.action == "CANCEL_FAILED":
                    tg.notify_text(
                        f"[broker 취소 실패] {_r.ticker} | {_r.style or '-'} "
                        f"{_r.filled_qty}/{_r.qty}주 - broker 미체결 확인 필요\n"
                        f"사유: {_r.reason}"
                    )
                elif _r.action == "MEMORY_CLEARED":
                    tg.notify_text(
                        f"[메모리만 정리] {_r.ticker} | {_r.style or '-'} "
                        f"{_r.filled_qty}/{_r.qty}주 - broker 미체결 확인 필요\n"
                        f"사유: {_r.reason}"
                    )
        except Exception as _e:
            logger.warning("reconcile_pending failed: {}", _e)

        # ???わ㎗?꾧쾿 (1??볦퍢)
        if time.time() - health_checked >= 3600:
            status = hm.check()
            hm.ping_scan()
            if not status.is_healthy:
                hm.try_recover(status)
            health_checked = time.time()

        # ??? 揶쏅벡??筌?沅?(15:20~15:29) ???館???????? ??뽰뇚
        if is_force_close_window():
            from core.risk_manager import STYLE_DAY
            day_positions = rm.get_positions_by_style(STYLE_DAY)
            if day_positions:
                logger.warning("[FORCE CLOSE] starting ({} positions)", len(day_positions))
                tg.notify_text(f"[FORCE CLOSE]\n{len(day_positions)} positions full-volume sell (longterm excluded)")
                from core.ai_judge import AIVerdict
                for ticker in list(day_positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                                      reason="end-of-day forced close", target_price=snap.current_price,
                                      stop_loss=snap.current_price, position_size="SMALL")
                        om.execute(v, snap.current_price)
                        tg.notify_verdict(v, snap.current_price)
                        tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "force_close")
                    except Exception as e:
                        logger.error("[{}] forced close failed: {}", ticker, e)
            time.sleep(60)
            continue

        # ???? ??볦퍢?紐꾩쪒揶쎛 筌?沅?(15:40~16:00) ??????????????????
        # ?類?뇣?關肉??筌???륁궔 ?????륁뱽 ?ル굛? ??μ뵬揶쎛嚥??癒?짗 筌?沅? ?醫됲뇣 筌띲끉????곸벉.
        if is_after_hours_close():
            from core.ai_judge import AIVerdict as _AV
            positions = rm.get_positions()
            sold = 0
            logger.info("after-hours close mode ({}~16:00) | positions {} | cash {:,}",
                        now.strftime("%H:%M"), len(positions), available_cash)
            if not positions:
                logger.info("   |- nothing to close -- 0 positions in memory "
                            "(possible balance restore not done after restart)")
            else:
                for ticker in list(positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        # ?癒?쟿夷???쟿 筌ｋ똾寃?(??볦퍢?紐꾨퓠??뺣즲 鈺곌퀗援??겸뫗????筌?沅?
                        if (rm.check_stop_loss(ticker, snap.current_price)
                                or rm.check_take_profit(ticker, snap.current_price)):
                            v = _AV(ticker=ticker, action="SELL", confidence=100,
                                    reason="after-hours close", target_price=snap.current_price,
                                    stop_loss=snap.current_price, position_size="SMALL")
                            # hoga="81" ????? trde_tp=81 (??볦퍢?紐꾩쪒揶쎛 ??μ뵬揶쎛)
                            om.execute(v, snap.current_price, hoga="81")
                            tg.notify_verdict(v, snap.current_price)
                            tracker.record_signal("system", ticker, "SELL", 100,
                                                  snap.current_price, True, "after_hours_close")
                            sold += 1
                    except Exception as e:
                        logger.error("[{}] after-hours close failed: {}", ticker, e)
                logger.info("after-hours close attempted {} / positions {}", sold, len(positions))
            time.sleep(60)
            continue

        # ???? ??볦퍢?紐껊뼊??? 筌?沅?(16:00~18:00) ??????????????????
        # 10?브쑬彛????μ뵬揶쎛 筌ｋ떯猿? 吏?0% 揶쎛野???쀫립. 筌?沅??袁⑹뒠, ?醫됲뇣 筌띲끉??疫뀀뜆?.
        if is_after_hours_single():
            from core.ai_judge import AIVerdict as _AV2
            positions = rm.get_positions()
            if positions:
                logger.info("[after-hours single-price mode] ({}~18:00) | holdings {} | close only",
                            now.strftime("%H:%M"), len(positions))
                sold = 0
                for ticker in list(positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        # ??볦퍢?紐껊뼊?????吏?0% ??쀫립 ???癒?쟿夷???쟿 域밸챶?嚥??怨몄뒠
                        if (rm.check_stop_loss(ticker, snap.current_price)
                                or rm.check_take_profit(ticker, snap.current_price)):
                            v = _AV2(ticker=ticker, action="SELL", confidence=100,
                                     reason="after-hours single-price close", target_price=snap.current_price,
                                     stop_loss=snap.current_price, position_size="SMALL")
                            # hoga="62" ????? trde_tp=62 (??볦퍢?紐껊뼊???). 筌왖?類?嚥??袁⑹삺揶쎛 癰귣?源?
                            om.execute(v, snap.current_price, hoga="62")
                            tg.notify_verdict(v, snap.current_price)
                            tracker.record_signal("system", ticker, "SELL", 100,
                                                  snap.current_price, True, "after_hours_single_price_close")
                            sold += 1
                    except Exception as e:
                        logger.error("[{}] after-hours single-price close failed: {}", ticker, e)
                logger.info("single-price close attempted {} / positions {}", sold, len(positions))
            time.sleep(120)   # connection in 10-min units -- no need for finer sleep
            continue

        # ??筌띾뜃而?筌ｌ꼶??
        if is_close_window() and not daily_reported:
            logger.info("[end-of-day handling started]")
            holdings = pm.get_holdings()
            stats    = pm.get_portfolio_stats(holdings)
            pm.save_snapshot(stats, holdings)
            pm.print_holdings(holdings, stats)

            # ?源껊궢 域뮤???브쑴苑?
            attr = attrib.analyze()
            attrib.print_report(attr)
            attrib.save_html(attr)

            # ?귐뗫７??
            try:
                tune_result = tuner.tune(force=True)
                tg.notify_text(
                    "Adaptive tuning complete\n"
                    f"Sample: {tune_result.trades} trades | Winrate: {tune_result.winrate:.1%}\n"
                    f"Avg net: {tune_result.avg_net_pnl_pct:+.2f}% | PF: {tune_result.profit_factor:.2f}\n"
                    f"min_conf: {tune_result.min_confidence} | R:R: {tune_result.min_effective_rr:.2f}"
                )
            except Exception as e:
                logger.warning("End-of-day adaptive tuning failed: {}", e)

            rg.generate_daily_report()
            rg.generate_html_daily()
            if now.weekday() == 4:
                rg.generate_weekly_report()

            # ?袁⑥셽 ?귐됰쐭癰귣?諭?
            tracker.print_leaderboard()

            # DB ?類ｂ봺 (90????곴맒)
            deleted = db_mgr.cleanup(retain_days=90)
            if deleted:
                logger.info("DB cleanup: {}", deleted)

            daily_reported = True
            time.sleep(120)
            continue

        if not is_close_window():
            daily_reported = False

        # ?? 24??볦퍢 ??沅???곸뇚 70??筌뤴뫀??怨뺤춦 (????곸뇚 ?얜떯?, ??덉읅 雅뚯눊由? ??
        active_session = is_market() or is_us_market_session()
        hot_alert_interval = HOT_INTERVAL_ACTIVE if active_session else HOT_INTERVAL_QUIET
        if time.time() - last_hot_scan >= hot_alert_interval:
            last_hot_scan = time.time()
            hm.ping_scan()   # treated as activity signal per scan (avoid false-alarm)
            today_str = now.strftime("%Y-%m-%d")
            if _alert_date != today_str:
                _alerted_today.clear()
                _alert_date = today_str

            from config import get_foreign_watch_names as _get_fw
            _fw_names = _get_fw()
            _full_universe = list(WATCH_LIST) + _fw_names
            _sess = []
            if is_market(): _sess.append("KR")
            if is_us_market_session(): _sess.append("US")
            if not _sess: _sess.append("OFF")
            logger.info("70+ monitor ({}) | KR {} + foreign {} | {} | next {} min",
                        now.strftime("%H:%M"), len(WATCH_LIST), len(_fw_names),
                        ",".join(_sess), hot_alert_interval // 60)

            # ???? 野꺜筌? 疫꿸퀣??60+ ??????野껊슣?????疫꿸퀣??70+ 筌?AI ?紐꾪뀱 (top 30) ?????? 70+ 筌?????
            hot_result = screener.run(
                universe=_full_universe, use_mock=False,
                min_score=60.0,
                ai_top_n=30,           # AI call upper limit (tech>=70 pre-filter)
                composite_min=70.0,
            )
            hot_candidates = [c for c in hot_result.candidates if c.score >= 70]
            if hot_candidates:
                tg.notify_hot_candidates(
                    hot_candidates,
                    title=f"70+ candidates | {','.join(_sess)} | {now.strftime('%H:%M')}",
                )
                for _hc in hot_candidates:
                    _alerted_today.add(_hc.ticker)
                    logger.info("70+ candidate: {} | {:.0f}", _hc.ticker, _hc.score)

        if not is_market():
            logger.debug("Out of trading hours ({})", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        if rm.is_halted():
            logger.warning("[trade halted]")
            tg.notify_halt(rm.get_daily_pnl())
            time.sleep(interval)
            continue

        scan_count += 1
        _refresh_cash()  # refresh kiwoom balance every scan -> realtime buying power
        logger.info("--- scan #{} | {} | buying_power {:,} KRW ---",
                    scan_count, now.strftime("%H:%M:%S"), available_cash)
        logger.info(
            "capital buckets | day left {:,}/{:,} | long left {:,}/{:,}",
            _style_cash(STYLE_DAY), int(RISK_CONFIG.get("capital_limit", 0) or 0),
            _style_cash(STYLE_LONG), int(LONG_RISK_CONFIG.get("capital_limit", 0) or 0),
        )
        hm.ping_scan()

        # ????뽰삂 筌???쇳떔: ??쎄쾿?귐됯섐 ??쎈뻬
        if scan_count == 1 or now.strftime("%H:%M") == "09:05":
            logger.info("Running stock screener...")
            scr_result = screener.run(
                universe=WATCH_LIST, use_mock=False, min_score=10.0
            )
            opening_hot = [c for c in scr_result.candidates if c.score >= 70]
            if opening_hot:
                tg.notify_hot_candidates(opening_hot, title="opening 70+ candidates")
            for c in scr_result.candidates:
                if c.score >= 70:
                    _alerted_today.add(c.ticker)

        # ?? ??????癒?쟿夷???쟿 筌ｋ똾寃???
        for ticker, pos in list(rm.get_positions().items()):
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("[{}] data fetch failed: {}", ticker, e); continue

            # ???뵝 筌ｋ똾寃?
            am.check(snap)

            from core.ai_judge import AIVerdict
            if rm.check_stop_loss(ticker, snap.current_price):
                # ???? ??? ?얠눖?????館???袁れ넎 ?癒?뼊 ????????????????
                pos = rm.get_positions().get(ticker)
                _convert = False
                if pos and pos.style == "daytrading" and RISK_CONFIG.get("convert_to_long_enabled", True):
                    # ?袁れ넎 鈺곌퀗援? MA120 ??+ ATR_pct ?臾믪벉 + AI ?醫듚??Hurdle
                    cond_ma120 = (not RISK_CONFIG.get("convert_require_ma120", True)) or \
                                 (snap.ma120 > 0 and snap.current_price > snap.ma120)
                    cond_atr = snap.atr_pct < RISK_CONFIG.get("convert_max_atr_pct", 3.0)
                    if cond_ma120 and cond_atr:
                        rm.convert_to_long(ticker,
                            reason=f"daytrade stop-loss zone but above MA120 (+ATR {snap.atr_pct:.1f}%) -> convert to longterm")
                        tg.notify_text(
                            f"[FLIP] daytrade -> longterm: {ticker}\n"
                            f"entry {pos.avg_price:,.0f} -> current {snap.current_price:,.0f}\n"
                            f"MA120 {snap.ma120:,.0f} 夷?ATR {snap.atr_pct:.2f}%"
                        )
                        tracker.record_signal("system", ticker, "CONVERT", 100,
                                              snap.current_price, True, "convert_to_long")
                        _convert = True

                if not _convert:
                    v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                                  reason="stop-loss hit", target_price=snap.current_price,
                                  stop_loss=snap.current_price, position_size="SMALL")
                    om.execute(v, snap.current_price)
                    tg.notify_verdict(v, snap.current_price)
                    tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "stop_loss")

            elif rm.check_take_profit(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="take-profit hit", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)
                tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "take_profit")

        # ?? ??? ?醫됲뇣 筌욊쑴????쇳떔 ??
        from core.risk_manager import STYLE_DAY, STYLE_LONG
        from core.ai_judge import AIVerdict as AV
        from config import fmt_price as _fmt_price

        def _execute_entry(snap, verdict, active_strategy, style):
            """common entry execution helper"""
            # buy_disabled_reason 寃뚯씠?????덉닔湲?0/?쒖꽭 stale ?깆뿉?쒕뒗 ?좉퇋 留ㅼ닔留?李⑤떒.
            # 留ㅻ룄(?먯젅/?듭젅/媛뺤젣泥?궛)????寃뚯씠?몄? 臾닿??섍쾶 蹂??⑥닔 ?몃??먯꽌 怨꾩냽 ?숈옉.
            if _buy_disabled_reason:
                logger.info("[entry blocked] {} | reason: {}", snap.ticker, _buy_disabled_reason)
                return
            style_cash = _style_cash(style)
            if style_cash <= 0:
                logger.info("[{}] budget exhausted: {}", style, snap.ticker)
                return
            sizing = ps.calc(snap, verdict.confidence, style_cash, style=style)
            if not sizing.is_valid:
                logger.debug("Position sizing -- qty 0 [{}]", snap.ticker)
                return
            basic_v = AV(
                ticker=snap.ticker,
                action=verdict.action,
                confidence=verdict.confidence,
                reason=f"[{style}][{active_strategy.name}][news:{verdict.news_judgment}] {verdict.reason}",
                target_price=verdict.target_price,
                stop_loss=sizing.stop_loss,
                position_size=active_strategy.name.upper()[:5],
            )
            result = om.execute(basic_v, snap.current_price, style_cash, style=style, atr=snap.atr)
            # 차단/오류는 매수 알림을 보내지 않고 사유만 짧게 통보한다.
            if not result.ok:
                if result.action in ("BLOCKED", "ERROR"):
                    tg.notify_text(
                        f"[entry/거부] {snap.ticker} | {style}/{active_strategy.name}\n"
                        f"사유: {result.reason}"
                    )
                return
            from config import fmt_price as _fmt_price
            key_points = getattr(verdict, "news_key_points", []) or []
            news_line = ""
            if key_points:
                news_line = " | " + " / ".join(str(p)[:45] for p in key_points[:2])
            # 실제 주문 수량과 체결 수량은 OrderResult 기준으로 표시한다.
            if result.filled:
                status_label = "체결"
                qty_text     = f"qty {result.qty}"
            elif result.is_partial:
                status_label = "부분체결"
                qty_text     = f"qty {result.filled_qty}/{result.qty}"
            else:
                status_label = "접수대기"
                qty_text     = f"qty {result.qty}"
            tg.notify_text(
                f"[entry/{status_label}] {snap.ticker} {verdict.confidence:.0f} | {style}/{active_strategy.name}\n"
                f"price {_fmt_price(snap.ticker, snap.current_price)} | {qty_text} | Kelly {sizing.kelly_fraction:.1%}\n"
                f"bucket cash {style_cash:,}\n"
                f"stop {_fmt_price(snap.ticker, sizing.stop_loss)} ({sizing.stop_loss_pct:.1f}%)\n"
                f"news {verdict.news_judgment}({verdict.news_score:+d}){news_line}\n"
                f"{str(verdict.reason)[:160]}"
            )

        # ?? ??? ?醫됲뇣 筌욊쑴?? ??볦퍢 ?袁り숲 ??
        _entry_start = dtime(*map(int, RISK_CONFIG.get("entry_start", "09:40").split(":")))
        _entry_end   = dtime(*map(int, RISK_CONFIG.get("entry_end",   "14:30").split(":")))
        _in_entry_window = _entry_start <= now.time() <= _entry_end

        if not _in_entry_window:
            logger.debug("[daytrade] entry window over ({}) -- no new buys", now.strftime("%H:%M"))

        if _in_entry_window:
            # ?? KOSPI 獄쎻뫚堉??袁り숲 ??
            _kospi_ok = True
            try:
                import yfinance as _yf
                _ki = _yf.Ticker("^KS11").fast_info
                _kospi_chg = (_ki.last_price - _ki.previous_close) / _ki.previous_close
                if _kospi_chg < RISK_CONFIG.get("kospi_min_change", -0.01):
                    _kospi_ok = False
                    logger.warning("[daytrade] KOSPI down({:.2%}) -- new buys halted", _kospi_chg)
            except Exception:
                pass

        if _in_entry_window and _kospi_ok:
            # ??? 筌욊쑴????쇳떔: ?怨쀪퐨??뽰맄 ?ル굝????30揶?筌?筌띲끇????쥓?ㅵ칰?筌ｋ똾寃?
            # (?袁⑷퍥 90?ル굝??? 70????筌뤴뫀??怨뺤춦??10~30?브쑬彛???怨뺤쨮 ?뚣끇苡?
            for name in WATCH_LIST_PRIORITY:
                if not _running or name in rm.get_positions():
                    continue
                try:
                    snap = dc.get_snapshot(name)
                except Exception as e:
                    logger.error("[daytrade][{}] data fetch failed: {}", name, e); continue

                from stock_universe import is_domestic
                if not is_domestic(snap.ticker):
                    continue

                # ???? 120??깃퐨 ?袁り숲: ?袁⑹삺揶쎛 > MA120 ??곷선??筌욊쑴????
                if snap.ma120 > 0 and snap.current_price <= snap.ma120:
                    logger.debug("[daytrade][{}] below 120MA -- entry blocked (current:{:,.0f} <= MA120:{:,.0f})",
                                 snap.ticker, snap.current_price, snap.ma120)
                    continue

                am.check(snap)

                # ?袁⑥셽 ??쇱㉦ ?類ㅼ뵥 ??min_strategies 揶???곴맒 ???궢??곷튊 筌욊쑴??
                _min_st = RISK_CONFIG.get("min_strategies", 2)
                _passed = [s for s in strategies if s.should_enter(snap)]
                if len(_passed) < _min_st:
                    continue
                active_strategy = _passed[0]

                # ???? ???遺얠컲??野껊슣???(?臾믪읈雅뚯뀑猷뱀읅?癒?┛???? ????
                fund = fund_gate.check(snap.ticker)
                if not fund.passed:
                    logger.info("[daytrade][{}] fundamental block: {}",
                                snap.ticker, " 夷?".join(fund.reasons))
                    tracker.record_signal(active_strategy.name, snap.ticker, "BLOCK_FUND",
                                          0, snap.current_price, False,
                                          " 夷?".join(fund.reasons))
                    continue

                verdict = int_judge.judge(snap, fetch_news=True)
                verdict.ticker = snap.ticker
                tracker.record_signal(active_strategy.name, snap.ticker, verdict.action,
                                      verdict.confidence, snap.current_price,
                                      verdict.is_executable, verdict.reason)
                strat_names = "+".join(s.name for s in _passed)
                logger.info("[daytrade] {} strategies({}) | {}", len(_passed), strat_names, verdict.summary_line)

                if verdict.news_blocked:
                    logger.info("[day] news block: {} | {}({:+d}) | {}",
                                snap.ticker, verdict.news_judgment,
                                verdict.news_score, verdict.news_reason[:80])
                    continue
                if not verdict.is_executable:
                    continue

                _execute_entry(snap, verdict, active_strategy, STYLE_DAY)

        # ?? ?館???醫됲뇣 筌욊쑴????쇳떔 (30?브쑬彛?? ??
        if time.time() - last_long_scan >= long_interval:
            last_long_scan = time.time()
            long_scan_count += 1
            logger.info("--- longterm scan #{} | {} ---", long_scan_count, now.strftime("%H:%M:%S"))

            for name in WATCH_LIST_LONG:
                if not _running or name in rm.get_positions():
                    continue
                try:
                    snap = dc.get_snapshot(name)
                except Exception as e:
                    logger.error("[longterm][{}] data fetch failed: {}", name, e); continue

                # ??? OpenAPI????곸뇚雅뚯눘??雅뚯눖揆 沃섎챷??????館??筌욊쑴???癒?퍥????쎄땁
                from stock_universe import is_domestic
                if not is_domestic(snap.ticker):
                    logger.debug("[longterm][{}] foreign stock -- kiwoom order unsupported, skipping entry", snap.ticker)
                    continue

                am.check(snap)
                # ?館????醫듚??疫꿸퀣??????앲첋?嚥??袁⑥셽 ?袁り숲 ??곸뵠 AI 筌욊낯???癒?뼊
                verdict = int_judge.judge(snap, fetch_news=True)
                verdict.ticker = snap.ticker

                if verdict.confidence < LONG_RISK_CONFIG["min_confidence"]:
                    continue
                if verdict.news_blocked:
                    logger.info("[long] news block: {} | {}({:+d})",
                                snap.ticker, verdict.news_judgment, verdict.news_score)
                    continue
                if not verdict.is_executable:
                    continue

                # ?館???筌띲끉??揶쎛????? 筌ｋ똾寃?
                long_cash = _style_cash(STYLE_LONG)
                check = rm.check_buy(snap.ticker, snap.current_price,
                                     verdict.confidence, long_cash, style=STYLE_LONG)
                if not check.allowed:
                    logger.debug("[longterm] qty blocked: {}", check.reason)
                    continue

                tracker.record_signal("longterm", snap.ticker, verdict.action,
                                      verdict.confidence, snap.current_price,
                                      verdict.is_executable, verdict.reason)
                logger.info("[longterm] {} | {}", snap.ticker, verdict.summary_line)

                class _LongStrategy:
                    name = "longterm"
                    def should_enter(self, _): return True

                _execute_entry(snap, verdict, _LongStrategy(), STYLE_LONG)

        logger.info("scan #{} complete | sleep {} sec", scan_count, interval)
        time.sleep(interval)

    # 종료 처리
    logger.info("Termination in progress...")
    cmd.stop()
    _cancelled = om.cancel_all_pending()
    if _cancelled:
        _n_ok      = sum(1 for r in _cancelled if r.action == "CANCELLED" and not r.is_partial)
        _n_partial = sum(1 for r in _cancelled if r.action == "CANCELLED" and r.is_partial)
        _n_filled  = sum(1 for r in _cancelled if r.action in ("FILLED", "PARTIAL"))
        _n_failed  = sum(1 for r in _cancelled if r.action == "CANCEL_FAILED")
        _n_mem     = sum(1 for r in _cancelled if r.action == "MEMORY_CLEARED")
        _lines = [f"[종료/취소] 미체결 {_cancelled.__len__()}건 정리"]
        if _n_ok:      _lines.append(f"  - broker 취소 성공: {_n_ok}건")
        if _n_partial: _lines.append(f"  - 부분체결 잔여 취소: {_n_partial}건")
        if _n_filled:  _lines.append(f"  - 정리 시점 이미 체결: {_n_filled}건")
        if _n_failed:  _lines.append(f"  - broker 취소 실패: {_n_failed}건, 수동 확인 필요")
        if _n_mem:     _lines.append(f"  - 메모리만 정리: {_n_mem}건, broker 잔여 가능")
        tg.notify_text("\n".join(_lines))
    kw.disconnect()

    # 최종 포트폴리오/성과 출력
    holdings = pm.get_holdings()
    stats    = pm.get_portfolio_stats(holdings)
    pm.print_holdings(holdings, stats)
    tracker.print_leaderboard()

    _day_stats = rm.get_day_trade_stats()
    tg.notify_text(
        f"auto trading stopped\n"
        f"daily pnl: {rm.get_daily_pnl():+,.0f} "
        f"(?⑦? {rm.get_daily_pnl(STYLE_DAY):+,.0f} / ?ν닾 {rm.get_daily_pnl(STYLE_LONG):+,.0f})\n"
        f"day-trades: {_day_stats['count']}嫄?"
        f"(??{_day_stats['wins']} / ??{_day_stats['losses']} / ?밸쪧 {_day_stats['winrate']:.1%})\n"
        f"total scans: {scan_count}"
    )
    logger.info("normal shutdown complete")


if __name__ == "__main__":
    main()
