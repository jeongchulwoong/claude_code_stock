"""
main.py — AI 기반 국내주식 자동매매 시스템 진입점 (통합 버전)
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, time as dtime

from loguru import logger

from config import (
    LOG_DIR, PAPER_TRADING, RISK_CONFIG,
    SCHEDULE_CONFIG, TRADING_MODE, WATCH_LIST,
)
from core.kiwoom_api import get_kiwoom_api
from core.data_collector import DataCollector
from core.integrated_judge import IntegratedJudge
from core.order_manager import OrderManager
from core.portfolio_manager import PortfolioManager
from core.report_generator import ReportGenerator
from core.risk_manager import RiskManager
from core.telegram_bot import TelegramBot
from core.telegram_commander import TelegramCommander
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy

logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add(LOG_DIR / "trade_{time:YYYYMMDD}.log",
           level="DEBUG", rotation="1 day", retention="30 days", encoding="utf-8")

_running = True
def _signal_handler(sig, frame):
    global _running
    logger.warning("종료 신호 수신")
    _running = False
signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

def _is_market_open() -> bool:
    now     = datetime.now().time()
    open_t  = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    close_t = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    return open_t <= now <= close_t

def _is_market_close_time() -> bool:
    now = datetime.now().time()
    return dtime(15, 30) <= now <= dtime(15, 35)

def main() -> None:
    mode_str = "📄 페이퍼 트레이딩" if PAPER_TRADING else "💰 실거래"
    logger.info("="*60)
    logger.info("  🤖 AI 기반 국내주식 자동매매 (통합 버전)")
    logger.info("  모드: {} | 종목: {}개", mode_str, len(WATCH_LIST))
    logger.info("="*60)

    if not PAPER_TRADING:
        logger.critical("⚠️  실거래 모드! 10초 후 시작.")
        for i in range(10, 0, -1):
            logger.critical("  {}초...", i); time.sleep(1)

    kw      = get_kiwoom_api(paper_trading=PAPER_TRADING)
    dc      = DataCollector(kw)
    rm      = RiskManager()
    om      = OrderManager(kw, rm)
    tg      = TelegramBot()
    pm      = PortfolioManager(rm)
    rg      = ReportGenerator()
    cmd     = TelegramCommander(rm, rg, om)
    judge   = IntegratedJudge()
    strategies = [MomentumStrategy(), MeanReversionStrategy()]

    available_cash = 10_000_000
    cmd.start_polling(poll_interval=3.0)
    cmd.send_startup_message()

    scan_count = 0; daily_reported = False
    interval   = SCHEDULE_CONFIG["scan_interval_minutes"] * 60

    while _running:
        now = datetime.now()

        if _is_market_close_time() and not daily_reported:
            logger.info("장 마감 — 일일 리포트 생성")
            holdings = pm.get_holdings()
            stats    = pm.get_portfolio_stats(holdings)
            pm.save_snapshot(stats, holdings)
            pm.print_holdings(holdings, stats)
            rg.generate_daily_report()
            rg.generate_html_daily()
            if now.weekday() == 4:
                rg.generate_weekly_report()
            daily_reported = True
            time.sleep(60); continue

        if not _is_market_close_time():
            daily_reported = False

        if not _is_market_open():
            logger.debug("장 외 시간 ({})", now.strftime("%H:%M"))
            time.sleep(60); continue

        if rm.is_halted():
            logger.warning("⛔ 거래 중단 상태")
            tg.notify_halt(rm.get_daily_pnl())
            time.sleep(interval); continue

        scan_count += 1
        logger.info("─── 스캔 #{} | {} ───", scan_count, now.strftime("%H:%M:%S"))

        # 손절·익절 체크
        for ticker, pos in list(rm.get_positions().items()):
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("포지션 체크 실패 [{}]: {}", ticker, e); continue
            from core.ai_judge import AIVerdict
            if rm.check_stop_loss(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="손절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price); tg.notify_verdict(v, snap.current_price)
            elif rm.check_take_profit(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="익절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price); tg.notify_verdict(v, snap.current_price)

        # 신규 진입
        for ticker in WATCH_LIST:
            if not _running or ticker in rm.get_positions(): continue
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("수집 실패 [{}]: {}", ticker, e); continue

            for strategy in strategies:
                if not strategy.should_enter(snap): continue
                verdict = judge.judge(snap, fetch_news=True)
                verdict.ticker = ticker
                logger.info(verdict.summary_line)
                if verdict.news_blocked:
                    tg.notify_text(f"⛔ 뉴스 차단: {ticker} | {verdict.news_judgment}({verdict.news_score:+d}점)")
                    break
                if verdict.action in ("BUY","SELL"):
                    from core.ai_judge import AIVerdict
                    basic_v = AIVerdict(
                        ticker=ticker, action=verdict.action,
                        confidence=verdict.confidence,
                        reason=f"[뉴스:{verdict.news_judgment}] {verdict.reason}",
                        target_price=verdict.target_price,
                        stop_loss=verdict.stop_loss, position_size=verdict.position_size,
                    )
                    tg.notify_verdict(basic_v, snap.current_price)
                    if verdict.news_key_points:
                        kp = "\n".join(f"  • {p}" for p in verdict.news_key_points)
                        tg.notify_text(f"📰 뉴스: {ticker}\n{verdict.news_judgment}({verdict.news_score:+d}점)\n{kp}")
                    om.execute(basic_v, snap.current_price, available_cash)
                break

        logger.info("스캔 완료 | {}초 후 재실행", interval)
        time.sleep(interval)

    # 종료
    logger.info("종료 중...")
    cmd.stop(); om.cancel_all_pending(); kw.disconnect()
    holdings = pm.get_holdings(); stats = pm.get_portfolio_stats(holdings)
    pm.print_holdings(holdings, stats)
    tg.notify_text(f"🛑 종료 | 손익:{rm.get_daily_pnl():+,.0f}원 | 스캔:{scan_count}회")
    logger.info("정상 종료")

if __name__ == "__main__":
    main()
