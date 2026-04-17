"""
backtest/monte_carlo.py — 몬테카를로 시뮬레이션

백테스팅 거래 내역을 기반으로 수천 번의 무작위 시뮬레이션을 실행,
전략의 통계적 신뢰구간·파산 확률·기대 수익 분포를 계산한다.

사용 예:
    mc = MonteCarloSimulator(backtest_result)
    mc_result = mc.run(n_simulations=5000)
    mc.print_report(mc_result)
    mc.save_html(mc_result)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from backtest.engine import BacktestResult

REPORT_DIR = Path(__file__).parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


@dataclass
class MonteCarloResult:
    n_simulations:    int
    n_trades:         int
    initial_capital:  float

    # 수익률 분포
    final_returns:    np.ndarray   # shape: (n_simulations,)
    max_drawdowns:    np.ndarray
    sharpe_ratios:    np.ndarray

    # 백분위 통계
    p5_return:   float    # 5분위 (최악 5%)
    p25_return:  float
    p50_return:  float    # 중앙값
    p75_return:  float
    p95_return:  float    # 최상 5%

    p5_mdd:      float
    p50_mdd:     float
    p95_mdd:     float

    # 위험 지표
    ruin_prob:   float    # 파산 확률 (-50% 이하)
    loss_prob:   float    # 손실 확률 (0% 이하)
    beat_market: float    # 코스피 연평균 8% 초과 확률

    # 에쿼티 커브 샘플 (시각화용)
    equity_samples: list[list[float]]  # 100개 샘플


class MonteCarloSimulator:
    """
    백테스팅 거래 내역에서 손익률을 추출하여
    부트스트랩 방식으로 몬테카를로 시뮬레이션을 실행한다.
    """

    RUIN_THRESHOLD   = -0.50   # 파산 기준: -50%
    MARKET_ANNUAL    =  0.08   # 코스피 연평균 수익률 가정

    def __init__(self, backtest_result: BacktestResult) -> None:
        self._result = backtest_result
        self._cfg    = backtest_result.config
        # 체결 완료된 거래의 손익률 배열
        closed = [t for t in backtest_result.trades if t.exit_date and t.pnl_pct is not None]
        self._pnl_pcts = np.array([t.pnl_pct / 100 for t in closed])
        self._n_trades = len(self._pnl_pcts)
        logger.info(
            "몬테카를로 초기화: {} [{} 거래, 평균손익:{:.2f}%]",
            backtest_result.ticker, self._n_trades,
            float(np.mean(self._pnl_pcts) * 100) if len(self._pnl_pcts) > 0 else 0,
        )

    def run(
        self,
        n_simulations:  int   = 5_000,
        trades_per_sim: int   = None,
        position_size:  float = 0.20,   # 1거래당 투자 비중
        seed:           int   = 42,
    ) -> MonteCarloResult:
        """
        n_simulations: 시뮬레이션 횟수
        trades_per_sim: 시뮬레이션당 거래 수 (None이면 원본과 동일)
        position_size: 1거래당 자본 비중 (0~1)
        """
        if len(self._pnl_pcts) < 3:
            logger.warning("거래 수 부족 ({}) — 합성 손익률로 보완", len(self._pnl_pcts))
            self._pnl_pcts = np.random.normal(0.01, 0.04, 20)

        rng  = np.random.default_rng(seed)
        n    = trades_per_sim or max(self._n_trades, 20)
        cap  = self._cfg.initial_capital

        final_returns  = np.zeros(n_simulations)
        max_drawdowns  = np.zeros(n_simulations)
        sharpe_ratios  = np.zeros(n_simulations)
        equity_samples = []

        for i in range(n_simulations):
            # 부트스트랩: 원본 손익률에서 무작위 복원추출
            sampled = rng.choice(self._pnl_pcts, size=n, replace=True)
            # 슬리피지·수수료 적용
            sampled = sampled - 0.0015

            # 자산 추이 시뮬레이션
            equity = cap
            peak   = cap
            max_dd = 0.0
            eq_curve = [cap]

            for pct in sampled:
                gain   = equity * position_size * pct
                equity = max(0, equity + gain)
                peak   = max(peak, equity)
                dd     = (equity - peak) / peak if peak > 0 else 0
                max_dd = min(max_dd, dd)
                eq_curve.append(equity)

            final_ret = (equity - cap) / cap
            final_returns[i] = final_ret
            max_drawdowns[i] = max_dd

            # 일별 수익률로 샤프지수 계산
            eq_arr = np.array(eq_curve)
            if len(eq_arr) > 1:
                daily = np.diff(eq_arr) / eq_arr[:-1]
                rf    = 0.035 / 252
                sharpe_ratios[i] = (
                    (daily.mean() - rf) / daily.std() * np.sqrt(252)
                    if daily.std() > 0 else 0
                )

            # 100개 샘플만 저장 (시각화용)
            if i < 100:
                equity_samples.append([round(v, 0) for v in eq_curve[::max(1, n//50)]])

        # 백분위 계산
        p = np.percentile

        result = MonteCarloResult(
            n_simulations   = n_simulations,
            n_trades        = n,
            initial_capital = cap,
            final_returns   = final_returns,
            max_drawdowns   = max_drawdowns,
            sharpe_ratios   = sharpe_ratios,
            p5_return   = float(p(final_returns, 5)  * 100),
            p25_return  = float(p(final_returns, 25) * 100),
            p50_return  = float(p(final_returns, 50) * 100),
            p75_return  = float(p(final_returns, 75) * 100),
            p95_return  = float(p(final_returns, 95) * 100),
            p5_mdd      = float(p(max_drawdowns, 5)  * 100),
            p50_mdd     = float(p(max_drawdowns, 50) * 100),
            p95_mdd     = float(p(max_drawdowns, 95) * 100),
            ruin_prob   = float(np.mean(final_returns <= self.RUIN_THRESHOLD) * 100),
            loss_prob   = float(np.mean(final_returns <= 0) * 100),
            beat_market = float(np.mean(final_returns >= self.MARKET_ANNUAL) * 100),
            equity_samples = equity_samples,
        )

        logger.info(
            "몬테카를로 완료: {}회 | 중앙수익:{:+.1f}% | 파산확률:{:.1f}% | 시장초과:{:.1f}%",
            n_simulations, result.p50_return, result.ruin_prob, result.beat_market,
        )
        return result

    # ── 터미널 리포트 ─────────────────────────

    def print_report(self, r: MonteCarloResult) -> None:
        print("\n" + "═"*55)
        print(f"  🎲 몬테카를로 시뮬레이션 결과")
        print(f"  {self._result.ticker} [{self._result.strategy_name}]")
        print(f"  시뮬레이션: {r.n_simulations:,}회 | 거래수/회: {r.n_trades}")
        print("═"*55)
        print(f"\n  [수익률 분포]")
        print(f"  최악  5% : {r.p5_return:+.1f}%")
        print(f"  하위 25% : {r.p25_return:+.1f}%")
        print(f"  중앙값   : {r.p50_return:+.1f}%  ← 기대 수익")
        print(f"  상위 75% : {r.p75_return:+.1f}%")
        print(f"  최상  5% : {r.p95_return:+.1f}%")
        print(f"\n  [MDD 분포]")
        print(f"  최악  5% : {r.p5_mdd:.1f}%")
        print(f"  중앙값   : {r.p50_mdd:.1f}%")
        print(f"  최상  5% : {r.p95_mdd:.1f}%")
        print(f"\n  [위험 지표]")
        c_ruin  = "\033[91m" if r.ruin_prob > 5 else "\033[92m"
        c_loss  = "\033[91m" if r.loss_prob > 40 else "\033[92m"
        c_beat  = "\033[92m" if r.beat_market > 50 else "\033[93m"
        RESET   = "\033[0m"
        print(f"  파산 확률(-50%이하) : {c_ruin}{r.ruin_prob:.1f}%{RESET}")
        print(f"  손실 확률           : {c_loss}{r.loss_prob:.1f}%{RESET}")
        print(f"  시장 초과(8%+) 확률 : {c_beat}{r.beat_market:.1f}%{RESET}")
        print("═"*55 + "\n")

    # ── HTML 리포트 ───────────────────────────

    def save_html(self, r: MonteCarloResult, filename: str = None) -> Path:
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mc_{self._result.ticker}_{ts}.html"

        # 히스토그램 데이터 (Chart.js용)
        hist_values = r.final_returns * 100
        bins = np.linspace(hist_values.min(), hist_values.max(), 50)
        counts, edges = np.histogram(hist_values, bins=bins)
        bar_labels = [f"{(edges[i]+edges[i+1])/2:.1f}" for i in range(len(counts))]
        bar_colors = [
            "#A32D2D" if float(edges[i]+edges[i+1])/2 < 0 else "#27500A"
            for i in range(len(counts))
        ]

        equity_json = json.dumps(r.equity_samples[:50])
        n_steps = max(len(s) for s in r.equity_samples[:50]) if r.equity_samples else 1
        labels_json = json.dumps(list(range(n_steps)))

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>몬테카를로 시뮬레이션 — {self._result.ticker}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f5f4f0;color:#2c2c2a;padding:24px}}
h1{{font-size:20px;font-weight:500;margin-bottom:4px}}
.sub{{color:#5f5e5a;font-size:13px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px}}
.card{{background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:16px}}
.card-title{{font-size:11px;color:#888780;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}}
.card-value{{font-size:22px;font-weight:500}}
.pos{{color:#27500A}}.neg{{color:#A32D2D}}.warn{{color:#854F0B}}
.chart-card{{background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:20px;margin-bottom:16px}}
.chart-title{{font-size:13px;font-weight:500;margin-bottom:16px}}
.stat-row{{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}}
.stat{{flex:1;min-width:120px;background:#f9f8f4;border-radius:8px;padding:10px}}
.stat-label{{font-size:11px;color:#888780;margin-bottom:4px}}
.stat-val{{font-size:15px;font-weight:500}}
</style>
</head>
<body>
<h1>🎲 몬테카를로 시뮬레이션</h1>
<p class="sub">
  {self._result.ticker} [{self._result.strategy_name}] |
  시뮬레이션 {r.n_simulations:,}회 | 거래수/회: {r.n_trades} |
  생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>

<div class="grid">
  <div class="card">
    <div class="card-title">중앙 기대수익 (p50)</div>
    <div class="card-value {'pos' if r.p50_return>=0 else 'neg'}">{r.p50_return:+.1f}%</div>
    <div style="font-size:11px;color:#888780;margin-top:6px">p5: {r.p5_return:+.1f}% ~ p95: {r.p95_return:+.1f}%</div>
  </div>
  <div class="card">
    <div class="card-title">파산 확률 (-50% 이하)</div>
    <div class="card-value {'neg' if r.ruin_prob>5 else 'pos'}">{r.ruin_prob:.1f}%</div>
    <div style="font-size:11px;color:#888780;margin-top:6px">손실 확률: {r.loss_prob:.1f}%</div>
  </div>
  <div class="card">
    <div class="card-title">시장 초과 (연 8%+) 확률</div>
    <div class="card-value {'pos' if r.beat_market>=50 else 'warn'}">{r.beat_market:.1f}%</div>
    <div style="font-size:11px;color:#888780;margin-top:6px">중앙 MDD: {r.p50_mdd:.1f}%</div>
  </div>
</div>

<div class="chart-card">
  <div class="chart-title">수익률 분포 히스토그램</div>
  <canvas id="hist-chart" height="80"></canvas>
  <div class="stat-row">
    <div class="stat"><div class="stat-label">최악 5%</div><div class="stat-val neg">{r.p5_return:+.1f}%</div></div>
    <div class="stat"><div class="stat-label">하위 25%</div><div class="stat-val">{r.p25_return:+.1f}%</div></div>
    <div class="stat"><div class="stat-label">중앙값</div><div class="stat-val">{r.p50_return:+.1f}%</div></div>
    <div class="stat"><div class="stat-label">상위 75%</div><div class="stat-val">{r.p75_return:+.1f}%</div></div>
    <div class="stat"><div class="stat-label">최상 5%</div><div class="stat-val pos">{r.p95_return:+.1f}%</div></div>
  </div>
</div>

<div class="chart-card">
  <div class="chart-title">에쿼티 커브 시뮬레이션 (50개 샘플)</div>
  <canvas id="equity-chart" height="80"></canvas>
</div>

<script>
// 히스토그램
new Chart(document.getElementById('hist-chart').getContext('2d'), {{
  type:'bar',
  data:{{
    labels:{json.dumps(bar_labels)},
    datasets:[{{
      data:{json.dumps(counts.tolist())},
      backgroundColor:{json.dumps(bar_colors)},
      borderWidth:0,borderRadius:2
    }}]
  }},
  options:{{
    responsive:true,plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{title:c=>c[0].label+'%',label:c=>c.parsed.y+'회'}}}}}},
    scales:{{
      x:{{grid:{{display:false}},ticks:{{font:{{size:9}},maxTicksLimit:12}}}},
      y:{{grid:{{color:'#f1efe8'}},ticks:{{font:{{size:10}}}}}}
    }}
  }}
}});

// 에쿼티 커브
const samples = {equity_json};
const labels  = {labels_json};
const datasets = samples.map((s,i) => ({{
  data:s, borderColor:'rgba(83,74,183,0.12)',
  borderWidth:1, pointRadius:0, fill:false, tension:0.2
}}));
new Chart(document.getElementById('equity-chart').getContext('2d'), {{
  type:'line',
  data:{{labels, datasets}},
  options:{{
    responsive:true,animation:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{
      x:{{display:false}},
      y:{{grid:{{color:'#f1efe8'}},
        ticks:{{font:{{size:10}},callback:v=>(v/10000).toFixed(0)+'만'}}}}
    }}
  }}
}});
</script>
</body>
</html>"""
        path = REPORT_DIR / filename
        path.write_text(html, encoding="utf-8")
        logger.info("몬테카를로 리포트: {}", path)
        return path
