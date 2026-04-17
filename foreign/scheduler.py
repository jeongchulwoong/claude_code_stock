"""
foreign/scheduler.py — 해외주식 주기적 스캔 스케줄러

미국 시장 시간(EST 09:30~16:00 = KST 23:30~06:00)에 맞춰
지정 간격으로 Finnhub → AI 판단 → 텔레그램 알림을 실행한다.

실행:
    python foreign/scheduler.py
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from foreign.api_client import ForeignDataCollector
from foreign.signal_engine import ForeignSignalEngine, ForeignTelegramNotifier

# ── 감시 종목 ─────────────────────────────────
WATCH_LIST = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "GOOGL",  # Alphabet
    "TSLA",   # Tesla
    "NVDA",   # NVIDIA
    "META",   # Meta
    "AMZN",   # Amazon
]

# ── 스케줄 설정 ───────────────────────────────
SCAN_INTERVAL_MIN = 30    # 30분마다 스캔
EST = ZoneInfo("America/New_York")

_running = True
def _stop(sig, frame):
    global _running
    logger.warning("종료 신호 수신")
    _running = False

signal.signal(signal.SIGINT,  _stop)
signal.signal(signal.SIGTERM, _stop)


# ── 시장 시간 체크 ────────────────────────────

def is_us_market_open() -> bool:
    """미국 주식 시장 운영 시간 여부 (EST 09:30~16:00)"""
    now = datetime.now(tz=EST)
    # 평일만
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close


# ── 단일 스캔 사이클 ──────────────────────────

def run_scan(
    collector:  ForeignDataCollector,
    engine:     ForeignSignalEngine,
    notifier:   ForeignTelegramNotifier,
    tickers:    list[str],
) -> None:
    logger.info("=" * 50)
    logger.info("해외주식 스캔 시작 | {}개 종목", len(tickers))

    # 데이터 수집
    snaps = collector.get_snapshots(tickers)
    if not snaps:
        logger.warning("스냅샷 수집 실패")
        return

    # AI 판단
    signals = engine.generate_batch(snaps)

    # 결과 요약 로그
    for s in signals:
        icon = "🟢" if s.action=="BUY" else "🔴" if s.action=="SELL" else "🟡"
        logger.info(
            "{} {} | ${:.2f} ({:+.2f}%) | RSI:{:.1f} | 신뢰:{} | {}",
            icon, s.ticker, s.current_price, s.change_pct,
            s.rsi, s.confidence, s.action,
        )

    # 텔레그램 — 전체 요약
    notifier.send_watchlist_summary(signals)

    # 텔레그램 — 실행 가능 신호 개별 발송
    for s in signals:
        if s.is_actionable:
            notifier.send_signal(s)
            time.sleep(1)

    actionable = sum(1 for s in signals if s.is_actionable)
    logger.info("스캔 완료 | 실행 가능 신호: {}건", actionable)


# ── 메인 루프 ─────────────────────────────────

def main(
    tickers:      list[str] = None,
    interval_min: int       = SCAN_INTERVAL_MIN,
    market_only:  bool      = True,   # False면 시간 외에도 실행
) -> None:
    tickers = tickers or WATCH_LIST

    collector = ForeignDataCollector()
    engine    = ForeignSignalEngine()
    notifier  = ForeignTelegramNotifier()

    logger.info("해외주식 스케줄러 시작 | {}분 간격 | 종목: {}", interval_min, tickers)

    while _running:
        if market_only and not is_us_market_open():
            est_now = datetime.now(tz=EST)
            logger.debug(
                "미국 시장 외 시간 (EST {}) — 대기",
                est_now.strftime("%H:%M")
            )
            time.sleep(60)
            continue

        run_scan(collector, engine, notifier, tickers)
        logger.info("다음 스캔까지 {}분 대기...", interval_min)
        time.sleep(interval_min * 60)

    logger.info("스케줄러 종료")


# ── 단일 실행 (테스트용) ──────────────────────

def run_once(tickers: list[str] = None) -> None:
    """단발성 스캔 — 테스트 및 CI 검증용"""
    tickers   = tickers or WATCH_LIST[:3]
    collector = ForeignDataCollector()
    engine    = ForeignSignalEngine()
    notifier  = ForeignTelegramNotifier()
    run_scan(collector, engine, notifier, tickers)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once",     action="store_true", help="단발 스캔 후 종료")
    parser.add_argument("--no-market-check", action="store_true", help="시장 시간 무시")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        main(interval_min=args.interval, market_only=not args.no_market_check)
