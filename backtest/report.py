"""
backtest/report.py — 백테스팅 결과 리포트 생성

- 터미널 출력 (rich 스타일 텍스트)
- HTML 리포트 (Chart.js 차트 + 거래 내역 테이블)
- CSV 거래 내역 저장
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from backtest.engine import BacktestResult

REPORT_DIR = Path(__file__).parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


# ── 터미널 출력 ───────────────────────────────

def print_summary(result: BacktestResult) -> None:
    """터미널에 결과 요약을 출력한다."""
    s = result.summary()
    print("\n" + "═" * 50)
    print(f"  📊 백테스팅 결과: {result.ticker} [{result.strategy_name}]")
    print("═" * 50)
    for k, v in s.items():
        print(f"  {k:<14} {v}")
    print("═" * 50)

    # 거래 내역 상위 10건
    closed = [t for t in result.trades if t.exit_date]
    if closed:
        print(f"\n  [최근 거래 {min(10, len(closed))}건]")
        print(f"  {'진입일':<12} {'청산일':<12} {'진입가':>10} {'청산가':>10} {'손익%':>8} {'사유'}")
        print("  " + "-" * 65)
        for t in closed[-10:]:
            emoji = "🟢" if t.is_win else "🔴"
            print(
                f"  {str(t.entry_date):<12} {str(t.exit_date):<12} "
                f"{t.entry_price:>10,.0f} {t.exit_price:>10,.0f} "
                f"{t.pnl_pct:>+7.2f}% {emoji} {t.exit_reason}"
            )
    print()


def compare_strategies(results: list[BacktestResult]) -> pd.DataFrame:
    """여러 전략을 한 테이블로 비교한다."""
    rows = []
    for r in results:
        rows.append({
            "전략":       r.strategy_name,
            "종목":       r.ticker,
            "총수익률":   f"{r.total_return_pct:+.2f}%",
            "CAGR":       f"{r.cagr:+.2f}%",
            "MDD":        f"{r.mdd:.2f}%",
            "샤프지수":   f"{r.sharpe_ratio:.2f}",
            "승률":       f"{r.win_rate:.1f}%",
            "손익비":     f"{r.profit_factor:.2f}",
            "거래수":     r.total_trades,
            "평균보유일": f"{r.avg_holding_days:.1f}",
        })
    df = pd.DataFrame(rows)
    print("\n" + "═" * 80)
    print("  📊 전략 비교 테이블")
    print("═" * 80)
    print(df.to_string(index=False))
    print("═" * 80)
    return df


# ── HTML 리포트 생성 ──────────────────────────

def generate_html_report(
    results: list[BacktestResult],
    filename: Optional[str] = None,
) -> Path:
    """
    Chart.js 기반 HTML 리포트를 생성하고 경로를 반환한다.
    results: 1개 이상의 BacktestResult 리스트
    """
    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_report_{ts}.html"

    report_path = REPORT_DIR / filename

    # 에쿼티 커브 데이터 (Chart.js용)
    datasets = []
    colors   = [
        "#534AB7", "#0F6E56", "#993C1D", "#185FA5",
        "#854F0B", "#A32D2D", "#3B6D11", "#993556",
    ]
    for idx, r in enumerate(results):
        eq = r.equity_curve.dropna()
        color = colors[idx % len(colors)]
        datasets.append({
            "label":           f"{r.ticker} [{r.strategy_name}]",
            "data":            [{"x": str(d.date()), "y": round(v, 0)} for d, v in eq.items()],
            "borderColor":     color,
            "backgroundColor": color + "22",
            "borderWidth":     2,
            "fill":            False,
            "pointRadius":     0,
            "tension":         0.1,
        })

    # 거래 내역 HTML 행
    trade_rows_html = ""
    for r in results:
        for t in sorted([tr for tr in r.trades if tr.exit_date], key=lambda x: x.entry_date):
            color = "#27500A" if t.is_win else "#A32D2D"
            trade_rows_html += f"""
            <tr>
                <td>{t.ticker}</td>
                <td>{r.strategy_name}</td>
                <td>{t.entry_date}</td>
                <td>{t.exit_date}</td>
                <td style="text-align:right">{t.entry_price:,.0f}</td>
                <td style="text-align:right">{t.exit_price:,.0f}</td>
                <td style="text-align:right;color:{color}">{t.pnl_pct:+.2f}%</td>
                <td style="text-align:right;color:{color}">{t.pnl:+,.0f}</td>
                <td>{t.exit_reason}</td>
                <td>{t.holding_days}일</td>
            </tr>"""

    # 요약 카드 HTML
    summary_cards_html = ""
    for r in results:
        mdd_color  = "#A32D2D" if r.mdd < -15 else "#854F0B" if r.mdd < -8 else "#27500A"
        ret_color  = "#27500A" if r.total_return_pct > 0 else "#A32D2D"
        summary_cards_html += f"""
        <div class="card">
            <div class="card-title">{r.ticker} [{r.strategy_name}]</div>
            <div class="stat-grid">
                <div class="stat"><div class="stat-label">총 수익률</div>
                    <div class="stat-value" style="color:{ret_color}">{r.total_return_pct:+.2f}%</div></div>
                <div class="stat"><div class="stat-label">CAGR</div>
                    <div class="stat-value">{r.cagr:+.2f}%</div></div>
                <div class="stat"><div class="stat-label">MDD</div>
                    <div class="stat-value" style="color:{mdd_color}">{r.mdd:.2f}%</div></div>
                <div class="stat"><div class="stat-label">샤프지수</div>
                    <div class="stat-value">{r.sharpe_ratio:.2f}</div></div>
                <div class="stat"><div class="stat-label">승률</div>
                    <div class="stat-value">{r.win_rate:.1f}%</div></div>
                <div class="stat"><div class="stat-label">손익비</div>
                    <div class="stat-value">{r.profit_factor:.2f}</div></div>
                <div class="stat"><div class="stat-label">총 거래</div>
                    <div class="stat-value">{r.total_trades}건</div></div>
                <div class="stat"><div class="stat-label">평균보유</div>
                    <div class="stat-value">{r.avg_holding_days:.1f}일</div></div>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>백테스팅 리포트 — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f4f0;color:#2c2c2a;padding:24px}}
  h1{{font-size:22px;font-weight:500;margin-bottom:20px;color:#2c2c2a}}
  h2{{font-size:16px;font-weight:500;margin:28px 0 12px}}
  .card{{background:#fff;border-radius:12px;padding:20px;margin-bottom:16px;border:1px solid #e0dfd8}}
  .card-title{{font-size:15px;font-weight:500;margin-bottom:16px;color:#3d3d3a}}
  .stat-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  .stat{{background:#f9f8f4;border-radius:8px;padding:12px}}
  .stat-label{{font-size:11px;color:#888780;margin-bottom:4px}}
  .stat-value{{font-size:18px;font-weight:500;color:#2c2c2a}}
  .chart-wrap{{background:#fff;border-radius:12px;padding:20px;margin-bottom:24px;border:1px solid #e0dfd8}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e0dfd8}}
  th{{background:#f1efe8;padding:10px 12px;text-align:left;font-size:12px;font-weight:500;color:#5f5e5a}}
  td{{padding:9px 12px;font-size:12px;border-top:1px solid #f1efe8;color:#3d3d3a}}
  tr:hover td{{background:#faf9f6}}
  .gen-time{{font-size:11px;color:#888780;margin-top:24px}}
</style>
</head>
<body>
<h1>📊 백테스팅 리포트</h1>
<p style="color:#5f5e5a;font-size:13px;margin-bottom:24px">
  생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
  초기자본: {results[0].config.initial_capital:,.0f}원 |
  수수료: {results[0].config.commission_rate*100:.3f}% |
  슬리피지: {results[0].config.slippage_rate*100:.1f}%
</p>

<h2>전략별 성과 요약</h2>
{summary_cards_html}

<h2>자산 추이 (에쿼티 커브)</h2>
<div class="chart-wrap">
  <canvas id="equity-chart" height="90"></canvas>
</div>

<h2>거래 내역 ({sum(r.total_trades for r in results)}건)</h2>
<table>
  <thead>
    <tr>
      <th>종목</th><th>전략</th><th>진입일</th><th>청산일</th>
      <th>진입가</th><th>청산가</th><th>손익%</th><th>손익(원)</th>
      <th>사유</th><th>보유</th>
    </tr>
  </thead>
  <tbody>{trade_rows_html}</tbody>
</table>

<p class="gen-time">생성: {datetime.now().isoformat()}</p>

<script>
const ctx = document.getElementById('equity-chart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{ datasets: {json.dumps(datasets)} }},
  options: {{
    responsive: true,
    interaction: {{ intersect: false, mode: 'index' }},
    plugins: {{
      legend: {{ position: 'top', labels: {{ font: {{ size: 12 }}, boxWidth: 16 }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ctx.dataset.label + ': ' +
            Math.round(ctx.parsed.y).toLocaleString() + '원'
        }}
      }}
    }},
    scales: {{
      x: {{
        type: 'time',
        time: {{ unit: 'month', displayFormats: {{ month: 'yy.MM' }} }},
        grid: {{ color: '#f1efe8' }},
        ticks: {{ font: {{ size: 11 }} }}
      }},
      y: {{
        grid: {{ color: '#f1efe8' }},
        ticks: {{
          font: {{ size: 11 }},
          callback: v => (v/10000).toFixed(0) + '만'
        }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""

    report_path.write_text(html, encoding="utf-8")
    logger.info("HTML 리포트 생성: {}", report_path)
    return report_path


def save_trades_csv(results: list[BacktestResult], filename: Optional[str] = None) -> Path:
    """거래 내역을 CSV로 저장한다."""
    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"trades_{ts}.csv"

    rows = []
    for r in results:
        for t in r.trades:
            if t.exit_date:
                rows.append({
                    "ticker":       t.ticker,
                    "strategy":     r.strategy_name,
                    "entry_date":   t.entry_date,
                    "exit_date":    t.exit_date,
                    "entry_price":  t.entry_price,
                    "exit_price":   t.exit_price,
                    "qty":          t.qty,
                    "pnl":          round(t.pnl, 0),
                    "pnl_pct":      round(t.pnl_pct, 2),
                    "holding_days": t.holding_days,
                    "exit_reason":  t.exit_reason,
                })

    csv_path = REPORT_DIR / filename
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("거래 내역 CSV: {}", csv_path)
    return csv_path
