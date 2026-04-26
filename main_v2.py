"""
main_v2.py ??AI ?먮룞留ㅻℓ ?쒖뒪???꾩쟾 ?듯빀 踰꾩쟾

?ы븿 紐⑤뱢 ?꾩껜:
  ??DB 留덉씠洹몃젅?댁뀡 (?쒖옉 ???먮룞)
  ???ъ뒪紐⑤땲??(留??쒓컙 泥댄겕)
  ??醫낅ぉ ?ㅽ겕由щ꼫 (???쒖옉 09:05 ?먮룞 ?ㅽ뻾)
  ???댁뒪 ?몄옱/?낆옱 遺꾩꽍 (留??ㅼ틪)
  ??硫????꾪봽?덉엫 + ?듯빀 AI ?먮떒
  ??Kelly Criterion ?ъ????ъ씠吏?
  ???뱁꽣 濡쒗뀒?댁뀡 ?꾨왂
  ??媛寃㈑룹“嫄??뚮┝ ?쒖뒪??
  ???ы듃?대━??VaR쨌CVaR
  ???먮룞 ?쇱씪쨌二쇨컙 由ы룷??
  ???붾젅洹몃옩 ?묐갑??紐낅졊
  ???꾨왂 ?깃낵 異붿쟻
  ???깃낵 洹??遺꾩꽍 (??留덇컧 ??

?ㅽ뻾:
    python main_v2.py
"""

from __future__ import annotations

import signal
import sys
import time

# Windows ?곕???UTF-8 媛뺤젣 ?ㅼ젙 (?대え吏/?쒓? 異쒕젰)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, time as dtime

from loguru import logger

from config import (LOG_DIR, LONG_RISK_CONFIG, RISK_CONFIG,
                    SCHEDULE_CONFIG, WATCH_LIST, WATCH_LIST_LONG,
                    WATCH_LIST_PRIORITY)

# ?? 濡쒓퉭 ?????????????????????????????????????
logger.remove()
# 1) 肄섏넄: INFO ?댁긽 而щ윭
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
# 2) 醫낇빀 濡쒓렇 (DEBUG ?꾩껜) ??90??蹂닿?
logger.add(LOG_DIR / "trade_{time:YYYYMMDD}.log",
           level="DEBUG", rotation="1 day", retention="90 days", encoding="utf-8")
# 3) ?먮윭 ?꾩슜 濡쒓렇 (ERROR/CRITICAL留? ??180??蹂닿?, ?몃윭釉붿뒋?낆슜
logger.add(LOG_DIR / "errors_{time:YYYYMMDD}.log",
           level="ERROR", rotation="1 day", retention="180 days", encoding="utf-8",
           format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}")
# 4) 嫄곕옒 ?꾩슜 濡쒓렇 (留ㅼ닔/留ㅻ룄/泥?궛/?꾪솚/泥닿껐/?먯젅쨌?듭젅留? ??365??蹂닿?
_TRADE_KEYWORDS = (
    "BUY", "SELL", "ORDER", "FILLED", "STOP", "TAKE", "FORCE_CLOSE",
    "CONVERT", "POSITION", "BLOCKED", "AI",
)
logger.add(LOG_DIR / "trades_{time:YYYYMMDD}.log",
           level="INFO", rotation="1 day", retention="365 days", encoding="utf-8",
           filter=lambda r: any(k in r["message"] for k in _TRADE_KEYWORDS),
           format="{time:HH:mm:ss} | {level:<7} | {message}")

# ?? 醫낅즺 泥섎━ ?????????????????????????????????
_running = True
def _sig(s, f):
    global _running; logger.warning("醫낅즺 ?좏샇"); _running = False
signal.signal(signal.SIGINT,  _sig)
signal.signal(signal.SIGTERM, _sig)

# ?? ?쒖옣 ?쒓컙 ?????????????????????????????????
def is_market(t=None):
    t = t or datetime.now().time()
    o = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    c = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    return o <= t <= c

def is_close_window():
    t = datetime.now().time()
    return dtime(15,30) <= t <= dtime(15,36)

def is_force_close_window():
    """?⑦? 媛뺤젣 泥?궛 援ш컙 (force_close_time ~ 15:29). 湲곕낯 15:10."""
    t = datetime.now().time()
    fc = RISK_CONFIG.get("force_close_time", "15:10").split(":")
    fc_time = dtime(int(fc[0]), int(fc[1]))
    return fc_time <= t < dtime(15, 30)


def is_after_hours_close():
    """?쒓컙?몄쥌媛 留ㅻℓ 媛??援ш컙 (15:40~16:00). ?좉퇋 留ㅼ닔 X, 留ㅻ룄/泥?궛留?"""
    t = datetime.now().time()
    return dtime(15, 40) <= t <= dtime(16, 0)


