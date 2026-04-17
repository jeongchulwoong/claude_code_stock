"""
tests/paper_trading.py — 페이퍼 트레이딩 시뮬레이터

실제 주문 없이 전략 로직과 AI 판단을 검증한다.
yfinance를 통해 과거 데이터를 받아 백테스팅도 가능하다.

실행:
    python tests/paper_trading.py
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime

import pandas as pd
from loguru import logger

from config import PAPER_TRADING, RISK_CONFIG, WATCH_LIST
from core.kiwoom_api import get_kiwoom_api
from core.data_collector import DataCollector, StockSnapshot
from core.ai_judge import AIJudge
from core.risk_manager import RiskManager
from core.order_manager import OrderManager
from core.telegram_bot import TelegramBot
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy


# ── 로깅 설정 ─────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add("logs/paper_trading_{time:YYYYMMDD}.log", level="DEBUG", rotation="1 day")


class PaperTradingSimulator:
    """
    페이퍼 트레이딩 시뮬레이터.
    실제 키움 API 대신 MockKiwoomAPI를 사용한다.
    """

    def __init__(self) -> None:
        assert PAPER_TRADING, "페이퍼 트레이딩 모드가 아닙니다! config.py 확인"

        self._kw      = get_kiwoom_api(paper_trading=True)
        self._dc      = DataCollector(self._kw)
        self._ai      = AIJudge()
        self._rm      = RiskManager()
        self._om      = OrderManager(self._kw, self._rm)
        self._tg      = TelegramBot()

        self._strategies = [
            MomentumStrategy(),
            MeanReversionStrategy(),
        ]

        self._available_cash = 5_000_000   # 시뮬레이션 시작 자본 500만원
        self._scan_count     = 0

        logger.info("="*50)
        logger.info("📄 페이퍼 트레이딩 시뮬레이터 시작")
        logger.info("감시 종목: {}", WATCH_LIST)
        logger.info("시작 자본: {:,}원", self._available_cash)
        logger.info("="*50)

    def login(self) -> None:
        self._kw.login()

    def run_once(self) -> None:
        """한 번의 스캔 사이클을 실행한다."""
        self._scan_count += 1
        logger.info("─── 스캔 #{} | {} ───", self._scan_count, datetime.now().strftime("%H:%M:%S"))

        if self._rm.is_halted():
            logger.warning("⛔ 거래 중단 상태 — 스캔 건너뜀")
            return

        # 보유 포지션 손절·익절 체크
        self._check_exit_conditions()

        # 신규 진입 스캔
        for ticker in WATCH_LIST:
            if ticker in self._rm.get_positions():
                continue   # 이미 보유 중

            try:
                snap = self._dc.get_snapshot(ticker)
            except Exception as e:
                logger.error("데이터 수집 실패 [{}]: {}", ticker, e)
                continue

            # 전략 필터 → AI 판단
            for strategy in self._strategies:
                if strategy.should_enter(snap):
                    logger.info(
                        "전략 진입 조건 충족: {} [{}]", strategy.name, ticker
                    )
                    verdict = self._ai.judge(snap)
                    self._tg.notify_verdict(verdict, snap.current_price)
                    self._om.execute(verdict, snap.current_price, self._available_cash)
                    break   # 하나의 전략만 적용

    def run_loop(self, interval_sec: int = 300, max_scans: int = 0) -> None:
        """
        주기적으로 스캔을 반복한다.
        max_scans=0이면 무한 반복.
        """
        self.login()
        count = 0
        while True:
            self.run_once()
            count += 1
            if max_scans and count >= max_scans:
                break
            logger.info("다음 스캔까지 {}초 대기...", interval_sec)
            time.sleep(interval_sec)

        self._print_summary()

    def _check_exit_conditions(self) -> None:
        """보유 포지션의 손절·익절 조건을 체크한다."""
        for ticker, pos in list(self._rm.get_positions().items()):
            try:
                snap = self._dc.get_snapshot(ticker)
                price = snap.current_price
            except Exception:
                continue

            if self._rm.check_stop_loss(ticker, price):
                from core.ai_judge import AIVerdict
                verdict = AIVerdict(
                    ticker=ticker, action="SELL", confidence=100,
                    reason="손절선 도달 (-3%)", target_price=price,
                    stop_loss=price, position_size="SMALL",
                )
                self._om.execute(verdict, price)
                self._tg.notify_verdict(verdict, price)

            elif self._rm.check_take_profit(ticker, price):
                from core.ai_judge import AIVerdict
                verdict = AIVerdict(
                    ticker=ticker, action="SELL", confidence=100,
                    reason="익절선 도달 (+6%)", target_price=price,
                    stop_loss=price, position_size="SMALL",
                )
                self._om.execute(verdict, price)
                self._tg.notify_verdict(verdict, price)

    def _print_summary(self) -> None:
        """시뮬레이션 결과 요약 출력"""
        logger.info("="*50)
        logger.info("📊 페이퍼 트레이딩 결과 요약")
        logger.info("총 스캔 횟수: {}회", self._scan_count)
        logger.info("일일 손익: {:+,.0f}원", self._rm.get_daily_pnl())
        logger.info("잔여 포지션: {}개", len(self._rm.get_positions()))
        for ticker, pos in self._rm.get_positions().items():
            logger.info("  {} | {}주 | 평단:{:,}", ticker, pos.qty, int(pos.avg_price))
        logger.info("="*50)


# ── 진입점 ────────────────────────────────────

if __name__ == "__main__":
    sim = PaperTradingSimulator()

    # 단일 스캔 테스트 (CI/CD 검증용)
    sim.login()
    sim.run_once()
    sim._print_summary()
