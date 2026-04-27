"""
backtest/composite_backtest.py — 통합 점수 기반 백테스트

매일 종가에 통합 스크리너 돌려서 70+ 진입 → ATR 기반 SL/TP 청산.
거래비용·세금 모두 반영. 셋업 타입별 ROI 분석.

사용:
    python -m backtest.composite_backtest --period 6mo --tickers 005930.KS,000660.KS
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class CompositeBacktestResult:
    total_trades:    int
    winrate:         float
    avg_pnl_pct:     float
    total_pnl_pct:   float
    max_drawdown:    float
    by_setup:        dict
    avg_hold_days:   float
    by_month:        dict


class CompositeBacktest:
    """통합 점수 ≥ 70 진입 → ATR 손절·익절 청산. 거래비용 반영."""

    COMMISSION = 0.00015     # 매수+매도 각각
    SELL_TAX   = 0.0020      # 매도만
    SLIPPAGE   = 0.001       # 0.1%

    def __init__(self, atr_sl_mult: float = 1.5, atr_tp_mult: float = 3.0,
                 max_hold_days: int = 10):
        self.sl_mult = atr_sl_mult
        self.tp_mult = atr_tp_mult
        self.max_hold = max_hold_days

    def run(self, ticker: str, period: str = "6mo") -> CompositeBacktestResult:
        """단일 종목 백테스트."""
        import yfinance as yf
        df = yf.download(ticker, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 60:
            return self._empty_result()
        if hasattr(df.columns, "levels"):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        return self._simulate(ticker, df)

    def _simulate(self, ticker: str, df: pd.DataFrame) -> CompositeBacktestResult:
        from core.data_collector import DataCollector
        from core.screener import MarketScreener
        from core.fundamental_gate import FundamentalGate

        screener = MarketScreener(fundamental_gate=FundamentalGate())
        trades = []
        position = None  # {entry_idx, entry_price, atr, sl, tp, setup}

        for i in range(60, len(df) - 1):
            slice_df = df.iloc[:i+1]
            indicators = DataCollector._calc_indicators(slice_df)
            cur_close  = float(slice_df["close"].iloc[-1])
            atr        = indicators.get("atr", 0)

            # 보유 중이면 청산 체크
            if position is not None:
                next_high = float(df.iloc[i+1]["high"])
                next_low  = float(df.iloc[i+1]["low"])
                next_open = float(df.iloc[i+1]["open"])
                exit_price = None; exit_reason = ""
                if next_low <= position["sl"]:
                    exit_price = position["sl"]; exit_reason = "SL"
                elif next_high >= position["tp"]:
                    exit_price = position["tp"]; exit_reason = "TP"
                elif (i+1 - position["entry_idx"]) >= self.max_hold:
                    exit_price = next_open; exit_reason = "TIME"
                if exit_price is not None:
                    # 비용 보정
                    entry = position["entry_price"] * (1 + self.SLIPPAGE) * (1 + self.COMMISSION)
                    exit_ = exit_price * (1 - self.SLIPPAGE) * (1 - self.COMMISSION - self.SELL_TAX)
                    pnl_pct = (exit_ - entry) / entry * 100
                    trades.append({
                        "entry_idx": position["entry_idx"],
                        "exit_idx":  i+1,
                        "entry_price": position["entry_price"],
                        "exit_price":  exit_price,
                        "pnl_pct":     pnl_pct,
                        "setup":       position["setup"],
                        "reason":      exit_reason,
                        "hold_days":   i+1 - position["entry_idx"],
                    })
                    position = None

            # 신규 진입 시그널 (간이 평가 — _evaluate 호출)
            if position is None and atr > 0:
                # 가벼운 snapshot 객체로 _evaluate 호출
                from types import SimpleNamespace
                snap = SimpleNamespace(
                    ticker=ticker, current_price=cur_close, name=ticker,
                    volume_ratio=indicators.get("volume", 0) / max(slice_df["volume"].tail(20).mean(), 1),
                    daily_df=slice_df, **indicators,
                )
                cand = screener._evaluate(ticker, snap)
                if cand and cand.tech_score >= 70 and cand.setup_type and cand.setup_type != "—" and "Bearish" not in cand.setup_type:
                    sl = cur_close - atr * self.sl_mult
                    tp = cur_close + atr * self.tp_mult
                    position = {
                        "entry_idx":   i,
                        "entry_price": cur_close,
                        "atr": atr, "sl": sl, "tp": tp,
                        "setup": cand.setup_type,
                    }

        return self._summarize(trades, df)

    def _summarize(self, trades: list, df: pd.DataFrame) -> CompositeBacktestResult:
        if not trades:
            return self._empty_result()
        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        winrate = len(wins) / len(trades)
        avg_pnl = sum(pnls) / len(pnls)
        total = sum(pnls)
        # MDD (실현 누적 기준)
        cum = []
        c = 0.0
        for p in pnls:
            c += p
            cum.append(c)
        peak = -float("inf"); mdd = 0.0
        for v in cum:
            peak = max(peak, v)
            mdd = min(mdd, v - peak)
        # 셋업별
        by_setup: dict = {}
        for t in trades:
            s = t["setup"]
            d = by_setup.setdefault(s, {"n": 0, "wins": 0, "pnl_sum": 0.0})
            d["n"] += 1
            if t["pnl_pct"] > 0: d["wins"] += 1
            d["pnl_sum"] += t["pnl_pct"]
        for s, d in by_setup.items():
            d["winrate"] = round(d["wins"] / d["n"], 3)
            d["avg_pnl"] = round(d["pnl_sum"] / d["n"], 2)
        # 월별
        by_month: dict = {}
        for t in trades:
            try:
                month = pd.Timestamp(df.index[t["exit_idx"]]).strftime("%Y-%m")
            except Exception:
                month = "?"
            by_month.setdefault(month, []).append(t["pnl_pct"])
        by_month = {k: round(sum(v), 2) for k, v in by_month.items()}
        avg_hold = sum(t["hold_days"] for t in trades) / len(trades)
        return CompositeBacktestResult(
            total_trades   = len(trades),
            winrate        = round(winrate, 3),
            avg_pnl_pct    = round(avg_pnl, 2),
            total_pnl_pct  = round(total, 2),
            max_drawdown   = round(mdd, 2),
            by_setup       = by_setup,
            avg_hold_days  = round(avg_hold, 1),
            by_month       = by_month,
        )

    @staticmethod
    def _empty_result() -> CompositeBacktestResult:
        return CompositeBacktestResult(0, 0.0, 0.0, 0.0, 0.0, {}, 0.0, {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="005930.KS,000660.KS,035420.KS",
                        help="콤마구분 티커")
    parser.add_argument("--period", default="6mo", help="yfinance period (3mo/6mo/1y)")
    args = parser.parse_args()

    bt = CompositeBacktest()
    print(f"\n{'='*70}\n  통합 점수 백테스트 (비용 + 거래세 + 슬리피지 반영)\n{'='*70}")

    grand_total = 0.0; grand_n = 0; grand_wins = 0
    for ticker in args.tickers.split(","):
        ticker = ticker.strip()
        print(f"\n📊 {ticker} ({args.period})")
        r = bt.run(ticker, args.period)
        if r.total_trades == 0:
            print("  거래 0건 — 진입 시그널 없음")
            continue
        print(f"  거래 {r.total_trades}건 | 승률 {r.winrate*100:.1f}% | 평균손익 {r.avg_pnl_pct:+.2f}% | 누적 {r.total_pnl_pct:+.2f}% | MDD {r.max_drawdown:.2f}%")
        print(f"  평균 보유 {r.avg_hold_days:.1f}일")
        if r.by_setup:
            print("  셋업별:")
            for s, d in sorted(r.by_setup.items(), key=lambda x: -x[1]["avg_pnl"]):
                print(f"    {s:30s} {d['n']:3d}건 / 승률 {d['winrate']*100:5.1f}% / 평균 {d['avg_pnl']:+.2f}%")
        grand_total += r.total_pnl_pct
        grand_n     += r.total_trades
        grand_wins  += int(r.winrate * r.total_trades)

    if grand_n > 0:
        print(f"\n{'='*70}")
        print(f"  전체: {grand_n}건 거래 | 승률 {grand_wins/grand_n*100:.1f}% | 누적 {grand_total:+.2f}%")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
