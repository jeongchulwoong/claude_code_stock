"""
backtest/run_backtest.py — 백테스팅 실행 스크립트

실행:
    python backtest/run_backtest.py
    python backtest/run_backtest.py --optimize
    python backtest/run_backtest.py --tickers 005930 000660 AAPL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from backtest.data_loader import BacktestDataLoader
from backtest.engine import BacktestConfig, BacktestEngine
from backtest.optimizer import StrategyOptimizer
from backtest.report import (
    compare_strategies,
    generate_html_report,
    print_summary,
    save_trades_csv,
)
from backtest.strategies import STRATEGY_REGISTRY

# ── 로깅 ──────────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def run_all_strategies(
    tickers: list[str],
    start:   str = "2020-01-01",
    end:     str = "2024-12-31",
) -> None:
    """지정 종목에 대해 모든 전략을 실행하고 비교한다."""

    loader = BacktestDataLoader(use_cache=True)
    config = BacktestConfig(
        initial_capital   = 10_000_000,
        commission_rate   = 0.00015,
        slippage_rate     = 0.001,
        stop_loss_pct     = -0.03,
        take_profit_pct   = 0.06,
        position_size_pct = 0.20,
    )
    engine  = BacktestEngine(config)
    results = []

    for ticker in tickers:
        logger.info("=" * 50)
        logger.info("종목 로드: {}", ticker)
        try:
            df = loader.load(ticker, start=start, end=end)
        except Exception as e:
            logger.error("데이터 로드 실패 [{}]: {}", ticker, e)
            continue

        for name, fn in STRATEGY_REGISTRY.items():
            try:
                result = engine.run(df, fn, ticker, name)
                print_summary(result)
                results.append(result)
            except Exception as e:
                logger.error("백테스팅 실패 [{}/{}]: {}", ticker, name, e)

    if results:
        compare_strategies(results)
        html_path = generate_html_report(results)
        csv_path  = save_trades_csv(results)
        logger.info("리포트 저장: {}", html_path)
        logger.info("CSV 저장:   {}", csv_path)
    else:
        logger.warning("실행 결과 없음")


def run_optimization(
    ticker: str,
    start:  str = "2020-01-01",
    end:    str = "2024-12-31",
) -> None:
    """단일 종목에 대해 파라미터 최적화를 실행한다."""

    loader = BacktestDataLoader(use_cache=True)
    config = BacktestConfig(initial_capital=10_000_000)

    logger.info("파라미터 최적화 시작: {} ({} ~ {})", ticker, start, end)

    try:
        df = loader.load(ticker, start=start, end=end)
    except Exception as e:
        logger.error("데이터 로드 실패: {}", e)
        return

    optimizer = StrategyOptimizer(df, ticker, config)

    # ── 모멘텀 최적화 ─────────────────────────
    logger.info("── 모멘텀 전략 최적화 ──")
    mom_opt = optimizer.optimize_momentum(
        rsi_range = (20, 45, 5),
        vol_range = (1.5, 3.5, 0.5),
        metric    = "sharpe",
    )
    logger.info("최적 파라미터: {}", mom_opt.best_params)
    logger.info("샤프지수:      {:.3f}", mom_opt.best_sharpe)
    print("\n[모멘텀 최적화 상위 10개 조합]")
    print(mom_opt.all_results.head(10).to_string(index=False))

    # ── RSI 역추세 최적화 ─────────────────────
    logger.info("── RSI 역추세 전략 최적화 ──")
    rsi_opt = optimizer.optimize_rsi(
        buy_range  = (20, 35, 5),
        sell_range = (50, 75, 5),
        metric     = "sharpe",
    )
    logger.info("최적 파라미터: {}", rsi_opt.best_params)
    print("\n[RSI 최적화 상위 10개 조합]")
    print(rsi_opt.all_results.head(10).to_string(index=False))

    # 최적 결과로 HTML 리포트 생성
    if mom_opt.best_result and rsi_opt.best_result:
        html_path = generate_html_report(
            [mom_opt.best_result, rsi_opt.best_result],
            filename=f"optimize_{ticker}.html",
        )
        logger.info("최적화 리포트: {}", html_path)


# ── CLI 진입점 ────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI 주식 백테스팅 실행기")
    parser.add_argument(
        "--tickers", nargs="+",
        default=["005930", "000660", "035420"],
        help="종목코드 목록 (기본: 삼성전자, SK하이닉스, NAVER)",
    )
    parser.add_argument("--start",    default="2020-01-01")
    parser.add_argument("--end",      default="2024-12-31")
    parser.add_argument("--optimize", action="store_true",
                        help="파라미터 최적화 모드 (첫 번째 종목만)")
    args = parser.parse_args()

    if args.optimize:
        run_optimization(args.tickers[0], args.start, args.end)
    else:
        run_all_strategies(args.tickers, args.start, args.end)


if __name__ == "__main__":
    main()
