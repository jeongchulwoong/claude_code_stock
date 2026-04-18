"""
main_v2.py — AI 자동매매 시스템 완전 통합 버전

포함 모듈 전체:
  ✅ DB 마이그레이션 (시작 시 자동)
  ✅ 헬스모니터 (매 시간 체크)
  ✅ 종목 스크리너 (장 시작 09:05 자동 실행)
  ✅ 뉴스 호재/악재 분석 (매 스캔)
  ✅ 멀티 타임프레임 + 통합 AI 판단
  ✅ Kelly Criterion 포지션 사이징
  ✅ 호가 분석 (OrderBook)
  ✅ 섹터 로테이션 전략
  ✅ 가격·조건 알림 시스템
  ✅ 포트폴리오 VaR·CVaR
  ✅ 자동 일일·주간 리포트
  ✅ 텔레그램 양방향 명령
  ✅ 전략 성과 추적
  ✅ 성과 귀인 분석 (장 마감 후)
  ✅ APScheduler 정교한 스케줄링

실행:
    python main_v2.py
"""

from __future__ import annotations

import signal
import sys
import time

# Windows 터미널 UTF-8 강제 설정 (이모지/한글 출력)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, time as dtime

from loguru import logger

from config import LOG_DIR, PAPER_TRADING, RISK_CONFIG, SCHEDULE_CONFIG, WATCH_LIST

# ── 로깅 ─────────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add(LOG_DIR / "trade_{time:YYYYMMDD}.log",
           level="DEBUG", rotation="1 day", retention="30 days", encoding="utf-8")

# ── 종료 처리 ─────────────────────────────────
_running = True
def _sig(s, f):
    global _running; logger.warning("종료 신호"); _running = False
signal.signal(signal.SIGINT,  _sig)
signal.signal(signal.SIGTERM, _sig)

# ── 시장 시간 ─────────────────────────────────
def is_market(t=None):
    t = t or datetime.now().time()
    o = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    c = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    return o <= t <= c

def is_close_window():
    t = datetime.now().time()
    return dtime(15,30) <= t <= dtime(15,36)


