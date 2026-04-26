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
from core.daytrade_journal import DayTradeJournal


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
    high_price:  float = 0.0                 # 보유 중 최고가 (트레일링 스탑용)
    atr_at_entry: float = 0.0                # 진입 시 ATR(원) — 동적 SL/TP 기준
    converted:   bool  = False               # 단타→장투 전환 여부

    def __post_init__(self):
        if self.high_price == 0.0:
            self.high_price = self.avg_price

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
        self._consec_losses: int = 0               # 연패 카운터 (일일 리셋)
        self._start_of_day_capital: float = 0.0    # 당일 시작 자본 (% halt 기준)

        logger.info(
            "RiskManager 초기화 | 단타 SL:{:.1f}×ATR/TP:{:.1f}×ATR | 장투 손절:{:.0%}/익절:{:.0%}",
            self._cfg.get("stop_loss_atr_mult", 1.5),
            self._cfg.get("take_profit_atr_mult", 3.0),
            abs(self._cfg_long["stop_loss_pct"]),
            self._cfg_long["take_profit_pct"],
        )

    def set_start_capital(self, cap: float) -> None:
        """당일 시작 자본 기록 (% 기반 손실 한도·리스크 사이징 기준점)"""
        self._start_of_day_capital = float(cap or 0)
        logger.info("당일 시작 자본 등록: {:,.0f}원", self._start_of_day_capital)

    # ── 퍼블릭 API ────────────────────────────

    def check_buy(
        self,
        ticker: str,
        price: float,
        confidence: int,
        available_cash: float,
        style: str = STYLE_DAY,
        atr: float = 0.0,
    ) -> RiskCheckResult:
        """매수 가능 여부 검사. ATR 이 주어지면 risk-per-trade 사이징 적용."""
        self._reset_if_new_day()
        cfg = self._cfg if style == STYLE_DAY else self._cfg_long

        if self._halted:
            return RiskCheckResult(False, "일일 손실 한도 초과로 거래 중단")

        if confidence < cfg["min_confidence"]:
            return RiskCheckResult(False, f"AI 신뢰도 부족 ({confidence} < {cfg['min_confidence']})")

        # 연패 서킷브레이커
        max_consec = cfg.get("consecutive_loss_halt", 3)
        if self._consec_losses >= max_consec:
            return RiskCheckResult(False, f"연패 {self._consec_losses}회 — 당일 매수 중단")

        style_count = sum(1 for p in self._positions.values() if p.style == style)
        if style_count >= cfg["max_positions"]:
            return RiskCheckResult(False,
                f"최대 보유 종목 수 초과 [{style}] ({style_count}/{cfg['max_positions']})")

        if ticker in self._positions:
            return RiskCheckResult(False, f"이미 보유 중인 종목: {ticker}")

        # 섹터 중복 차단 (상관관계 회피)
        if cfg.get("sector_overlap_block", False):
            try:
                from core.sector_map import has_sector_overlap, get_sector
                same_style_tickers = [t for t,p in self._positions.items() if p.style == style]
                overlap, conflict = has_sector_overlap(ticker, same_style_tickers)
                if overlap:
                    return RiskCheckResult(
                        False,
                        f"섹터 중복 차단: {get_sector(ticker)} 섹터에 이미 {conflict} 보유 중"
                    )
            except Exception:
                pass

        # ── 사이징 — min(risk-per-trade, slot-cash) ───────────
        style_capital_limit = float(cfg.get("capital_limit", 0) or 0)
        capital_base = self._start_of_day_capital or available_cash
        if style_capital_limit > 0:
            capital_base = min(capital_base, style_capital_limit)
        capital = max(0.0, min(capital_base, available_cash if available_cash > 0 else capital_base))
        risk_pct = cfg.get("risk_per_trade_pct", 0.005)
        risk_amount = capital * risk_pct

        slots_left = max(1, cfg["max_positions"] - style_count)
        cash_budget = (available_cash / slots_left) * 0.95
        cash_budget = min(cash_budget, cfg["max_invest_per_trade"])

        # 소액 자본 보호: 슬롯 분할이 1주 가격보다 작으면 가용현금 95%로 폴백
        # (max_invest_per_trade 이내, 1주 매수 가능 보장)
        if cash_budget < price <= min(cfg["max_invest_per_trade"], available_cash * 0.95):
            cash_budget = min(available_cash * 0.95, cfg["max_invest_per_trade"])

        if style == STYLE_DAY and atr > 0:
            # 손절폭 = ATR × multiplier. 리스크 금액 / 손절폭 = 수량
            sl_mult = cfg.get("stop_loss_atr_mult", 1.5)
            stop_dist = atr * sl_mult
            if stop_dist <= 0:
                return RiskCheckResult(False, "ATR 계산 오류 (stop_dist=0)")
            qty_by_risk = int(risk_amount / stop_dist)
            qty_by_cash = int(cash_budget / price)
            qty = min(qty_by_risk, qty_by_cash)
            # 소액 자본일 때 risk_amount 가 손절폭보다 작아 qty_by_risk=0 인 케이스 구제
            if qty == 0 and qty_by_cash >= 1 and capital < 2_000_000:
                qty = 1
                reason = (f"ATR사이징(소액보정) | ATR:{atr:.0f} / 손절폭:{stop_dist:.0f} "
                          f"/ 리스크:{risk_amount:,.0f} → qty={qty} (risk={qty_by_risk}, cash={qty_by_cash})")
            else:
                reason = (f"ATR사이징 통과 | ATR:{atr:.0f}원 / 손절폭:{stop_dist:.0f}원 "
                          f"/ 리스크:{risk_amount:,.0f}원 → qty={qty} "
                          f"(risk:{qty_by_risk}, cash:{qty_by_cash})")
        else:
            # ATR 없거나 장투 — cash-budget 기반 단순 사이징
            qty = int(cash_budget / price)
            reason = f"캐시 사이징 통과 | 슬롯:{cash_budget:,.0f}원 → qty={qty}"

        if qty <= 0:
            return RiskCheckResult(
                False,
                f"수량 0 — 가용:{available_cash:,.0f}원 / 슬롯예산:{cash_budget:,.0f}원 "
                f"/ 단가:{price:,.0f}원 / 리스크:{risk_amount:,.0f}원 / ATR:{atr:.0f}"
            )
        return RiskCheckResult(allowed=True, reason=reason, qty=qty, adjusted=False)

    def check_sell(self, ticker: str) -> RiskCheckResult:
        """매도 가능 여부 검사 (보유 확인)"""
        self._reset_if_new_day()

        if ticker not in self._positions:
            return RiskCheckResult(False, f"미보유 종목 매도 시도: {ticker}")

        pos = self._positions[ticker]
        return RiskCheckResult(True, "매도 가능", qty=pos.qty)

    def check_stop_loss(self, ticker: str, current_price: float) -> bool:
        """손절선 비교 (단타=ATR 기반, 장투=% 기반). 트레일링 스탑 포함."""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        cfg = self._cfg if pos.style == STYLE_DAY else self._cfg_long

        if current_price > pos.high_price:
            pos.high_price = current_price

        if pos.style == STYLE_DAY and pos.atr_at_entry > 0:
            # ATR 기반 손절: 진입가 - ATR×mult
            sl_mult = cfg.get("stop_loss_atr_mult", 1.5)
            stop_line = pos.avg_price - pos.atr_at_entry * sl_mult
            if current_price <= stop_line:
                logger.warning(
                    "ATR 손절 [{}] {}: 진입:{:.0f} → 현재:{:.0f} | 손절선:{:.0f} (ATR×{:.1f})",
                    pos.style, ticker, pos.avg_price, current_price, stop_line, sl_mult,
                )
                return True

            # ATR 트레일링: 고점 - ATR×trail_mult 하회
            trail_mult = cfg.get("trailing_stop_atr_mult", 1.2)
            trail_start_mult = cfg.get("trailing_start_atr_mult", 2.0)
            peak_gain = pos.high_price - pos.avg_price
            if peak_gain >= pos.atr_at_entry * trail_start_mult:
                trail_line = pos.high_price - pos.atr_at_entry * trail_mult
                if current_price <= trail_line:
                    logger.info(
                        "ATR 트레일링 [{}]: 고점:{:.0f} → 현재:{:.0f} | 트레일선:{:.0f}",
                        ticker, pos.high_price, current_price, trail_line,
                    )
                    return True
            return False

        # 장투 / ATR 없음 — 기존 % 기반
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct <= cfg["stop_loss_pct"]:
            logger.warning(
                "손절 발동 [{}] {}: 진입:{:.0f} → 현재:{:.0f} | {:.2%}",
                pos.style, ticker, pos.avg_price, current_price, pnl_pct,
            )
            return True
        return False

    def check_take_profit(self, ticker: str, current_price: float) -> bool:
        """익절선 비교 (단타=ATR×mult, 장투=% 기반)"""
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        cfg = self._cfg if pos.style == STYLE_DAY else self._cfg_long

        if pos.style == STYLE_DAY and pos.atr_at_entry > 0:
            tp_mult = cfg.get("take_profit_atr_mult", 3.0)
            tp_line = pos.avg_price + pos.atr_at_entry * tp_mult
            if current_price >= tp_line:
                logger.info(
                    "ATR 익절 [{}] {}: 진입:{:.0f} → 현재:{:.0f} | 익절선:{:.0f} (ATR×{:.1f})",
                    pos.style, ticker, pos.avg_price, current_price, tp_line, tp_mult,
                )
                return True
            return False

        pnl_pct = (current_price - pos.avg_price) / pos.avg_price
        if pnl_pct >= cfg["take_profit_pct"]:
            logger.info(
                "익절 발동 [{}] {}: 진입:{:.0f} → 현재:{:.0f} | {:.2%}",
                pos.style, ticker, pos.avg_price, current_price, pnl_pct,
            )
            return True
        return False

    def convert_to_long(self, ticker: str, reason: str = "") -> bool:
        """
        단타 → 장투 전환. 'style'만 바꾸고 손절·익절 기준이 장투로 재적용된다.
        avg_price/qty 는 유지. 'converted' 플래그 True 로 기록.
        """
        if ticker not in self._positions:
            return False
        pos = self._positions[ticker]
        if pos.style == STYLE_LONG:
            return False   # 이미 장투
        DayTradeJournal().record_conversion(
            ticker=ticker,
            qty=pos.qty,
            entry_price=pos.avg_price,
            current_price=pos.high_price or pos.avg_price,
            reason=reason,
        )
        pos.style = STYLE_LONG
        pos.converted = True
        logger.warning(
            "🔄 단타→장투 전환 [{}]: 진입 {:.0f}원 | 사유: {}",
            ticker, pos.avg_price, reason or "조건 미충족 전환",
        )
        return True

    def get_positions_by_style(self, style: str) -> dict[str, Position]:
        """단타 또는 장투 포지션만 반환"""
        return {t: p for t, p in self._positions.items() if p.style == style}

    # ── 포지션 업데이트 ───────────────────────

    def add_position(self, ticker: str, name: str, qty: int, price: float,
                     style: str = STYLE_DAY, atr: float = 0.0) -> None:
        self._positions[ticker] = Position(
            ticker=ticker, name=name, qty=qty, avg_price=price, style=style,
            atr_at_entry=float(atr or 0),
        )
        logger.info("포지션 추가 [{}]: {} x{}주 @{:,.0f} | ATR진입:{:.0f}",
                    style, ticker, qty, price, atr or 0)

    def remove_position(self, ticker: str, sell_price: float) -> Optional[float]:
        """포지션 제거 후 실현 손익을 반환한다."""
        if ticker not in self._positions:
            logger.warning("포지션 없음: {}", ticker)
            return None
        pos = self._positions.pop(ticker)
        pnl = (sell_price - pos.avg_price) * pos.qty
        self._daily_pnl += pnl

        # 연패 카운터 (단타만 카운트)
        if pos.style == STYLE_DAY:
            if pnl < 0:
                self._consec_losses += 1
            else:
                self._consec_losses = 0

        logger.info(
            "포지션 청산: {} | 손익:{:+,.0f}원 | 일누계:{:+,.0f}원 | 연패:{}",
            ticker, pnl, self._daily_pnl, self._consec_losses,
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
        # % 기반 한도 우선, 없으면 원 기반 한도 사용
        pct_limit = self._cfg.get("daily_loss_limit_pct")
        if pct_limit is not None and self._start_of_day_capital > 0:
            abs_limit = self._start_of_day_capital * pct_limit   # pct_limit은 -0.02 형태
            if self._daily_pnl <= abs_limit:
                self._halted = True
                logger.critical(
                    "⛔ 일일 손실 한도(%) 초과! 손실:{:+,.0f}원 ({:.2%} of {:,.0f})",
                    self._daily_pnl, self._daily_pnl / self._start_of_day_capital,
                    self._start_of_day_capital,
                )
                return
        if self._daily_pnl <= self._cfg["daily_loss_limit"]:
            self._halted = True
            logger.critical(
                "⛔ 일일 손실 한도 초과! 거래 자동 중단 | 손실:{:+,.0f}원",
                self._daily_pnl,
            )

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._today:
            logger.info("날짜 변경 — 일일 손익·중단·연패 초기화")
            self._today = today
            self._daily_pnl = 0.0
            self._halted = False
            self._consec_losses = 0
