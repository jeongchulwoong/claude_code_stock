"""
backtest/optimizer.py — 전략 파라미터 그리드 서치 최적화

예: RSI 기준값 20~40 / 거래량 배수 1.5~3.0 조합을 모두 테스트하여
샤프지수 기준 최적 파라미터를 탐색한다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd
from loguru import logger

from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult


@dataclass
class OptimizeResult:
    best_params:  dict[str, Any]
    best_sharpe:  float
    best_result:  BacktestResult
    all_results:  pd.DataFrame    # 전체 파라미터 조합 결과표


class StrategyOptimizer:
    """
    그리드 서치 기반 전략 파라미터 최적화.
    
    사용 예:
        optimizer = StrategyOptimizer(df, "005930")
        result = optimizer.optimize_momentum()
        print(result.best_params)
        print(result.all_results)
    """

    def __init__(
        self,
        df:     pd.DataFrame,
        ticker: str,
        config: BacktestConfig = None,
    ) -> None:
        self._df     = df
        self._ticker = ticker
        self._cfg    = config or BacktestConfig()
        self._engine = BacktestEngine(self._cfg)

    # ── 모멘텀 전략 최적화 ────────────────────

    def optimize_momentum(
        self,
        rsi_range:    tuple = (20, 40, 5),      # (시작, 끝, 스텝)
        vol_range:    tuple = (1.5, 3.5, 0.5),
        metric:       str   = "sharpe",          # 최적화 기준
    ) -> OptimizeResult:
        """
        RSI 기준값 × 거래량 배수 그리드 서치.
        """
        import numpy as np

        rsi_values = np.arange(*rsi_range)
        vol_values = np.arange(*vol_range)
        params_grid = list(itertools.product(rsi_values, vol_values))

        logger.info(
            "모멘텀 최적화 시작: {} 조합 | 종목: {}",
            len(params_grid), self._ticker
        )

        rows = []
        best_score  = -999.0
        best_result = None
        best_params = {}

        for rsi_th, vol_th in params_grid:
            rsi_th = float(rsi_th)
            vol_th = float(vol_th)

            def strategy_fn(df, i, r=rsi_th, v=vol_th):
                row = df.iloc[i]
                if (
                    row["rsi"]       < r and
                    row["vol_ratio"] >= v and
                    row["ma5"]       > row["ma20"] and
                    row["macd_cross"]
                ):
                    return "BUY"
                if row["rsi"] > 70:
                    return "SELL"
                return "HOLD"

            result = self._engine.run(
                self._df, strategy_fn, self._ticker,
                f"momentum(rsi<{rsi_th:.0f},vol>{vol_th:.1f})"
            )

            score = self._get_metric(result, metric)
            rows.append({
                "rsi_threshold":  rsi_th,
                "vol_threshold":  vol_th,
                "total_return":   result.total_return_pct,
                "cagr":           result.cagr,
                "mdd":            result.mdd,
                "sharpe":         result.sharpe_ratio,
                "win_rate":       result.win_rate,
                "profit_factor":  result.profit_factor,
                "total_trades":   result.total_trades,
                metric:           score,
            })

            if score > best_score and result.total_trades >= 3:
                best_score  = score
                best_result = result
                best_params = {"rsi_threshold": rsi_th, "vol_threshold": vol_th}

        all_df = pd.DataFrame(rows).sort_values(metric, ascending=False)

        logger.info(
            "최적 파라미터: {} | {}={:.3f}",
            best_params, metric, best_score
        )

        return OptimizeResult(
            best_params  = best_params,
            best_sharpe  = best_score,
            best_result  = best_result,
            all_results  = all_df,
        )

    # ── RSI 역추세 최적화 ────────────────────

    def optimize_rsi(
        self,
        buy_range:  tuple = (20, 35, 5),
        sell_range: tuple = (50, 75, 5),
        metric:     str   = "sharpe",
    ) -> OptimizeResult:
        """
        RSI 매수 기준값 × 매도 기준값 그리드 서치.
        """
        import numpy as np

        buy_vals  = np.arange(*buy_range)
        sell_vals = np.arange(*sell_range)
        params_grid = [(b, s) for b, s in itertools.product(buy_vals, sell_vals) if s > b + 15]

        logger.info("RSI 최적화 시작: {} 조합", len(params_grid))

        rows = []
        best_score  = -999.0
        best_result = None
        best_params = {}

        for buy_th, sell_th in params_grid:
            buy_th  = float(buy_th)
            sell_th = float(sell_th)

            def strategy_fn(df, i, b=buy_th, s=sell_th):
                row = df.iloc[i]
                if row["rsi"] < b:
                    return "BUY"
                if row["rsi"] > s:
                    return "SELL"
                return "HOLD"

            result = self._engine.run(
                self._df, strategy_fn, self._ticker,
                f"rsi(buy<{buy_th:.0f},sell>{sell_th:.0f})"
            )

            score = self._get_metric(result, metric)
            rows.append({
                "buy_threshold":  buy_th,
                "sell_threshold": sell_th,
                "total_return":   result.total_return_pct,
                "cagr":           result.cagr,
                "mdd":            result.mdd,
                "sharpe":         result.sharpe_ratio,
                "win_rate":       result.win_rate,
                "total_trades":   result.total_trades,
            })

            if score > best_score and result.total_trades >= 3:
                best_score  = score
                best_result = result
                best_params = {"buy_threshold": buy_th, "sell_threshold": sell_th}

        all_df = pd.DataFrame(rows).sort_values(metric, ascending=False)

        return OptimizeResult(
            best_params  = best_params,
            best_sharpe  = best_score,
            best_result  = best_result,
            all_results  = all_df,
        )

    # ── 메트릭 선택 ──────────────────────────

    @staticmethod
    def _get_metric(result: BacktestResult, metric: str) -> float:
        mapping = {
            "sharpe":       result.sharpe_ratio,
            "total_return": result.total_return_pct,
            "cagr":         result.cagr,
            "mdd":          -abs(result.mdd),   # MDD는 낮을수록 좋으므로 부호 반전
            "win_rate":     result.win_rate,
            "profit_factor":result.profit_factor,
        }
        return mapping.get(metric, result.sharpe_ratio)
