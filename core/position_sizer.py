"""
core/position_sizer.py — 포지션 사이징 고도화

방법론:
  1. Kelly Criterion   — 기대값 기반 최적 투자 비중
  2. ATR 기반 손절     — 변동성 적응형 손절선 자동 계산
  3. 분할 Kelly        — Full Kelly의 1/4 (안전 마진)
  4. 리스크 한도 체크   — RISK_CONFIG 하드 상한 적용

사용 예:
    sizer  = PositionSizer(risk_manager)
    result = sizer.calc(snap, verdict, available_cash)
    print(result.qty, result.stop_loss, result.reason)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from config import DB_PATH, LONG_RISK_CONFIG, RISK_CONFIG
from core.data_collector import StockSnapshot
from core.risk_manager import STYLE_DAY


# ── 결과 구조 ─────────────────────────────────

@dataclass
class SizingResult:
    qty:            int        # 매수 수량
    invest_amount:  float      # 총 투자금액
    stop_loss:      float      # ATR 기반 손절가
    stop_loss_pct:  float      # 손절 % (계산값)
    kelly_fraction: float      # 계산된 Kelly 비중
    method:         str        # 사용된 방법
    reason:         str        # 상세 설명

    @property
    def is_valid(self) -> bool:
        return self.qty > 0


# ── 포지션 사이저 ─────────────────────────────

class PositionSizer:
    """
    Kelly Criterion + ATR 기반 포지션 사이징.

    - Full Kelly가 아닌 1/4 Kelly 사용 (과도한 레버리지 방지)
    - ATR 2배를 손절선으로 사용 (변동성 반영)
    - RISK_CONFIG 하드 한도 적용 (절대 초과 불가)
    """

    # 보수적 단타용 Kelly 계수
    KELLY_FRACTION = 0.15

    # ATR 손절 배수 (1.5 = 타이트한 단타 손절)
    ATR_MULTIPLIER = 1.5

    # Kelly 비중 최대치 (20% 초과 금지 — 보수적 단타)
    MAX_KELLY_FRACTION = 0.20

    def __init__(self, risk_manager=None) -> None:
        self._rm = risk_manager
        # 과거 거래 기록에서 승률·손익비 로드
        self._win_rate, self._payoff = self._load_historical_stats()

    # ── 퍼블릭 API ────────────────────────────

    def calc(
        self,
        snap:           StockSnapshot,
        confidence:     int,
        available_cash: float,
        style:          str = STYLE_DAY,
        ai_win_rate:    Optional[float] = None,   # AI가 제공한 승률 (없으면 역사적 값 사용)
        ai_payoff:      Optional[float] = None,   # AI가 제공한 손익비
    ) -> SizingResult:
        """
        최적 포지션 크기를 계산한다.

        confidence: AI 신뢰도 (0~100) — 높을수록 큰 포지션
        available_cash: 사용 가능 현금
        """
        cfg   = RISK_CONFIG if style == STYLE_DAY else LONG_RISK_CONFIG
        price = snap.current_price
        atr   = getattr(snap, "atr", price * 0.015)  # ATR 없으면 1.5% 가정

        # 1. ATR 기반 손절가 계산
        atr_stop_price = price - self.ATR_MULTIPLIER * atr
        atr_stop_pct   = (atr_stop_price - price) / price  # 음수

        # 손절선을 RISK_CONFIG 최소값으로 clip
        config_stop_pct = cfg["stop_loss_pct"]  # 예: -0.03
        stop_pct        = max(atr_stop_pct, config_stop_pct)   # 더 좁은 쪽
        stop_price      = round(price * (1 + stop_pct), 2)

        # 2. Kelly Criterion 계산
        win_rate = ai_win_rate or self._win_rate
        payoff   = ai_payoff   or self._payoff
        kelly    = self._calc_kelly(win_rate, payoff, confidence)

        # 3. 투자금 결정
        # 소액 자본(<2백만원)일 때는 0.20 cap이 너무 빡빡해 1주도 못 사는 경우가 생긴다.
        # 자본이 작으면 cap을 풀어 max_invest_per_trade까지 허용.
        kelly_amount = available_cash * kelly
        max_amount   = cfg["max_invest_per_trade"]
        if available_cash < 2_000_000:
            cap = min(max_amount, available_cash * 0.95)
        else:
            cap = min(max_amount, available_cash * 0.20)

        # 1주는 살 수 있게 floor 보장 (가격이 cap 이내일 때만)
        floor = price if price <= cap else 0
        invest_amount = max(kelly_amount, floor)
        invest_amount = min(invest_amount, cap)
        invest_amount = max(invest_amount, 0)

        # 4. 수량 계산
        qty = int(invest_amount / price)

        method = f"1/4 Kelly({kelly:.1%}) + ATR손절({stop_pct:.1%})"
        reason = (
            f"승률:{win_rate:.0%} | 손익비:{payoff:.1f}배 | "
            f"Kelly:{kelly:.1%} → Kelly금액:{kelly_amount:,.0f} / "
            f"floor(1주):{floor:,.0f} / cap:{cap:,.0f} → 투자:{invest_amount:,.0f}원 | "
            f"손절:{stop_pct:.1%}(ATR x{self.ATR_MULTIPLIER})"
        )

        logger.info(
            "포지션 사이징 [{}]: {}주 @{:,} | {}",
            snap.ticker, qty, price, method,
        )

        return SizingResult(
            qty           = qty,
            invest_amount = qty * price,
            stop_loss     = stop_price,
            stop_loss_pct = stop_pct * 100,
            kelly_fraction= kelly,
            method        = method,
            reason        = reason,
        )

    def calc_position_size_label(self, kelly: float) -> str:
        """Kelly 비중에 따라 SMALL/MEDIUM/LARGE 반환"""
        if kelly < 0.05:  return "SMALL"
        if kelly < 0.12:  return "MEDIUM"
        return "LARGE"

    # ── Kelly Criterion ───────────────────────

    def _calc_kelly(
        self, win_rate: float, payoff: float, confidence: int
    ) -> float:
        """
        Kelly = (p * b - q) / b
        p: 승률, b: 손익비, q: 패률(1-p)
        confidence 가중치: 70점 → 1.0, 90점 → 1.3, 50점 → 0.5
        """
        if payoff <= 0 or win_rate <= 0:
            return 0.0

        p   = win_rate
        q   = 1 - p
        b   = payoff
        raw = (p * b - q) / b  # Full Kelly

        if raw <= 0:
            return 0.0

        # 신뢰도 가중치 (70점=기준, ±1%/점)
        conf_weight = 1.0 + (confidence - 70) * 0.01
        conf_weight = max(0.3, min(2.0, conf_weight))

        # 1/4 Kelly 적용 후 상한 clip
        quarter = raw * self.KELLY_FRACTION * conf_weight
        return round(min(quarter, self.MAX_KELLY_FRACTION), 4)

    # ── 역사적 통계 로드 ─────────────────────

    @staticmethod
    def _load_historical_stats() -> tuple[float, float]:
        """
        DB 거래 내역에서 승률과 손익비를 계산한다.
        데이터 부족 시 보수적 기본값 사용.
        """
        DEFAULT_WIN_RATE = 0.50   # 기본 승률 50%
        DEFAULT_PAYOFF   = 2.0    # 기본 손익비 2:1

        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT order_type, qty, price FROM orders "
                    "WHERE status IN ('FILLED','PAPER_FILLED') "
                    "ORDER BY timestamp"
                ).fetchall()
        except Exception:
            return DEFAULT_WIN_RATE, DEFAULT_PAYOFF

        if len(rows) < 10:
            return DEFAULT_WIN_RATE, DEFAULT_PAYOFF

        # 매수→매도 쌍으로 손익 계산
        buys: dict[str, list] = {}
        wins, losses = [], []

        for otype, qty, price in rows:
            if otype == "BUY":
                buys.setdefault("pending", []).append((qty, price))
            elif otype == "SELL" and buys.get("pending"):
                buy_qty, buy_price = buys["pending"].pop(0)
                pnl = (price - buy_price) * min(qty, buy_qty)
                (wins if pnl > 0 else losses).append(abs(pnl))

        total = len(wins) + len(losses)
        if total < 5:
            return DEFAULT_WIN_RATE, DEFAULT_PAYOFF

        win_rate = len(wins) / total
        avg_win  = sum(wins)  / len(wins)  if wins   else 1
        avg_loss = sum(losses)/ len(losses) if losses else 1
        payoff   = avg_win / avg_loss if avg_loss > 0 else DEFAULT_PAYOFF

        logger.debug(
            "역사적 통계 로드: 승률={:.0%} | 손익비={:.2f} ({}건)",
            win_rate, payoff, total,
        )
        return round(win_rate, 3), round(payoff, 3)
