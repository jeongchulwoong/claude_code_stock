"""
core/risk_manager.py — 리스크 관리 모듈

안전장치 체크 순서:
  1. 일일 손실 한도 초과?  → 전체 거래 중단
  2. 최대 보유 종목 수 초과? → 매수 차단
  3. 1회 투자금 한도 초과? → 수량 조정
  4. AI 신뢰도 기준 미달?  → HOLD 처리
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from loguru import logger

from config import RISK_CONFIG


# ── 결과 구조 ──────────────────────────────────

@dataclass
class RiskCheckResult:
    allowed:  bool
    reason:   str
    qty:      int = 0          # 조정된 주문 수량 (0이면 차단)
    adjusted: bool = False     # 수량 조정 여부


# ── 포지션 추적 ────────────────────────────────

@dataclass
class Position:
    ticker:      str
    name:        str
    qty:         int
    avg_price:   float
    entry_date:  date = field(default_factory=date.today)

    @property
    def invested_amount(self) -> float:
        return self.qty * self.avg_price


# ── RiskManager ────────────────────────────────

class RiskManager:
    """
    주문 전 리스크 파라미터를 검증하고,
    일일 손실 한도 초과 시 거래를 자동 중단한다.
    """

    def __init__(self) -> None:
        self._cfg = RISK_CONFIG
        self._positions: dict[str, Position] = {}   # ticker → Position
        self._daily_pnl: float = 0.0                # 오늘 실현 손익 (원)
        self._halted: bool = False                  # 거래 중단 플래그
        self._today: date = date.today()

        logger.info(
            "RiskManager 초기화 | 손절:{:.0%} | 일손실한도:{:,}원 | 최대종목:{}개",
            abs(self._cfg["stop_loss_pct"]),
            abs(self._cfg["daily_loss_limit"]),
            self._cfg["max_positions"],
        )

    # ── 퍼블릭 API ────────────────────────────

    def check_buy(
        self,
        ticker: str,
        price: int,
        confidence: int,
        available_cash: int,
    ) -> RiskCheckResult:
        """
        매수 가능 여부를 검사하고 조정된 주문 수량을 반환한다.
        """
        self._reset_if_new_day()

        # 1) 거래 중단 여부
        if self._halted:
            return RiskCheckResult(False, "일일 손실 한도 초과로 거래 중단")

        # 2) AI 신뢰도
        if confidence < self._cfg["min_confidence"]:
            return RiskCheckResult(
                False,
                f"AI 신뢰도 부족 ({confidence} < {self._cfg['min_confidence']})",
            )

        # 3) 최대 보유 종목 수
        if len(self._positions) >= self._cfg["max_positions"]:
            return RiskCheckResult(
                False,
                f"최대 보유 종목 수 초과 ({len(self._positions)}/{self._cfg['max_positions']})",
            )

        # 4) 이미 보유 중인 종목
        if ticker in self._positions:
            return RiskCheckResult(False, f"이미 보유 중인 종목: {ticker}")

        # 5) 투자금 산정
        max_invest = min(self._cfg["max_invest_per_trade"], available_cash)
        qty = int(max_invest / price)
        if qty <= 0:
            return RiskCheckResult(False, f"투자금 부족 (가용:{available_cash:,}원, 가격:{price:,}원)")

        adjusted = (qty * price) < self._cfg["max_invest_per_trade"]
        return RiskCheckResult(
            allowed=True,
            reason="리스크 검사 통과",
            qty=qty,
            adjusted=adjusted,
        )

    def check_sell(self, ticker: str) -> RiskCheckResult:
        """매도 가능 여부 검사 (보유 확인)"""
        self._reset_if_new_day()

        if ticker not in self._positions:
            return RiskCheckResult(False, f"미보유 종목 매도 시도: {ticker}")

        pos = self._positions[ticker]
        return RiskCheckResult(True, "매도 가능", qty=pos.qty)

    def check_stop_loss(self, ticker: str, current_price: int) -> bool:
        """손절 조건 달성 여부 반환 (True = 손절 실행 필요)"""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct <= self._cfg["stop_loss_pct"]:
            logger.warning(
                "손절 발동: {} | 진입:{:,} → 현재:{:,} | {:.2%}",
                ticker, int(pos.avg_price), current_price, pnl_pct,
            )
            return True
        return False

    def check_take_profit(self, ticker: str, current_price: int) -> bool:
        """익절 조건 달성 여부 반환 (True = 익절 실행 필요)"""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct >= self._cfg["take_profit_pct"]:
            logger.info(
                "익절 발동: {} | 진입:{:,} → 현재:{:,} | {:.2%}",
                ticker, int(pos.avg_price), current_price, pnl_pct,
            )
            return True
        return False

    # ── 포지션 업데이트 ───────────────────────

    def add_position(self, ticker: str, name: str, qty: int, price: float) -> None:
        self._positions[ticker] = Position(
            ticker=ticker, name=name, qty=qty, avg_price=price
        )
        logger.info("포지션 추가: {} x{}주 @{:,}", ticker, qty, int(price))

    def remove_position(self, ticker: str, sell_price: float) -> Optional[float]:
        """포지션 제거 후 실현 손익을 반환한다."""
        if ticker not in self._positions:
            logger.warning("포지션 없음: {}", ticker)
            return None
        pos = self._positions.pop(ticker)
        pnl = (sell_price - pos.avg_price) * pos.qty
        self._daily_pnl += pnl
        logger.info(
            "포지션 청산: {} | 손익:{:+,.0f}원 | 일누계:{:+,.0f}원",
            ticker, pnl, self._daily_pnl,
        )
        self._check_daily_halt()
        return pnl

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_daily_pnl(self) -> float:
        return self._daily_pnl

    def is_halted(self) -> bool:
        return self._halted

    # ── 내부 헬퍼 ────────────────────────────

    def _check_daily_halt(self) -> None:
        if self._daily_pnl <= self._cfg["daily_loss_limit"]:
            self._halted = True
            logger.critical(
                "⛔ 일일 손실 한도 초과! 거래 자동 중단 | 손실:{:+,.0f}원",
                self._daily_pnl,
            )

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._today:
            logger.info("날짜 변경 — 일일 손익·중단 플래그 초기화")
            self._today = today
            self._daily_pnl = 0.0
            self._halted = False