def is_after_hours_single():
    """?쒓컙?몃떒?쇨? 留ㅻℓ 媛??援ш컙 (16:00~18:00). 泥?궛 ?꾩슜."""
    t = datetime.now().time()
    return dtime(16, 0) < t <= dtime(18, 0)


def is_us_market_session():
    """
    NYSE/NASDAQ ?뺢퇋??(?쒓뎅?쒓컙).
    ?쒖???EST): 23:30~06:00 KST
    ?쒕㉧???EDT): 22:30~05:00 KST
    DST ?뺥솗 ?먮퀎 ?대젮?곕?濡?22:30~06:00 愿묐쾾???덈룄??
    """
    t = datetime.now().time()
    return t >= dtime(22, 30) or t < dtime(6, 0)


def main():
    logger.info("="*65)
    logger.info("  ?쨼 AI ?먮룞留ㅻℓ v2 ???ㅼ쟾?ъ옄")
    logger.info("  醫낅ぉ:{}媛?| {}", len(WATCH_LIST),
                datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("="*65)

    logger.critical("?좑툘  ?ㅺ굅??10珥????쒖옉")
    for i in range(10, 0, -1): logger.critical("  {}珥?..", i); time.sleep(1)

    # ?? 1. DB 珥덇린???????????????????????????
    from core.db_manager import init_db
    db_mgr = init_db()

    # ?? 2. 而댄룷?뚰듃 珥덇린?????????????????????
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
    # ?ㅽ겕由щ꼫????붾찘??AI 二쇱엯 ???듯빀 ?먯닔 ?곗텧
    screener   = MarketScreener(dc, fundamental_gate=fund_gate, integrated_judge=int_judge)
    tracker    = StrategyTracker()
    attrib     = PerformanceAttributor()
    tg         = TelegramBot()
    cmd        = TelegramCommander(rm, rg, om)
    tuner      = AdaptiveTuner()

    try:
        tune_result = tuner.tune(force=False)
        logger.info(
            "?곸쓳???쒕떇 ?곸슜 | ?쒕낯:{} ?밸쪧:{:.1%} ?쒗룊洹?{:+.2f}% PF:{:.2f} | min_conf:{} R:R:{:.2f}",
            tune_result.trades, tune_result.winrate, tune_result.avg_net_pnl_pct,
            tune_result.profit_factor, tune_result.min_confidence,
            tune_result.min_effective_rr,
        )
    except Exception as e:
        logger.warning("?곸쓳???쒕떇 ?곸슜 ?ㅽ뙣: {}", e)

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
    interval            = SCHEDULE_CONFIG["scan_interval_minutes"] * 60   # ?⑦?: 10遺?
    long_interval       = 30 * 60                                          # ?ν닾: 30遺?
    last_long_scan      = 0.0   # ?ν닾 留덉?留??ㅼ틪 ?쒓컖
    last_hot_scan       = 0.0   # 70???뚮┝ 留덉?留??ㅼ틪 ?쒓컖
    # ?숈쟻 二쇨린: ?쒓뎅 ?뺢퇋??誘멸뎅 ?뺢퇋?μ씠硫?10遺? 洹???30遺?
    HOT_INTERVAL_ACTIVE = 10 * 60
    HOT_INTERVAL_QUIET  = 30 * 60
    _alerted_today: set[str] = set()                                       # ?뱀씪 ?대? ?뚮┝ 蹂대궦 醫낅ぉ
    _alert_date: str    = ""                                               # ?뚮┝ 紐⑸줉 ?좎쭨 異붿쟻

    def _style_invested(style: str) -> float:
        return sum(p.invested_amount for p in rm.get_positions().values() if p.style == style)

    def _style_cash(style: str) -> int:
        cfg = RISK_CONFIG if style == "daytrading" else LONG_RISK_CONFIG
        limit = int(cfg.get("capital_limit", 0) or 0)
        invested = _style_invested(style)
        budget_left = max(0.0, limit - invested) if limit > 0 else float(available_cash)
        return int(max(0.0, min(float(available_cash), budget_left)))

    # 濡쒓렇??+ ?붽퀬 議고쉶
    try:
        kw.login()
    except Exception as e:
        logger.critical("濡쒓렇???ㅽ뙣: {}", e); sys.exit(1)

    # ?붽퀬 議고쉶 (?ㅺ굅??紐⑥쓽?ъ옄 紐⑤몢) ??kt00001(?덉닔湲덉긽?? ?곗꽑, ?ㅽ뙣 ??kt00004
    available_cash = 0
    try:
        deposit = kw.get_deposit_detail() if hasattr(kw, "get_deposit_detail") else {}
        # kt00001 ??二쇰Ц媛?κ툑??愿???꾨낫 ?꾨뱶 以?0 ?꾨땶 理쒕뙎媛믪쓣 ?ъ슜
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
            "?붽퀬 議고쉶: ?됯?湲?{:,} / ?덉닔湲?{:,} / D+2={:,} / 二쇰Ц媛??{:,} "
            "(kt00001.ord_alow_amt={:,}, d2_ord_psbl={:,})",
            eval_amt, entr, d2_entra, available_cash,
            deposit.get("ord_alow_amt", 0), deposit.get("d2_ord_psbl_amt", 0),
        )
        # ?뱀씪 ?쒖옉 ?먮낯 = 二쇰Ц媛??+ ?됯?湲덉븸 (?덈떎硫?
        rm.set_start_capital(available_cash + eval_amt)
    except Exception as e:
        logger.warning("?붽퀬 議고쉶 ?ㅽ뙣: {}", e)

    # ?? ?먮낯 ?쒓퀎 ?덈궡 (30?꾩감 ?뺤쭅??議곗뼵) ???????
    if 0 < available_cash < 2_000_000:
        logger.warning(
            "?뮕 [?먮낯 ?쒓퀎 ?덈궡] ?꾩옱 留ㅼ닔媛??{:,}?? 遺꾨떒???⑦?濡??듦퀎???곗쐞 ?뺣낫??"
            "理쒖냼 1,000留뚯썝~2,000留뚯썝???꾩슂?⑸땲?? 嫄곕옒鍮꾩슜 0.4% (?섏닔猷??멸툑+?щ━?쇱?)媛 "
            "?묒? ?먮낯?먯꽑 ??鍮꾩쨷??李⑥????좎쓽誘명븳 alpha ?대젮?. "
            "?꾩옱???숈뒿/寃利?紐⑹쟻 沅뚯옣.", available_cash,
        )

    if available_cash <= 0:
        logger.critical(
            "?좑툘  留ㅼ닔媛??湲덉븸 0?????ㅼ젣 ?붽퀬媛 ?녾굅??API ?꾨뱶 留ㅽ븨???섎せ?? "
            "?좉퇋 留ㅼ닔???쒕룄?섏? ?딆뒿?덈떎 (?ъ???留ㅻ룄/泥?궛? ?뺤긽 ?숈옉)."
        )
        tg.notify_text(
            "?좑툘 ?붽퀬 議고쉶 寃곌낵 留ㅼ닔媛??0??n"
            "?좉퇋 留ㅼ닔 蹂대쪟. 濡쒓렇??'kt00004 ?붽퀬 ?묐떟 | ?쒖꽦?꾨뱶=...' ?뺤씤 ?꾩슂."
        )

    # ?? 蹂댁쑀 醫낅ぉ 蹂듭썝 (?ъ떆????RiskManager???깅줉) ?????
    from core.risk_manager import STYLE_LONG as _SL
    try:
        holdings_init = kw.get_holdings() if hasattr(kw, "get_holdings") else []
        for h in holdings_init:
            # ?ъ떆??吏곹썑???⑦?/?ν닾 援щ텇 ?뺣낫媛 ?놁쓬 ??蹂댁닔?곸쑝濡??ν닾濡??깅줉
            # (?ν닾 ?먯젅 -7%/?듭젅 +20% 媛 ???덇렇?ъ? ???꾩쓽 泥?궛 ?꾪뿕 ??
            rm.add_position(h["ticker"], h["name"] or h["ticker"],
                            h["qty"], h["avg_price"] or h["cur_price"], style=_SL)
        if holdings_init:
            logger.info("??蹂댁쑀醫낅ぉ {}媛?RiskManager??蹂듭썝 ?꾨즺", len(holdings_init))
            tg.notify_text(
                f"?삼툘 蹂댁쑀醫낅ぉ {len(holdings_init)}媛?蹂듭썝\n" +
                "\n".join(f"  ??{h['name'] or h['ticker']} {h['qty']}二?@{h['avg_price']:,.0f}??"
                         f"({h['pnl_rate']:+.2f}%)" for h in holdings_init[:10])
            )
    except Exception as e:
        logger.warning("蹂댁쑀醫낅ぉ 蹂듭썝 ?ㅽ뙣: {}", e)

    # ?? 媛???꾧툑 媛깆떊 ?ы띁 (留??ㅼ틪留덈떎 ?몄텧) ????????????
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
                logger.debug("二쇰Ц媛??媛깆떊: {:,} ??{:,}", available_cash, new_cash)
                available_cash = new_cash
        except Exception as _e:
            logger.debug("二쇰Ц媛??媛깆떊 ?ㅽ뙣: {}", _e)

    cmd.start_polling(poll_interval=3.0)
    cmd.send_startup_message()

    # ?? WebSocket ?ㅼ떆媛??쒖꽭 諛깃렇?쇱슫???ㅻ젅????????????????
    # kiwoom_ws.py??KiwoomWebSocket??硫붿씤 ?ㅻ젅?쒖? 媛숈? ?꾨줈?몄뒪?먯꽌 ?ㅽ뻾
    # ??price_cache???ㅼ떆媛?媛寃⑹쓣 湲곕줉 ??DataCollector媛 罹먯떆??媛寃??쒖슜
    import threading, asyncio

    def _run_ws_bg():
        try:
            from core.kiwoom_ws import KiwoomWebSocket
            from stock_universe import get_ticker
            _ws_tickers = []
            for _n in WATCH_LIST:
                _t = get_ticker(_n)
                if _t and (_t.endswith(".KS") or _t.endswith(".KQ")):
                    _ws_tickers.append(_t.replace(".KS","").replace(".KQ",""))
            logger.info("[WS諛깃렇?쇱슫?? ?ㅼ떆媛??쒖꽭 媛먯떆 ?쒖옉 ??{}媛?醫낅ぉ", len(_ws_tickers))
            _ws = KiwoomWebSocket(tickers=_ws_tickers)
            asyncio.run(_ws.run())
        except Exception as _e:
            logger.error("[WS諛깃렇?쇱슫?? ?쒖옉 ?ㅽ뙣: {} ???ㅼ떆媛??쒖꽭 ?놁쓬", _e)

    _ws_thread = threading.Thread(target=_run_ws_bg, daemon=True, name="KiwoomWS")
    _ws_thread.start()
    logger.info("WebSocket background thread started")
    # ????????????????????????????????????????????????????????

    # 湲곕낯 ?뚮┝ ?깅줉
    from stock_universe import resolve as _resolve
    for name in WATCH_LIST[:3]:
        _t, _ = _resolve(name)
        am.add_volume_alert(_t, _t, multiplier=3.5)

    logger.info("??쒕낫?? python dashboard/realtime_app.py ??http://localhost:5001/advanced")

    # ?? 硫붿씤 猷⑦봽 ????????????????????????????
    while _running:
        now = datetime.now()

        # ?ъ뒪泥댄겕 (1?쒓컙)
        if time.time() - health_checked >= 3600:
            status = hm.check()
            hm.ping_scan()
            if not status.is_healthy:
                hm.try_recover(status)
            health_checked = time.time()

        # ?⑦? 媛뺤젣 泥?궛 (15:20~15:29) ???ν닾 ?ъ??섏? ?쒖쇅
        if is_force_close_window():
            from core.risk_manager import STYLE_DAY
            day_positions = rm.get_positions_by_style(STYLE_DAY)
            if day_positions:
                logger.warning("???⑦? 媛뺤젣 泥?궛 ?쒖옉 ({}媛??ъ???", len(day_positions))
                tg.notify_text(f"???⑦? 媛뺤젣 泥?궛\n{len(day_positions)}媛??ъ????꾨웾 留ㅻ룄 (?ν닾 ?쒖쇅)")
                from core.ai_judge import AIVerdict
                for ticker in list(day_positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                                      reason="??留덇컧 ??媛뺤젣 泥?궛", target_price=snap.current_price,
                                      stop_loss=snap.current_price, position_size="SMALL")
                        om.execute(v, snap.current_price)
                        tg.notify_verdict(v, snap.current_price)
                        tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "媛뺤젣泥?궛")
                    except Exception as e:
                        logger.error("[{}] 媛뺤젣 泥?궛 ?ㅽ뙣: {}", ticker, e)
            time.sleep(60)
            continue

        # ?? ?쒓컙?몄쥌媛 泥?궛 (15:40~16:00) ?????????
        # ?뺢퇋?μ뿉??紐??섏삩 ?ъ??섏쓣 醫낃? ?⑥씪媛濡??먮룞 泥?궛. ?좉퇋 留ㅼ닔 ?놁쓬.
        if is_after_hours_close():
            from core.ai_judge import AIVerdict as _AV
            positions = rm.get_positions()
            sold = 0
            logger.info("after-hours close mode ({}~16:00) | positions {} | cash {:,}",
                        now.strftime("%H:%M"), len(positions), available_cash)
            if not positions:
                logger.info("   ??泥?궛 ????놁쓬 ??硫붾え由ъ긽 ?ъ???0媛?"
                            "(?ъ떆??吏곹썑???ㅼ? ?붽퀬 蹂듭썝 ?꾩씠??鍮꾩뼱 ?덉쓣 ???덉쓬)")
            else:
                for ticker in list(positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        # ?먯젅쨌?듭젅 泥댄겕 (?쒓컙?몄뿉?쒕룄 議곌굔 異⑹” ??泥?궛)
                        if (rm.check_stop_loss(ticker, snap.current_price)
                                or rm.check_take_profit(ticker, snap.current_price)):
                            v = _AV(ticker=ticker, action="SELL", confidence=100,
                                    reason="?쒓컙?몄쥌媛 泥?궛", target_price=snap.current_price,
                                    stop_loss=snap.current_price, position_size="SMALL")
                            # hoga="81" ???ㅼ? trde_tp=81 (?쒓컙?몄쥌媛 ?⑥씪媛)
                            om.execute(v, snap.current_price, hoga="81")
                            tg.notify_verdict(v, snap.current_price)
                            tracker.record_signal("system", ticker, "SELL", 100,
                                                  snap.current_price, True, "?쒓컙?몄쥌媛泥?궛")
                            sold += 1
                    except Exception as e:
                        logger.error("[{}] ?쒓컙??泥?궛 ?ㅽ뙣: {}", ticker, e)
                logger.info("after-hours close attempted {} / positions {}", sold, len(positions))
            time.sleep(60)
            continue

        # ?? ?쒓컙?몃떒?쇨? 泥?궛 (16:00~18:00) ?????????
        # 10遺꾨쭏???⑥씪媛 泥닿껐, 짹10% 媛寃??쒗븳. 泥?궛 ?꾩슜, ?좉퇋 留ㅼ닔 湲덉?.
        if is_after_hours_single():
            from core.ai_judge import AIVerdict as _AV2
            positions = rm.get_positions()
            if positions:
                logger.info("???쒓컙?몃떒?쇨? 紐⑤뱶 ({}~18:00) | 蹂댁쑀 {}媛?| 泥?궛 ?꾩슜",
                            now.strftime("%H:%M"), len(positions))
                sold = 0
                for ticker in list(positions):
                    try:
                        snap = dc.get_snapshot(ticker)
                        # ?쒓컙?몃떒?쇨???짹10% ?쒗븳 ???먯젅쨌?듭젅 洹몃?濡??곸슜
                        if (rm.check_stop_loss(ticker, snap.current_price)
                                or rm.check_take_profit(ticker, snap.current_price)):
                            v = _AV2(ticker=ticker, action="SELL", confidence=100,
                                     reason="?쒓컙?몃떒?쇨? 泥?궛", target_price=snap.current_price,
                                     stop_loss=snap.current_price, position_size="SMALL")
                            # hoga="62" ???ㅼ? trde_tp=62 (?쒓컙?몃떒?쇨?). 吏?뺢?濡??꾩옱媛 蹂대깂
                            om.execute(v, snap.current_price, hoga="62")
                            tg.notify_verdict(v, snap.current_price)
                            tracker.record_signal("system", ticker, "SELL", 100,
                                                  snap.current_price, True, "?쒓컙?몃떒?쇨?泥?궛")
                            sold += 1
                    except Exception as e:
                        logger.error("[{}] ?쒓컙?몃떒?쇨? 泥?궛 ?ㅽ뙣: {}", ticker, e)
                logger.info("single-price close attempted {} / positions {}", sold, len(positions))
            time.sleep(120)   # ?⑥씪媛 泥닿껐??10遺??⑥쐞???먯＜ ???꾩슂 ?놁쓬
            continue

        # ??留덇컧 泥섎━
        if is_close_window() and not daily_reported:
            logger.info("??留덇컧 泥섎━ ?쒖옉")
            holdings = pm.get_holdings()
            stats    = pm.get_portfolio_stats(holdings)
            pm.save_snapshot(stats, holdings)
            pm.print_holdings(holdings, stats)

            # ?깃낵 洹??遺꾩꽍
            attr = attrib.analyze()
            attrib.print_report(attr)
            attrib.save_html(attr)

            # 由ы룷??
            try:
                tune_result = tuner.tune(force=True)
                tg.notify_text(
                    "?곸쓳???쒕떇 ?꾨즺\n"
                    f"?쒕낯: {tune_result.trades}嫄?| ?밸쪧: {tune_result.winrate:.1%}\n"
                    f"?쒗룊洹? {tune_result.avg_net_pnl_pct:+.2f}% | PF: {tune_result.profit_factor:.2f}\n"
                    f"min_conf: {tune_result.min_confidence} | R:R: {tune_result.min_effective_rr:.2f}"
                )
            except Exception as e:
                logger.warning("留덇컧 ?곸쓳???쒕떇 ?ㅽ뙣: {}", e)

            rg.generate_daily_report()
            rg.generate_html_daily()
            if now.weekday() == 4:
                rg.generate_weekly_report()

            # ?꾨왂 由щ뜑蹂대뱶
            tracker.print_leaderboard()

            # DB ?뺣━ (90???댁긽)
            deleted = db_mgr.cleanup(retain_days=90)
            if deleted:
                logger.info("DB ?뺣━: {}", deleted)

            daily_reported = True
            time.sleep(120)
            continue

        if not is_close_window():
            daily_reported = False

        # ? 24?쒓컙 援?궡+?댁쇅 70??紐⑤땲?곕쭅 (???댁쇅 臾닿?, ?숈쟻 二쇨린) ?
        active_session = is_market() or is_us_market_session()
        hot_alert_interval = HOT_INTERVAL_ACTIVE if active_session else HOT_INTERVAL_QUIET
        if time.time() - last_hot_scan >= hot_alert_interval:
            last_hot_scan = time.time()
            hm.ping_scan()   # ???ㅼ틪???쒕룞 ?좏샇濡?移댁슫??(?ъ뒪 ?ㅼ틪吏??false-alarm 諛⑹?)
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

            # ?듯빀 寃利? 湲곗닠 60+ ?????寃뚯씠????湲곗닠 70+ 留?AI ?몄텧 (top 30) ???듯빀 70+ 留??대┝
            hot_result = screener.run(
                universe=_full_universe, use_mock=False,
                min_score=60.0,
                ai_top_n=30,           # AI ?몄텧 ?곹븳 ???ъ쟾?꾪꽣(湲곗닠70+ + ??뷀넻怨?濡??ㅼ젣??蹂댄넻 ?곴쾶
                composite_min=70.0,
            )
            hot_candidates = [c for c in hot_result.candidates if c.score >= 70]
            if hot_candidates:
                tg.notify_hot_candidates(
                    hot_candidates,
                    title=f"70???댁긽 ?꾨낫 | {','.join(_sess)} | {now.strftime('%H:%M')}",
                )
                for _hc in hot_candidates:
                    _alerted_today.add(_hc.ticker)
                    logger.info("70+ candidate: {} | {:.0f}", _hc.ticker, _hc.score)

        if not is_market():
            logger.debug("?????쒓컙 ({})", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        if rm.is_halted():
            logger.warning("??嫄곕옒 以묐떒")
            tg.notify_halt(rm.get_daily_pnl())
            time.sleep(interval)
            continue

        scan_count += 1
        _refresh_cash()  # 留??ㅼ틪留덈떎 ?ㅼ? ?붽퀬 ?ъ“?????ㅼ떆媛?留ㅼ닔媛?μ븸 諛섏쁺
        logger.info("??? ?ㅼ틪 #{} | {} | 留ㅼ닔媛??{:,}?????",
                    scan_count, now.strftime("%H:%M:%S"), available_cash)
        logger.info(
            "capital buckets | day left {:,}/{:,} | long left {:,}/{:,}",
            _style_cash(STYLE_DAY), int(RISK_CONFIG.get("capital_limit", 0) or 0),
            _style_cash(STYLE_LONG), int(LONG_RISK_CONFIG.get("capital_limit", 0) or 0),
        )
        hm.ping_scan()

        # ???쒖옉 泥??ㅼ틪: ?ㅽ겕由щ꼫 ?ㅽ뻾
        if scan_count == 1 or now.strftime("%H:%M") == "09:05":
            logger.info("醫낅ぉ ?ㅽ겕由щ꼫 ?ㅽ뻾...")
            scr_result = screener.run(
                universe=WATCH_LIST, use_mock=False, min_score=10.0
            )
            opening_hot = [c for c in scr_result.candidates if c.score >= 70]
            if opening_hot:
                tg.notify_hot_candidates(opening_hot, title="opening 70+ candidates")
            for c in scr_result.candidates:
                if c.score >= 70:
                    _alerted_today.add(c.ticker)

        # ? ?ъ????먯젅쨌?듭젅 泥댄겕 ?
        for ticker, pos in list(rm.get_positions().items()):
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("[{}] ?곗씠???ㅽ뙣: {}", ticker, e); continue

            # ?뚮┝ 泥댄겕
            am.check(snap)

            from core.ai_judge import AIVerdict
            if rm.check_stop_loss(ticker, snap.current_price):
                # ?? ?⑦? 臾쇰┝ ???ν닾 ?꾪솚 ?먮떒 ????????
                pos = rm.get_positions().get(ticker)
                _convert = False
                if pos and pos.style == "daytrading" and RISK_CONFIG.get("convert_to_long_enabled", True):
                    # ?꾪솚 議곌굔: MA120 ??+ ATR_pct ?묒쓬 + AI ?좊ː??Hurdle
                    cond_ma120 = (not RISK_CONFIG.get("convert_require_ma120", True)) or \
                                 (snap.ma120 > 0 and snap.current_price > snap.ma120)
                    cond_atr = snap.atr_pct < RISK_CONFIG.get("convert_max_atr_pct", 3.0)
                    if cond_ma120 and cond_atr:
                        rm.convert_to_long(ticker,
                            reason=f"?⑦? ?먯젅 援ш컙?대굹 MA120 ?곷떒(+ATR {snap.atr_pct:.1f}%) ???ν닾 ?꾪솚")
                        tg.notify_text(
                            f"?봽 ?⑦??믪옣???꾪솚: {ticker}\n"
                            f"吏꾩엯 {pos.avg_price:,.0f} ???꾩옱 {snap.current_price:,.0f}\n"
                            f"MA120 {snap.ma120:,.0f} 쨌 ATR {snap.atr_pct:.2f}%"
                        )
                        tracker.record_signal("system", ticker, "CONVERT", 100,
                                              snap.current_price, True, "convert_to_long")
                        _convert = True

                if not _convert:
                    v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                                  reason="?먯젅???꾨떖", target_price=snap.current_price,
                                  stop_loss=snap.current_price, position_size="SMALL")
                    om.execute(v, snap.current_price)
                    tg.notify_verdict(v, snap.current_price)
                    tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "?먯젅")

            elif rm.check_take_profit(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="?듭젅???꾨떖", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)
                tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "?듭젅")

        # ? ?⑦? ?좉퇋 吏꾩엯 ?ㅼ틪 ?
        from core.risk_manager import STYLE_DAY, STYLE_LONG
        from core.ai_judge import AIVerdict as AV
        from config import fmt_price as _fmt_price

        def _execute_entry(snap, verdict, active_strategy, style):
            """怨듯넻 吏꾩엯 ?ㅽ뻾 ?ы띁"""
            style_cash = _style_cash(style)
            if style_cash <= 0:
                logger.info("[{}] budget exhausted: {}", style, snap.ticker)
                return
            sizing = ps.calc(snap, verdict.confidence, style_cash)
            if not sizing.is_valid:
                logger.debug("?ъ????ъ씠吏????섎웾 0 [{}]", snap.ticker)
                return
            basic_v = AV(
                ticker=snap.ticker,
                action=verdict.action,
                confidence=verdict.confidence,
                reason=f"[{style}][{active_strategy.name}][?댁뒪:{verdict.news_judgment}] {verdict.reason}",
                target_price=verdict.target_price,
                stop_loss=sizing.stop_loss,
                position_size=active_strategy.name.upper()[:5],
            )
            om.execute(basic_v, snap.current_price, style_cash, style=style, atr=snap.atr)
            from config import fmt_price as _fmt_price
            key_points = getattr(verdict, "news_key_points", []) or []
            news_line = ""
            if key_points:
                news_line = " | " + " / ".join(str(p)[:45] for p in key_points[:2])
            tg.notify_text(
                f"[entry] {snap.ticker} {verdict.confidence:.0f} | {style}/{active_strategy.name}\n"
                f"price {_fmt_price(snap.ticker, snap.current_price)} | qty {sizing.qty} | Kelly {sizing.kelly_fraction:.1%}\n"
                f"bucket cash {style_cash:,}\n"
                f"stop {_fmt_price(snap.ticker, sizing.stop_loss)} ({sizing.stop_loss_pct:.1f}%)\n"
                f"news {verdict.news_judgment}({verdict.news_score:+d}){news_line}\n"
                f"{str(verdict.reason)[:160]}"
            )

        # ? ?⑦? ?좉퇋 吏꾩엯: ?쒓컙 ?꾪꽣 ?
        _entry_start = dtime(*map(int, RISK_CONFIG.get("entry_start", "09:40").split(":")))
        _entry_end   = dtime(*map(int, RISK_CONFIG.get("entry_end",   "14:30").split(":")))
        _in_entry_window = _entry_start <= now.time() <= _entry_end

        if not _in_entry_window:
            logger.debug("[?⑦?] 吏꾩엯 ?쒓컙 ??({}) ???좉퇋 留ㅼ닔 ?놁쓬", now.strftime("%H:%M"))

        if _in_entry_window:
            # ? KOSPI 諛⑺뼢 ?꾪꽣 ?
            _kospi_ok = True
            try:
                import yfinance as _yf
                _ki = _yf.Ticker("^KS11").fast_info
                _kospi_chg = (_ki.last_price - _ki.previous_close) / _ki.previous_close
                if _kospi_chg < RISK_CONFIG.get("kospi_min_change", -0.01):
                    _kospi_ok = False
                    logger.warning("[?⑦?] KOSPI ?섎씫({:.2%}) ???좉퇋 留ㅼ닔 以묐떒", _kospi_chg)
            except Exception:
                pass

        if _in_entry_window and _kospi_ok:
            # ?⑦? 吏꾩엯 ?ㅼ틪: ?곗꽑?쒖쐞 醫낅ぉ(??30媛?留?留ㅻ텇 鍮좊Ⅴ寃?泥댄겕
            # (?꾩껜 90醫낅ぉ? 70????紐⑤땲?곕쭅??10~30遺꾨쭏???곕줈 而ㅻ쾭)
            for name in WATCH_LIST_PRIORITY:
                if not _running or name in rm.get_positions():
                    continue
                try:
                    snap = dc.get_snapshot(name)
                except Exception as e:
                    logger.error("[?⑦?][{}] ?섏쭛 ?ㅽ뙣: {}", name, e); continue

                from stock_universe import is_domestic
                if not is_domestic(snap.ticker):
                    continue

                # ?? 120?쇱꽑 ?꾪꽣: ?꾩옱媛 > MA120 ?댁뼱??吏꾩엯 ?
                if snap.ma120 > 0 and snap.current_price <= snap.ma120:
                    logger.debug("[?⑦?][{}] 120?쇱꽑 ?꾨옒 ??吏꾩엯 遺덇? (?꾩옱媛:{:,.0f} ??MA120:{:,.0f})",
                                 snap.ticker, snap.current_price, snap.ma120)
                    continue

                am.check(snap)

                # ?꾨왂 ?ㅼ쨷 ?뺤씤 ??min_strategies 媛??댁긽 ?듦낵?댁빞 吏꾩엯
                _min_st = RISK_CONFIG.get("min_strategies", 2)
                _passed = [s for s in strategies if s.should_enter(snap)]
                if len(_passed) < _min_st:
                    continue
                active_strategy = _passed[0]

                # ?? ??붾찘??寃뚯씠??(?묒쟾二셋룹쟻?먭린??而? ??
                fund = fund_gate.check(snap.ticker)
                if not fund.passed:
                    logger.info("[?⑦?][{}] ??붾찘??李⑤떒: {}",
                                snap.ticker, " 쨌 ".join(fund.reasons))
                    tracker.record_signal(active_strategy.name, snap.ticker, "BLOCK_FUND",
                                          0, snap.current_price, False,
                                          " 쨌 ".join(fund.reasons))
                    continue

                verdict = int_judge.judge(snap, fetch_news=True)
                verdict.ticker = snap.ticker
                tracker.record_signal(active_strategy.name, snap.ticker, verdict.action,
                                      verdict.confidence, snap.current_price,
                                      verdict.is_executable, verdict.reason)
                strat_names = "+".join(s.name for s in _passed)
                logger.info("[?⑦?] {}媛쒖쟾??{}) | {}", len(_passed), strat_names, verdict.summary_line)

                if verdict.news_blocked:
                    logger.info("[day] news block: {} | {}({:+d}) | {}",
                                snap.ticker, verdict.news_judgment,
                                verdict.news_score, verdict.news_reason[:80])
                    continue
                if not verdict.is_executable:
                    continue

                _execute_entry(snap, verdict, active_strategy, STYLE_DAY)

        # ? ?ν닾 ?좉퇋 吏꾩엯 ?ㅼ틪 (30遺꾨쭏?? ?
        if time.time() - last_long_scan >= long_interval:
            last_long_scan = time.time()
            long_scan_count += 1
            logger.info("??? ?ν닾 ?ㅼ틪 #{} | {} ???", long_scan_count, now.strftime("%H:%M:%S"))

            for name in WATCH_LIST_LONG:
                if not _running or name in rm.get_positions():
                    continue
                try:
                    snap = dc.get_snapshot(name)
                except Exception as e:
                    logger.error("[?ν닾][{}] ?섏쭛 ?ㅽ뙣: {}", name, e); continue

                # ?ㅼ? OpenAPI???댁쇅二쇱떇 二쇰Ц 誘몄??????ν닾 吏꾩엯 ?먯껜瑜??ㅽ궢
                from stock_universe import is_domestic
                if not is_domestic(snap.ticker):
                    logger.debug("[?ν닾][{}] ?댁쇅二쇱떇 ???ㅼ? 二쇰Ц 誘몄??? 吏꾩엯 ?ㅽ궢", snap.ticker)
                    continue

                am.check(snap)
                # ?ν닾???좊ː??湲곗?????쑝誘濡??꾨왂 ?꾪꽣 ?놁씠 AI 吏곸젒 ?먮떒
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

                # ?ν닾??留ㅼ닔 媛???щ? 泥댄겕
                long_cash = _style_cash(STYLE_LONG)
                check = rm.check_buy(snap.ticker, snap.current_price,
                                     verdict.confidence, long_cash, style=STYLE_LONG)
                if not check.allowed:
                    logger.debug("[?ν닾] 留ㅼ닔 李⑤떒: {}", check.reason)
                    continue

                tracker.record_signal("longterm", snap.ticker, verdict.action,
                                      verdict.confidence, snap.current_price,
                                      verdict.is_executable, verdict.reason)
                logger.info("[?ν닾] {} | {}", snap.ticker, verdict.summary_line)

                class _LongStrategy:
                    name = "longterm"
                    def should_enter(self, _): return True

                _execute_entry(snap, verdict, _LongStrategy(), STYLE_LONG)

        logger.info("scan #{} complete | sleep {} sec", scan_count, interval)
        time.sleep(interval)

    # ?? 醫낅즺 泥섎━ ????????????????????????????
    logger.info("醫낅즺 泥섎━ 以?..")
    cmd.stop()
    om.cancel_all_pending()
    kw.disconnect()

    # 理쒖쥌 ?ы듃?대━??
    holdings = pm.get_holdings()
    stats    = pm.get_portfolio_stats(holdings)
    pm.print_holdings(holdings, stats)
    tracker.print_leaderboard()

    tg.notify_text(
        f"auto trading stopped\n"
        f"daily pnl: {rm.get_daily_pnl():+,.0f}\n"
        f"total scans: {scan_count}"
    )
    logger.info("normal shutdown complete")


if __name__ == "__main__":
    main()
