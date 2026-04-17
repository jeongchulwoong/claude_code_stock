"""
backtest/engine.py — 백테스팅 핵심 엔진

이벤트 기반 시뮬레이션:
  - 매일 전략 조건 확인 → 매수/매도 판단
  - 슬리피지·수수료 적용
  - 포지션·손익 추적
  - 결과 통계 자동 계산
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger


# ── 설정 ──────────────────────────────────────

@dataclass
class BacktestConfig:
    initial_capital:     float = 10_000_000   # 초기 자본 (1000만원)
    commission_rate:     float = 0.00015       # 수수료 0.015% (키움 기준)
    slippage_rate:       float = 0.001         # 슬리피지 0.1%
    stop_loss_pct:       float = -0.03         # 손절 -3%
    take_profit_pct:     float = 0.06          # 익절 +6%
    max_positions:       int   = 5             # 최대 동시 보유 종목
    position_size_pct:   float = 0.20          # 1종목 최대 투자 비중 20%
    use_stop_loss:       bool  = True
    use_take_profit:     bool  = True


# ── 거래 기록 ─────────────────────────────────

@dataclass
class Trade:
    ticker:     str
    entry_date: date
    entry_price: float
    exit_date:  Optional[date]  = None
    exit_price: Optional[float] = None
    qty:        int   = 0
    pnl:        float = 0.0
    pnl_pct:    float = 0.0
    exit_reason: str  = ""

    @property
    def holding_days(self) -> int:
        if self.exit_date:
            return (self.exit_date - self.entry_date).days
        return 0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


# ── 백테스팅 결과 ─────────────────────────────

@dataclass
class BacktestResult:
    ticker:          str
    strategy_name:   str
    config:          BacktestConfig
    trades:          list[Trade]
    equity_curve:    pd.Series        # 날짜별 자산 추이
    daily_returns:   pd.Series        # 날짜별 수익률

    # ── 핵심 통계 ─────────────────────────────
    @property
    def total_return_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        return (self.equity_curve.iloc[-1] / self.config.initial_capital - 1) * 100

    @property
    def cagr(self) -> float:
        """연평균 수익률 (CAGR)"""
        if len(self.equity_curve) < 2:
            return 0.0
        years = len(self.equity_curve) / 252
        return ((self.equity_curve.iloc[-1] / self.config.initial_capital) ** (1 / years) - 1) * 100

    @property
    def mdd(self) -> float:
        """최대 낙폭 (Maximum Drawdown)"""
        if self.equity_curve.empty:
            return 0.0
        peak   = self.equity_curve.cummax()
        dd     = (self.equity_curve - peak) / peak
        return float(dd.min() * 100)

    @property
    def sharpe_ratio(self) -> float:
        """샤프 지수 (무위험 수익률 3.5% 가정)"""
        if self.daily_returns.empty or self.daily_returns.std() == 0:
            return 0.0
        rf_daily = 0.035 / 252
        excess   = self.daily_returns - rf_daily
        return float(excess.mean() / excess.std() * np.sqrt(252))

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.exit_date]
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.is_win) / len(closed) * 100

    @property
    def profit_factor(self) -> float:
        """손익비 (총이익 / 총손실)"""
        closed  = [t for t in self.trades if t.exit_date]
        gross_win  = sum(t.pnl for t in closed if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in closed if t.pnl < 0))
        return gross_win / gross_loss if gross_loss else float("inf")

    @property
    def avg_holding_days(self) -> float:
        closed = [t for t in self.trades if t.exit_date]
        if not closed:
            return 0.0
        return sum(t.holding_days for t in closed) / len(closed)

    @property
    def total_trades(self) -> int:
        return len([t for t in self.trades if t.exit_date])

    def summary(self) -> dict:
        return {
            "종목":          self.ticker,
            "전략":          self.strategy_name,
            "총 수익률":     f"{self.total_return_pct:+.2f}%",
            "CAGR":          f"{self.cagr:+.2f}%",
            "MDD":           f"{self.mdd:.2f}%",
            "샤프지수":      f"{self.sharpe_ratio:.2f}",
            "승률":          f"{self.win_rate:.1f}%",
            "손익비":        f"{self.profit_factor:.2f}",
            "총 거래 수":    self.total_trades,
            "평균 보유일":   f"{self.avg_holding_days:.1f}일",
            "최종 자산":     f"{self.equity_curve.iloc[-1]:,.0f}원",
        }


# ── 백테스팅 엔진 ─────────────────────────────

class BacktestEngine:
    """
    이벤트 기반 백테스팅 엔진.

    사용 예:
        engine = BacktestEngine(config)
        result = engine.run(df, strategy_fn, "005930", "모멘텀")
    """

    def __init__(self, config: BacktestConfig = None) -> None:
        self._cfg = config or BacktestConfig()

    def run(
        self,
        df: pd.DataFrame,
        strategy_fn: Callable[[pd.DataFrame, int], str],
        ticker:       str = "UNKNOWN",
        strategy_name: str = "unnamed",
    ) -> BacktestResult:
        """
        백테스팅을 실행한다.

        strategy_fn(df, i) → "BUY" | "SELL" | "HOLD"
        df: BacktestDataLoader.load()의 결과
        i : 현재 행 인덱스 (look-ahead 방지)
        """
        cfg       = self._cfg
        cash      = cfg.initial_capital
        position: Optional[Trade] = None
        trades:   list[Trade]     = []
        equity:   list[float]     = []

        logger.info("백테스팅 시작: {} | {} | {}행", ticker, strategy_name, len(df))

        for i in range(60, len(df)):   # 지표 준비 기간(60일) 이후부터
            row       = df.iloc[i]
            today     = df.index[i].date()
            price     = float(row["close"])
            buy_price = price * (1 + cfg.slippage_rate)
            sell_price= price * (1 - cfg.slippage_rate)

            # ── 손절·익절 체크 (전략 판단 전) ────
            if position:
                pnl_pct = (price - position.entry_price) / position.entry_price
                if cfg.use_stop_loss and pnl_pct <= cfg.stop_loss_pct:
                    cash, position = self._close(
                        position, today, sell_price, cash, trades, "손절"
                    )
                elif cfg.use_take_profit and pnl_pct >= cfg.take_profit_pct:
                    cash, position = self._close(
                        position, today, sell_price, cash, trades, "익절"
                    )

            # ── 전략 신호 ─────────────────────────
            signal = strategy_fn(df, i)

            if signal == "BUY" and position is None:
                invest = min(
                    cash * cfg.position_size_pct,
                    cfg.initial_capital * cfg.position_size_pct,
                )
                qty = int(invest / buy_price)
                if qty > 0:
                    cost      = qty * buy_price * (1 + cfg.commission_rate)
                    cash     -= cost
                    position  = Trade(
                        ticker      = ticker,
                        entry_date  = today,
                        entry_price = buy_price,
                        qty         = qty,
                    )

            elif signal == "SELL" and position:
                cash, position = self._close(
                    position, today, sell_price, cash, trades, "전략 매도"
                )

            # ── 자산 추적 ─────────────────────────
            pos_value = (position.qty * price) if position else 0
            equity.append(cash + pos_value)

        # 마지막 포지션 강제 청산
        if position:
            last_price = float(df.iloc[-1]["close"]) * (1 - cfg.slippage_rate)
            cash, _ = self._close(position, df.index[-1].date(), last_price, cash, trades, "기간 종료")

        equity_series = pd.Series(
            equity, index=df.index[60:60 + len(equity)]
        )
        daily_returns = equity_series.pct_change().dropna()

        result = BacktestResult(
            ticker        = ticker,
            strategy_name = strategy_name,
            config        = cfg,
            trades        = trades,
            equity_curve  = equity_series,
            daily_returns = daily_returns,
        )

        self._log_result(result)
        return result

    # ── 포지션 청산 헬퍼 ─────────────────────

    @staticmethod
    def _close(
        pos: Trade,
        exit_date: date,
        exit_price: float,
        cash: float,
        trades: list[Trade],
        reason: str,
    ):
        commission = pos.qty * exit_price * 0.00015
        proceeds   = pos.qty * exit_price - commission
        cost_basis = pos.qty * pos.entry_price

        pos.exit_date  = exit_date
        pos.exit_price = exit_price
        pos.pnl        = proceeds - cost_basis
        pos.pnl_pct    = (exit_price - pos.entry_price) / pos.entry_price * 100
        pos.exit_reason= reason
        trades.append(pos)

        return cash + proceeds, None

    @staticmethod
    def _log_result(result: BacktestResult) -> None:
        s = result.summary()
        logger.info("─" * 45)
        logger.info("백테스팅 완료: {} [{}]", result.ticker, result.strategy_name)
        for k, v in s.items():
            logger.info("  {:12} {}", k, v)
        logger.info("─" * 45)