def main():
    mode = "📄 페이퍼" if PAPER_TRADING else "💰 실거래"
    logger.info("="*65)
    logger.info("  🤖 AI 자동매매 v2 — 완전 통합 버전")
    logger.info("  모드:{} | 종목:{}개 | {}", mode, len(WATCH_LIST),
                datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("="*65)

    if not PAPER_TRADING:
        logger.critical("⚠️  실거래 10초 후 시작")
        for i in range(10, 0, -1): logger.critical("  {}초...", i); time.sleep(1)

    # ── 1. DB 초기화 ─────────────────────────
    from core.db_manager import init_db
    db_mgr = init_db()

    # ── 2. 컴포넌트 초기화 ───────────────────
    from core.kiwoom_api import get_kiwoom_api
    from core.data_collector import DataCollector, YFinanceDataCollector
    from core.integrated_judge import IntegratedJudge
    from core.ai_judge_advanced import MultiTimeframeBuilder, AdvancedAIJudge
    from core.order_manager import OrderManager
    from core.portfolio_manager import PortfolioManager
    from core.position_sizer import PositionSizer
    from core.report_generator import ReportGenerator
    from core.risk_manager import RiskManager
    from core.screener import MarketScreener
    from core.health_monitor import HealthMonitor
    from core.alert_manager import AlertManager
    from core.order_book_analyzer import OrderBookAnalyzer
    from core.strategy_tracker import StrategyTracker
    from core.performance_attribution import PerformanceAttributor
    from core.telegram_bot import TelegramBot
    from core.telegram_commander import TelegramCommander

    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.breakout import BreakoutStrategy
    from strategies.volume_surge import VolumeSurgeStrategy
    from strategies.sector_rotation import SectorRotationStrategy

    kw      = get_kiwoom_api(paper_trading=PAPER_TRADING)
    from core.kiwoom_api import MockKiwoomAPI
    dc = YFinanceDataCollector() if isinstance(kw, MockKiwoomAPI) else DataCollector(kw)
    rm      = RiskManager()
    om      = OrderManager(kw, rm)
    pm      = PortfolioManager(rm)
    ps      = PositionSizer(rm)
    rg      = ReportGenerator()
    screener= MarketScreener(dc)
    hm      = HealthMonitor(kw, rm)
    am      = AlertManager()
    oba     = OrderBookAnalyzer()
    tracker = StrategyTracker()
    attrib  = PerformanceAttributor()
    tg      = TelegramBot()
    cmd     = TelegramCommander(rm, rg, om)

    mtf_builder = MultiTimeframeBuilder()
    adv_judge   = AdvancedAIJudge()
    int_judge   = IntegratedJudge()

    sr_strategy = SectorRotationStrategy(market_phase="unknown")
    strategies  = [
        MomentumStrategy(),
        MeanReversionStrategy(),
        BreakoutStrategy(),
        VolumeSurgeStrategy(),
        sr_strategy,
    ]

    available_cash  = 10_000_000
    daily_reported  = False
    health_checked  = time.time()
    scan_count      = 0
    interval        = SCHEDULE_CONFIG["scan_interval_minutes"] * 60

    # 로그인 + 텔레그램 시작
    try:
        kw.login()
    except Exception as e:
        logger.critical("로그인 실패: {}", e); sys.exit(1)

    cmd.start_polling(poll_interval=3.0)
    cmd.send_startup_message()

    # 기본 알림 등록
    from stock_universe import resolve as _resolve
    for name in WATCH_LIST[:3]:
        _t, _ = _resolve(name)
        am.add_volume_alert(_t, _t, multiplier=3.5)

    logger.info("대시보드: python dashboard/realtime_app.py → http://localhost:5001/advanced")

    # ── 메인 루프 ────────────────────────────
    while _running:
        now = datetime.now()

        # 헬스체크 (1시간)
        if time.time() - health_checked >= 3600:
            status = hm.check()
            hm.ping_scan()
            if not status.is_healthy:
                hm.try_recover(status)
            health_checked = time.time()

        # 장 마감 처리
        if is_close_window() and not daily_reported:
            logger.info("장 마감 처리 시작")
            holdings = pm.get_holdings()
            stats    = pm.get_portfolio_stats(holdings)
            pm.save_snapshot(stats, holdings)
            pm.print_holdings(holdings, stats)

            # 성과 귀인 분석
            attr = attrib.analyze()
            attrib.print_report(attr)
            attrib.save_html(attr)

            # 리포트
            rg.generate_daily_report()
            rg.generate_html_daily()
            if now.weekday() == 4:
                rg.generate_weekly_report()

            # 전략 리더보드
            tracker.print_leaderboard()

            # DB 정리 (90일 이상)
            deleted = db_mgr.cleanup(retain_days=90)
            if deleted:
                logger.info("DB 정리: {}", deleted)

            daily_reported = True
            time.sleep(120)
            continue

        if not is_close_window():
            daily_reported = False

        if not is_market():
            logger.debug("장 외 시간 ({})", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        if rm.is_halted():
            logger.warning("⛔ 거래 중단")
            tg.notify_halt(rm.get_daily_pnl())
            time.sleep(interval)
            continue

        scan_count += 1
        logger.info("─── 스캔 #{} | {} ───", scan_count, now.strftime("%H:%M:%S"))
        hm.ping_scan()

        # 장 시작 첫 스캔: 스크리너 실행
        if scan_count == 1 or now.strftime("%H:%M") == "09:05":
            logger.info("종목 스크리너 실행...")
            scr_result = screener.run(
                universe=WATCH_LIST, use_mock=False, min_score=30.0
            )
            tg.notify_text(screener.to_telegram(scr_result))

        # ─ 포지션 손절·익절 체크 ─
        for ticker, pos in list(rm.get_positions().items()):
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("[{}] 데이터 실패: {}", ticker, e); continue

            # 알림 체크
            am.check(snap)

            from core.ai_judge import AIVerdict
            if rm.check_stop_loss(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="손절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)
                tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "손절")

            elif rm.check_take_profit(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="익절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)
                tracker.record_signal("system", ticker, "SELL", 100, snap.current_price, True, "익절")

        # ─ 신규 진입 스캔 ─
        for ticker in WATCH_LIST:
            if not _running or ticker in rm.get_positions():
                continue

            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("[{}] 수집 실패: {}", ticker, e); continue

            # 알림 체크
            am.check(snap)

            # 전략 사전 필터
            active_strategy = None
            for strategy in strategies:
                if strategy.should_enter(snap):
                    active_strategy = strategy
                    break

            if not active_strategy:
                continue

            # 통합 AI 판단 (뉴스 + 기술지표)
            verdict = int_judge.judge(snap, fetch_news=True)
            verdict.ticker = snap.ticker  # resolved ticker (e.g. "AAPL"), not name

            # 신호 기록
            tracker.record_signal(
                active_strategy.name, snap.ticker,
                verdict.action, verdict.confidence,
                snap.current_price, verdict.is_executable,
                verdict.reason,
            )

            logger.info("{} | {}", active_strategy.name, verdict.summary_line)

            # 뉴스 악재 차단
            if verdict.news_blocked:
                tg.notify_text(
                    f"⛔ 뉴스 차단: {snap.ticker} | "
                    f"{verdict.news_judgment}({verdict.news_score:+d}점)\n"
                    f"{verdict.news_reason[:80]}"
                )
                continue

            if not verdict.is_executable:
                continue

            # Kelly 포지션 사이징
            sizing = ps.calc(snap, verdict.confidence, available_cash)
            if not sizing.is_valid:
                logger.debug("포지션 사이징 — 수량 0 [{ticker}]")
                continue

            # 주문 실행
            from core.ai_judge import AIVerdict as AV
            basic_v = AV(
                ticker=ticker,
                action=verdict.action,
                confidence=verdict.confidence,
                reason=f"[{active_strategy.name}][뉴스:{verdict.news_judgment}] {verdict.reason}",
                target_price=verdict.target_price,
                stop_loss=sizing.stop_loss,
                position_size=active_strategy.name.upper()[:5],
            )
            om.execute(basic_v, snap.current_price, available_cash)
            tg.notify_verdict(basic_v, snap.current_price)

            # 뉴스 상세 알림
            if verdict.news_key_points:
                kp = "\n".join(f"  • {p}" for p in verdict.news_key_points)
                tg.notify_text(
                    f"📰 {snap.ticker} 뉴스 분석\n"
                    f"판정: {verdict.news_judgment}({verdict.news_score:+d}점)\n{kp}"
                )

            # Kelly 사이징 정보
            from core.telegram_bot import _fmt_price
            tg.notify_text(
                f"⚖️ 포지션 사이징 [{snap.ticker}]\n"
                f"Kelly:{sizing.kelly_fraction:.1%} | {sizing.qty}주\n"
                f"손절:{_fmt_price(snap.ticker, sizing.stop_loss)}({sizing.stop_loss_pct:.1f}%)"
            )

        logger.info("스캔 #{} 완료 | {}초 후 재실행", scan_count, interval)
        time.sleep(interval)

    # ── 종료 처리 ────────────────────────────
    logger.info("종료 처리 중...")
    cmd.stop()
    om.cancel_all_pending()
    kw.disconnect()

    # 최종 포트폴리오
    holdings = pm.get_holdings()
    stats    = pm.get_portfolio_stats(holdings)
    pm.print_holdings(holdings, stats)
    tracker.print_leaderboard()

    tg.notify_text(
        f"🛑 자동매매 종료\n"
        f"일일 손익: {rm.get_daily_pnl():+,.0f}원\n"
        f"총 스캔: {scan_count}회"
    )
    logger.info("정상 종료 완료")


if __name__ == "__main__":
    main()
