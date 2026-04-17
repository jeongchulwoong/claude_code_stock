"""
backtest/walk_forward.py — 워크포워드(Walk-Forward) 테스트

과최적화(Overfitting) 방지를 위한 표준 검증 방법론:
  1. 전체 기간을 N개 윈도우로 분할
  2. 각 윈도우: In-Sample(훈련) 기간에서 최적 파라미터 탐색
  3. Out-of-Sample(검증) 기간에 실제 적용
  4. OOS 결과만 모아 최종 성과 측정

이 과정을 통과한 전략만 실거래에 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from loguru import logger

from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult, Trade
from backtest.optimizer import StrategyOptimizer

REPORT_DIR = Path(__file__).parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


@dataclass
class WFWindow:
    """단일 워크포워드 윈도우 결과"""
    window_no:      int
    is_start:       str    # In-Sample 시작
    is_end:         str    # In-Sample 종료
    oos_start:      str    # Out-of-Sample 시작
    oos_end:        str    # Out-of-Sample 종료
    best_params:    dict
    is_sharpe:      float  # IS 최적 샤프지수
    oos_result:     BacktestResult
    efficiency:     float  # OOS / IS 샤프지수 비율 (1에 가까울수록 좋음)


@dataclass
class WalkForwardResult:
    ticker:        str
    strategy_name: str
    windows:       list[WFWindow]

    @property
    def oos_trades(self) -> list[Trade]:
        """모든 OOS 구간 거래 통합"""
        all_trades = []
        for w in self.windows:
            all_trades.extend([t for t in w.oos_result.trades if t.exit_date])
        return all_trades

    @property
    def avg_efficiency(self) -> float:
        effs = [w.efficiency for w in self.windows if not np.isnan(w.efficiency)]
        return float(np.mean(effs)) if effs else 0.0

    @property
    def oos_win_rate(self) -> float:
        trades = self.oos_trades
        if not trades:
            return 0.0
        return sum(1 for t in trades if t.is_win) / len(trades) * 100

    @property
    def oos_total_return(self) -> float:
        """OOS 구간 에쿼티 커브 연결하여 총 수익률 계산"""
        cap = self.windows[0].oos_result.config.initial_capital
        final = cap
        for w in self.windows:
            eq = w.oos_result.equity_curve
            if not eq.empty:
                ratio = eq.iloc[-1] / eq.iloc[0]
                final *= ratio
        return (final - cap) / cap * 100

    def summary(self) -> dict:
        return {
            "종목":          self.ticker,
            "전략":          self.strategy_name,
            "윈도우 수":      len(self.windows),
            "OOS 총수익률":   f"{self.oos_total_return:+.2f}%",
            "OOS 승률":      f"{self.oos_win_rate:.1f}%",
            "평균 효율성":    f"{self.avg_efficiency:.2f}",
            "OOS 거래수":    len(self.oos_trades),
            "판정":          "✅ 통과" if self.avg_efficiency >= 0.5 else "❌ 실패",
        }


class WalkForwardTester:
    """
    앵커드(Anchored) 또는 롤링(Rolling) 워크포워드 테스트.

    - anchored: IS 시작점 고정, OOS 윈도우 점진 확장
    - rolling:  IS/OOS 윈도우 크기 고정, 슬라이딩

    사용 예:
        wf = WalkForwardTester(df, "005930", config)
        result = wf.run_momentum(n_windows=5, mode="rolling")
        wf.print_report(result)
    """

    def __init__(
        self,
        df:      pd.DataFrame,
        ticker:  str,
        config:  BacktestConfig = None,
    ) -> None:
        self._df     = df
        self._ticker = ticker
        self._cfg    = config or BacktestConfig()
        self._engine = BacktestEngine(self._cfg)

    # ── 모멘텀 전략 WF ───────────────────────

    def run_momentum(
        self,
        n_windows:  int  = 5,
        is_pct:     float = 0.7,   # IS 비율
        mode:       str  = "rolling",
        metric:     str  = "sharpe",
    ) -> WalkForwardResult:
        """
        모멘텀 전략에 대해 워크포워드를 실행한다.
        각 IS 구간에서 RSI·거래량 파라미터를 최적화하고
        OOS 구간에 적용한다.
        """
        windows = self._split_windows(n_windows, is_pct, mode)
        results = []

        for i, (is_df, oos_df, is_s, is_e, oos_s, oos_e) in enumerate(windows, 1):
            logger.info("WF 윈도우 {}/{} | IS:{} ~ {} | OOS:{} ~ {}",
                        i, n_windows, is_s, is_e, oos_s, oos_e)

            # IS 최적화
            opt = StrategyOptimizer(is_df, self._ticker, self._cfg)
            try:
                opt_result = opt.optimize_momentum(
                    rsi_range=(20, 45, 5),
                    vol_range=(1.5, 3.0, 0.5),
                    metric=metric,
                )
                best_params = opt_result.best_params or {"rsi_threshold": 35.0, "vol_threshold": 2.0}
                is_sharpe   = opt_result.best_sharpe if opt_result.best_sharpe > -900 else 0.0
            except Exception as e:
                logger.warning("IS 최적화 실패: {}", e)
                best_params = {"rsi_threshold": 35.0, "vol_threshold": 2.0}
                is_sharpe   = 0.0

            # OOS 적용
            rsi_th = best_params.get("rsi_threshold", 35)
            vol_th = best_params.get("vol_threshold", 2.0)

            def oos_strategy(df, idx, r=rsi_th, v=vol_th):
                row = df.iloc[idx]
                if (row["rsi"] < r and row["vol_ratio"] >= v and
                        row["ma5"] > row["ma20"] and row["macd_cross"]):
                    return "BUY"
                if row["rsi"] > 70:
                    return "SELL"
                return "HOLD"

            oos_result = self._engine.run(
                oos_df, oos_strategy, self._ticker,
                f"wf_momentum_w{i}(rsi<{rsi_th:.0f},vol>{vol_th:.1f})"
            )
            oos_sharpe = oos_result.sharpe_ratio

            efficiency = (oos_sharpe / is_sharpe) if abs(is_sharpe) > 0.01 else 0.0

            results.append(WFWindow(
                window_no   = i,
                is_start    = is_s, is_end    = is_e,
                oos_start   = oos_s, oos_end  = oos_e,
                best_params = best_params,
                is_sharpe   = is_sharpe,
                oos_result  = oos_result,
                efficiency  = efficiency,
            ))

        return WalkForwardResult(
            ticker        = self._ticker,
            strategy_name = "momentum_wf",
            windows       = results,
        )

    # ── 윈도우 분할 ───────────────────────────

    def _split_windows(
        self, n: int, is_pct: float, mode: str
    ) -> list[tuple]:
        """(is_df, oos_df, is_start, is_end, oos_start, oos_end) 리스트 반환"""
        df   = self._df
        total= len(df)
        step = total // (n + 1) if mode == "rolling" else None
        windows = []

        for i in range(n):
            if mode == "rolling":
                is_size  = int(total * is_pct * (i + 1) / n)
                oos_size = step
                is_start = 0
                is_end   = min(is_size, total - oos_size)
                oos_start= is_end
                oos_end  = min(oos_start + oos_size, total)
            else:  # anchored
                chunk    = total // n
                oos_start= chunk * i
                oos_end  = chunk * (i + 1)
                is_start = 0
                is_end   = oos_start

            if is_end - is_start < 60 or oos_end - oos_start < 20:
                continue

            is_df  = df.iloc[is_start:is_end]
            oos_df = df.iloc[oos_start:oos_end]

            fmt = lambda idx: str(df.index[min(idx, len(df)-1)].date())
            windows.append((
                is_df, oos_df,
                fmt(is_start), fmt(is_end - 1),
                fmt(oos_start), fmt(oos_end - 1),
            ))

        return windows

    # ── 리포트 출력 ───────────────────────────

    def print_report(self, result: WalkForwardResult) -> None:
        s = result.summary()
        print("\n" + "═"*65)
        print(f"  🔄 워크포워드 테스트 결과: {result.ticker} [{result.strategy_name}]")
        print("═"*65)
        for k, v in s.items():
            print(f"  {k:<14} {v}")

        print(f"\n  [윈도우별 상세]")
        print(f"  {'윈도우':>3} {'IS 기간':^24} {'OOS 기간':^24} {'파라미터':^20} {'IS 샤프':>7} {'OOS 수익률':>10} {'효율성':>7}")
        print("  " + "-"*100)
        for w in result.windows:
            oos_ret = w.oos_result.total_return_pct
            rsi = w.best_params.get('rsi_threshold', '-')
            vol = w.best_params.get('vol_threshold', '-')
            print(
                f"  {w.window_no:>3} {w.is_start}~{w.is_end}  "
                f"{w.oos_start}~{w.oos_end}  "
                f"RSI<{rsi:.0f} Vol>{vol:.1f}  "
                f"{w.is_sharpe:>7.2f} {oos_ret:>+9.2f}%  {w.efficiency:>7.2f}"
                if isinstance(rsi, float) else
                f"  {w.window_no:>3} {w.is_start}~{w.is_end}  "
                f"{w.oos_start}~{w.oos_end}  {'N/A':^20}  "
                f"{w.is_sharpe:>7.2f} {oos_ret:>+9.2f}%  {w.efficiency:>7.2f}"
            )

        verdict = "✅ 통과 — 실거래 적용 가능" if result.avg_efficiency >= 0.5 else "❌ 실패 — 과최적화 의심"
        print(f"\n  평균 효율성: {result.avg_efficiency:.2f} | {verdict}")
        print("═"*65 + "\n")

    def save_html(self, result: WalkForwardResult, filename: str = None) -> Path:
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"wf_{result.ticker}_{ts}.html"

        rows = ""
        for w in result.windows:
            oos_ret = w.oos_result.total_return_pct
            eff_color = "#27500A" if w.efficiency >= 0.5 else "#A32D2D"
            ret_color = "#27500A" if oos_ret >= 0 else "#A32D2D"
            rows += f"""<tr>
              <td>W{w.window_no}</td>
              <td>{w.is_start} ~ {w.is_end}</td>
              <td>{w.oos_start} ~ {w.oos_end}</td>
              <td>{w.best_params}</td>
              <td>{w.is_sharpe:.2f}</td>
              <td style="color:{ret_color}">{oos_ret:+.2f}%</td>
              <td>{w.oos_result.win_rate:.1f}%</td>
              <td style="color:{eff_color}">{w.efficiency:.2f}</td>
            </tr>"""

        s = result.summary()
        verdict_color = "#27500A" if result.avg_efficiency >= 0.5 else "#A32D2D"
        html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>워크포워드 — {result.ticker}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f5f4f0;color:#2c2c2a;padding:24px}}
h1{{font-size:20px;font-weight:500;margin-bottom:4px}}
.sub{{color:#5f5e5a;font-size:13px;margin-bottom:24px}}
.verdict{{font-size:16px;font-weight:500;color:{verdict_color};margin:16px 0}}
.stat-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.stat{{background:#fff;border-radius:10px;border:1px solid #e0dfd8;padding:14px}}
.stat-label{{font-size:11px;color:#888780;margin-bottom:6px}}
.stat-value{{font-size:18px;font-weight:500}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;
       border:1px solid #e0dfd8;overflow:hidden}}
th{{background:#f1efe8;padding:10px 12px;font-size:11px;font-weight:500;
    color:#5f5e5a;text-align:left}}
td{{padding:10px 12px;font-size:12px;border-top:1px solid #f1efe8;color:#3d3d3a}}
</style>
</head><body>
<h1>🔄 워크포워드 테스트</h1>
<p class="sub">{result.ticker} [{result.strategy_name}] | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p class="verdict">{s['판정']}</p>
<div class="stat-grid">
  <div class="stat"><div class="stat-label">OOS 총 수익률</div><div class="stat-value">{s['OOS 총수익률']}</div></div>
  <div class="stat"><div class="stat-label">OOS 승률</div><div class="stat-value">{s['OOS 승률']}</div></div>
  <div class="stat"><div class="stat-label">평균 효율성</div><div class="stat-value">{s['평균 효율성']}</div></div>
  <div class="stat"><div class="stat-label">OOS 거래수</div><div class="stat-value">{s['OOS 거래수']}건</div></div>
</div>
<table>
<thead><tr><th>윈도우</th><th>IS 기간</th><th>OOS 기간</th><th>최적 파라미터</th>
<th>IS 샤프</th><th>OOS 수익률</th><th>OOS 승률</th><th>효율성</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style="font-size:11px;color:#888780;margin-top:16px">
효율성 = OOS 샤프지수 / IS 샤프지수. 0.5 이상이면 과최적화 없이 일반화된 전략으로 판단.
</p>
</body></html>"""
        path = REPORT_DIR / filename
        path.write_text(html, encoding="utf-8")
        logger.info("워크포워드 리포트: {}", path)
        return path
