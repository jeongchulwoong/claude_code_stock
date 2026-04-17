"""
main.py — 진입점

실행:
    # 페이퍼 트레이딩 (기본)
    python main.py

    # 실거래 (config.py 또는 .env에서 TRADING_MODE=live 설정 필요)
    TRADING_MODE=live python main.py
"""

from __future__ import annotations

import sys
import signal
from datetime import datetime, time as dtime

from loguru import logger

from config import (
    PAPER_TRADING,
    SCHEDULE_CONFIG,
    WATCH_LIST,
    LOG_DIR,
    TRADING_MODE,
)
from core.kiwoom_api import get_kiwoom_api
from core.data_collector import DataCollector
from core.ai_judge import AIJudge
from core.risk_manager import RiskManager
from core.order_manager import OrderManager
from core.telegram_bot import TelegramBot
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy


# ── 로깅 설정 ─────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
)
logger.add(
    LOG_DIR / "trade_{time:YYYYMMDD}.log",
    level="DEBUG",
    rotation="1 day",
    retention="30 days",
    encoding="utf-8",
)


# ── 종료 핸들러 ───────────────────────────────
_running = True

def _signal_handler(sig, frame):
    global _running
    logger.warning("종료 신호 수신 — 안전하게 종료합니다.")
    _running = False

signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── 장 시간 체크 ──────────────────────────────

def _is_market_open() -> bool:
    """현재 시각이 장 운영 시간인지 확인한다."""
    now = datetime.now().time()
    open_t  = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    close_t = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    return open_t <= now <= close_t


# ── 메인 루프 ─────────────────────────────────

def main() -> None:
    import time

    mode_str = "📄 페이퍼 트레이딩" if PAPER_TRADING else "💰 실거래"
    logger.info("="*55)
    logger.info("  🤖 AI 기반 국내주식 자동매매 시스템")
    logger.info("  모드: {} | 감시 종목: {}개", mode_str, len(WATCH_LIST))
    logger.info("="*55)

    # 실거래 모드 이중 확인
    if not PAPER_TRADING:
        logger.critical("⚠️  실거래 모드입니다! 10초 후 시작됩니다. 취소하려면 Ctrl+C")
        time.sleep(10)

    # ── 컴포넌트 초기화 ───────────────────────
    kw = get_kiwoom_api(paper_trading=PAPER_TRADING)
    dc = DataCollector(kw)
    ai = AIJudge()
    rm = RiskManager()
    om = OrderManager(kw, rm)
    tg = TelegramBot()

    strategies = [
        MomentumStrategy(),
        MeanReversionStrategy(),
    ]

    available_cash = 5_000_000   # TODO: 키움 API에서 실시간 조회로 교체

    # ── 로그인 ───────────────────────────────
    try:
        kw.login()
    except Exception as e:
        logger.critical("로그인 실패: {}", e)
        sys.exit(1)

    tg.notify_text(f"🤖 자동매매 시작 ({mode_str})\n감시 종목: {', '.join(WATCH_LIST)}")
    scan_count = 0
    interval   = SCHEDULE_CONFIG["scan_interval_minutes"] * 60

    # ── 메인 루프 ────────────────────────────
    while _running:
        if not _is_market_open():
            logger.debug("장 외 시간 — 대기 중 ({})", datetime.now().strftime("%H:%M:%S"))
            time.sleep(60)
            continue

        scan_count += 1
        logger.info("─── 스캔 #{} ───", scan_count)

        if rm.is_halted():
            logger.warning("⛔ 일일 손실 한도 초과 — 오늘 거래 중단")
            tg.notify_halt(rm.get_daily_pnl())
            time.sleep(interval)
            continue

        # 보유 포지션 손절·익절 체크
        for ticker, pos in list(rm.get_positions().items()):
            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("포지션 체크 실패 [{}]: {}", ticker, e)
                continue

            from core.ai_judge import AIVerdict

            if rm.check_stop_loss(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="손절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)

            elif rm.check_take_profit(ticker, snap.current_price):
                v = AIVerdict(ticker=ticker, action="SELL", confidence=100,
                              reason="익절선 도달", target_price=snap.current_price,
                              stop_loss=snap.current_price, position_size="SMALL")
                om.execute(v, snap.current_price)
                tg.notify_verdict(v, snap.current_price)

        # 신규 진입 스캔
        for ticker in WATCH_LIST:
            if not _running:
                break
            if ticker in rm.get_positions():
                continue

            try:
                snap = dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("데이터 수집 실패 [{}]: {}", ticker, e)
                continue

            for strategy in strategies:
                if strategy.should_enter(snap):
                    verdict = ai.judge(snap)
                    tg.notify_verdict(verdict, snap.current_price)
                    om.execute(verdict, snap.current_price, available_cash)
                    break

        logger.info("스캔 완료. {}초 후 재실행.", interval)
        time.sleep(interval)

    # ── 종료 처리 ────────────────────────────
    logger.info("시스템 종료 중...")
    om.cancel_all_pending()
    kw.disconnect()
    tg.notify_text(
        f"🛑 자동매매 종료\n"
        f"일일 손익: {rm.get_daily_pnl():+,.0f}원\n"
        f"총 스캔: {scan_count}회"
    )
    logger.info("정상 종료 완료.")


if __name__ == "__main__":
    main()
