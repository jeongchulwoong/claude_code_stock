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

from config import LONG_RISK_CONFIG, RISK_CONFIG


# ── 결과 구조 ──────────────────────────────────

@dataclass
class RiskCheckResult:
    allowed:  bool
    reason:   str
    qty:      int = 0          # 조정된 주문 수량 (0이면 차단)
    adjusted: bool = False     # 수량 조정 여부


# ── 포지션 추적 ────────────────────────────────

STYLE_DAY  = "daytrading"
STYLE_LONG = "longterm"


@dataclass
class Position:
    ticker:      str
    name:        str
    qty:         int
    avg_price:   float
    style:       str  = STYLE_DAY            # "daytrading" | "longterm"
    entry_date:  date = field(default_factory=date.today)

    @property
    def invested_amount(self) -> float:
        return self.qty * self.avg_price


# ── RiskManager ────────────────────────────────

class RiskManager:
    """
    단타/장투 포지션을 분리 관리한다.
    각 스타일별 독립 리스크 설정·포지션 한도·손익 집계를 적용한다.
    """

    def __init__(self) -> None:
        self._cfg      = RISK_CONFIG
        self._cfg_long = LONG_RISK_CONFIG
        self._positions: dict[str, Position] = {}   # ticker → Position (전체)
        self._daily_pnl: float = 0.0
        self._halted: bool = False
        self._today: date = date.today()

        logger.info(
            "RiskManager 초기화 | 단타 손절:{:.0%}/익절:{:.0%} | 장투 손절:{:.0%}/익절:{:.0%}",
            abs(self._cfg["stop_loss_pct"]),
            self._cfg["take_profit_pct"],
            abs(self._cfg_long["stop_loss_pct"]),
            self._cfg_long["take_profit_pct"],
        )

    # ── 퍼블릭 API ────────────────────────────

    def check_buy(
        self,
        ticker: str,
        price: float,
        confidence: int,
        available_cash: float,
        style: str = STYLE_DAY,
    ) -> RiskCheckResult:
        """매수 가능 여부 검사. style='daytrading'|'longterm'"""
        self._reset_if_new_day()
        cfg = self._cfg if style == STYLE_DAY else self._cfg_long

        if self._halted:
            return RiskCheckResult(False, "일일 손실 한도 초과로 거래 중단")

        if confidence < cfg["min_confidence"]:
            return RiskCheckResult(
                False,
                f"AI 신뢰도 부족 ({confidence} < {cfg['min_confidence']})",
            )

        # 스타일별 보유 종목 수 체크
        style_count = sum(1 for p in self._positions.values() if p.style == style)
        if style_count >= cfg["max_positions"]:
            return RiskCheckResult(
                False,
                f"최대 보유 종목 수 초과 [{style}] ({style_count}/{cfg['max_positions']})",
            )

        if ticker in self._positions:
            return RiskCheckResult(False, f"이미 보유 중인 종목: {ticker}")

        max_invest = min(cfg["max_invest_per_trade"], available_cash)
        qty = int(max_invest / price)
        if qty <= 0:
            return RiskCheckResult(False, f"투자금 부족 (가용:{available_cash:,.0f}원, 가격:{price:,.0f}원)")

        adjusted = (qty * price) < cfg["max_invest_per_trade"]
        return RiskCheckResult(allowed=True, reason="리스크 검사 통과", qty=qty, adjusted=adjusted)

    def check_sell(self, ticker: str) -> RiskCheckResult:
        """매도 가능 여부 검사 (보유 확인)"""
        self._reset_if_new_day()

        if ticker not in self._positions:
            return RiskCheckResult(False, f"미보유 종목 매도 시도: {ticker}")

        pos = self._positions[ticker]
        return RiskCheckResult(True, "매도 가능", qty=pos.qty)

    def check_stop_loss(self, ticker: str, current_price: float) -> bool:
        """포지션 스타일에 맞는 손절선 비교"""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        cfg = self._cfg if pos.style == STYLE_DAY else self._cfg_long
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct <= cfg["stop_loss_pct"]:
            logger.warning(
                "손절 발동 [{}] {}: 진입:{:.2f} → 현재:{:.2f} | {:.2%}",
                pos.style, ticker, pos.avg_price, current_price, pnl_pct,
            )
            return True
        return False

    def check_take_profit(self, ticker: str, current_price: float) -> bool:
        """포지션 스타일에 맞는 익절선 비교"""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        cfg = self._cfg if pos.style == STYLE_DAY else self._cfg_long
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct >= cfg["take_profit_pct"]:
            logger.info(
                "익절 발동 [{}] {}: 진입:{:.2f} → 현재:{:.2f} | {:.2%}",
                pos.style, ticker, pos.avg_price, current_price, pnl_pct,
            )
            return True
        return False

    def get_positions_by_style(self, style: str) -> dict[str, Position]:
        """단타 또는 장투 포지션만 반환"""
        return {t: p for t, p in self._positions.items() if p.style == style}

    # ── 포지션 업데이트 ───────────────────────

    def add_position(self, ticker: str, name: str, qty: int, price: float,
                     style: str = STYLE_DAY) -> None:
        self._positions[ticker] = Position(
            ticker=ticker, name=name, qty=qty, avg_price=price, style=style
        )
        logger.info("포지션 추가 [{}]: {} x{}주 @{:.2f}", style, ticker, qty, price)

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
