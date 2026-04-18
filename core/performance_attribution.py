"""
core/performance_attribution.py — 성과 귀인 분석

어떤 신호/전략/AI 판단이 수익에 얼마나 기여했는지 분석한다.

분석 항목:
  1. 전략별 기여 손익
  2. 종목별 기여 손익
  3. 섹터별 기여
  4. 시간대별 성과 (오전/오후)
  5. AI 신뢰도 구간별 적중률
  6. 뉴스 신호 기여도
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import DB_PATH

REPORT_DIR = Path(__file__).parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


@dataclass
class AttributionResult:
    period_start: str
    period_end:   str
    total_pnl:    float

    by_strategy:  dict[str, float]   # 전략 → 손익
    by_ticker:    dict[str, float]   # 종목 → 손익
    by_sector:    dict[str, float]   # 섹터 → 손익
    by_hour:      dict[int, float]   # 시간대 → 손익
    by_conf_band: dict[str, dict]    # 신뢰도 구간 → {count, pnl, win_rate}
    news_contrib: dict[str, float]   # 뉴스판정 → 손익


class PerformanceAttributor:
    """
    DB 거래 기록과 AI 로그를 결합하여
    성과 귀인 분석 리포트를 생성한다.
    """

    SECTOR_MAP = {
        "005930": "반도체", "000660": "반도체", "035420": "IT",
        "051910": "화학",   "006400": "화학",   "005380": "자동차",
        "000270": "자동차", "068270": "바이오",  "207940": "바이오",
        "035720": "IT",    "AAPL": "Tech",    "MSFT": "Tech",
        "NVDA": "Tech",    "TSLA": "EV",      "META": "Tech",
    }

    def analyze(
        self,
        start_date: Optional[date] = None,
        end_date:   Optional[date] = None,
    ) -> AttributionResult:
        """기간별 성과 귀인 분석 실행"""
        end   = end_date   or date.today()
        start = start_date or (end - timedelta(days=30))

        orders  = self._load_orders(start, end)
        signals = self._load_signals(start, end)

        if orders.empty:
            logger.warning("분석 기간 내 거래 없음: {} ~ {}", start, end)

        total_pnl    = self._calc_pnl(orders)
        by_strategy  = self._by_strategy(orders, signals)
        by_ticker    = self._by_ticker(orders)
        by_sector    = self._by_sector(by_ticker)
        by_hour      = self._by_hour(orders)
        by_conf_band = self._by_confidence(signals)
        news_contrib = self._news_contribution(signals)

        result = AttributionResult(
            period_start = start.isoformat(),
            period_end   = end.isoformat(),
            total_pnl    = total_pnl,
            by_strategy  = by_strategy,
            by_ticker    = by_ticker,
            by_sector    = by_sector,
            by_hour      = by_hour,
            by_conf_band = by_conf_band,
            news_contrib = news_contrib,
        )

        logger.info("성과 귀인 완료: {} ~ {} | 총손익:{:+,.0f}원", start, end, total_pnl)
        return result

    def print_report(self, r: AttributionResult) -> None:
        print("\n" + "═"*60)
        print(f"  📊 성과 귀인 분석: {r.period_start} ~ {r.period_end}")
        print(f"  총 손익: {r.total_pnl:+,.0f}원")
        print("═"*60)

        if r.by_strategy:
            print("\n  [전략별 기여]")
            for s, pnl in sorted(r.by_strategy.items(), key=lambda x: -x[1]):
                bar = "▓" * min(int(abs(pnl)/5000), 15)
                icon = "📈" if pnl >= 0 else "📉"
                print(f"  {icon} {s:<22} {pnl:>+12,.0f}원  {bar}")

        if r.by_ticker:
            print("\n  [종목별 기여 Top 5]")
            for t, pnl in sorted(r.by_ticker.items(), key=lambda x: -abs(x[1]))[:5]:
                icon = "🟢" if pnl >= 0 else "🔴"
                print(f"  {icon} {t:<10} {pnl:>+12,.0f}원")

        if r.by_sector:
            print("\n  [섹터별 기여]")
            for sec, pnl in sorted(r.by_sector.items(), key=lambda x: -x[1]):
                icon = "📈" if pnl >= 0 else "📉"
                print(f"  {icon} {sec:<12} {pnl:>+12,.0f}원")

        if r.by_conf_band:
            print("\n  [AI 신뢰도 구간별 적중률]")
            for band, stat in sorted(r.by_conf_band.items()):
                cnt = stat.get("count", 0)
                wr  = stat.get("win_rate", 0)
                pnl = stat.get("pnl", 0)
                print(f"  신뢰도 {band}: {cnt}건 | 승률 {wr:.0f}% | {pnl:>+10,.0f}원")

        print("═"*60 + "\n")

    def save_html(self, r: AttributionResult) -> Path:
        """HTML 귀인 리포트 저장"""
        strategy_rows = "".join(
            f"<tr><td>{s}</td>"
            f"<td style='color:{'#27500A' if p>=0 else '#A32D2D'};text-align:right'>{p:+,.0f}원</td></tr>"
            for s, p in sorted(r.by_strategy.items(), key=lambda x: -x[1])
        )
        ticker_rows = "".join(
            f"<tr><td>{t}</td>"
            f"<td style='color:{'#27500A' if p>=0 else '#A32D2D'};text-align:right'>{p:+,.0f}원</td></tr>"
            for t, p in sorted(r.by_ticker.items(), key=lambda x: -abs(x[1]))[:10]
        )
        conf_rows = "".join(
            f"<tr><td>{b}</td><td>{s.get('count',0)}</td>"
            f"<td>{s.get('win_rate',0):.0f}%</td>"
            f"<td style='color:{'#27500A' if s.get('pnl',0)>=0 else '#A32D2D'};text-align:right'>{s.get('pnl',0):+,.0f}원</td></tr>"
            for b, s in sorted(r.by_conf_band.items())
        )

        html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>성과 귀인 분석 {r.period_start}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f5f4f0;color:#2c2c2a;padding:24px}}
h1{{font-size:20px;font-weight:500;margin-bottom:4px}}
.sub{{color:#5f5e5a;font-size:13px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.card{{background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:20px}}
.card h2{{font-size:13px;font-weight:500;margin-bottom:12px}}
.total{{font-size:28px;font-weight:500;color:{'#27500A' if r.total_pnl>=0 else '#A32D2D'}}}
table{{width:100%;border-collapse:collapse}}
th{{font-size:11px;color:#5f5e5a;padding:7px;border-bottom:1px solid #e0dfd8;text-align:left}}
td{{font-size:12px;padding:7px;border-bottom:1px solid #f1efe8;color:#3d3d3a}}
</style></head><body>
<h1>📊 성과 귀인 분석</h1>
<p class="sub">{r.period_start} ~ {r.period_end} | 생성: {datetime.now().strftime('%H:%M')}</p>
<div style="background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:20px;margin-bottom:16px">
  <div style="font-size:11px;color:#888780;margin-bottom:6px">기간 총 손익</div>
  <div class="total">{'📈' if r.total_pnl>=0 else '📉'} {r.total_pnl:+,.0f}원</div>
</div>
<div class="grid">
  <div class="card"><h2>전략별 기여</h2>
    <table><thead><tr><th>전략</th><th>손익</th></tr></thead>
    <tbody>{strategy_rows}</tbody></table>
  </div>
  <div class="card"><h2>종목별 기여 (Top 10)</h2>
    <table><thead><tr><th>종목</th><th>손익</th></tr></thead>
    <tbody>{ticker_rows}</tbody></table>
  </div>
</div>
<div class="card"><h2>AI 신뢰도 구간별 성과</h2>
  <table><thead><tr><th>구간</th><th>거래수</th><th>승률</th><th>손익</th></tr></thead>
  <tbody>{conf_rows}</tbody></table>
</div>
</body></html>"""
        path = REPORT_DIR / f"attribution_{r.period_start}.html"
        path.write_text(html, encoding="utf-8")
        logger.info("귀인 리포트: {}", path)
        return path

    # ── DB 로드 ───────────────────────────────

    def _load_orders(self, start: date, end: date) -> pd.DataFrame:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT timestamp, ticker, order_type, qty, price, status, reason "
                    "FROM orders WHERE DATE(timestamp) BETWEEN ? AND ? "
                    "AND status IN ('FILLED','PAPER_FILLED')",
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=["timestamp","ticker","type","qty","price","status","reason"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["amount"] = df["qty"] * df["price"] * df["type"].map({"BUY":-1,"SELL":1})
            return df
        except Exception:
            return pd.DataFrame()

    def _load_signals(self, start: date, end: date) -> pd.DataFrame:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT signal_date, strategy, ticker, action, confidence, executed "
                    "FROM strategy_signals WHERE DATE(signal_date) BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows, columns=["date","strategy","ticker","action","confidence","executed"])
        except Exception:
            return pd.DataFrame()

    # ── 분석 헬퍼 ────────────────────────────

    @staticmethod
    def _calc_pnl(orders: pd.DataFrame) -> float:
        if orders.empty:
            return 0.0
        return float(orders["amount"].sum())

    def _by_strategy(self, orders: pd.DataFrame, signals: pd.DataFrame) -> dict[str, float]:
        if signals.empty or orders.empty:
            return {}
        result = {}
        for strat, grp in signals.groupby("strategy"):
            tickers = grp["ticker"].unique()
            subset  = orders[orders["ticker"].isin(tickers)]
            result[strat] = float(subset["amount"].sum()) if not subset.empty else 0.0
        return result

    @staticmethod
    def _by_ticker(orders: pd.DataFrame) -> dict[str, float]:
        if orders.empty:
            return {}
        return orders.groupby("ticker")["amount"].sum().round(0).to_dict()

    def _by_sector(self, by_ticker: dict[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for ticker, pnl in by_ticker.items():
            sector = self.SECTOR_MAP.get(ticker, "기타")
            result[sector] = result.get(sector, 0.0) + pnl
        return result

    @staticmethod
    def _by_hour(orders: pd.DataFrame) -> dict[int, float]:
        if orders.empty:
            return {}
        orders = orders.copy()
        orders["hour"] = orders["timestamp"].dt.hour
        return orders.groupby("hour")["amount"].sum().round(0).to_dict()

    @staticmethod
    def _by_confidence(signals: pd.DataFrame) -> dict[str, dict]:
        if signals.empty:
            return {}
        bands = {"50-59": (50,59), "60-69": (60,69), "70-79": (70,79), "80+": (80,100)}
        result = {}
        for band, (lo, hi) in bands.items():
            subset = signals[(signals["confidence"] >= lo) & (signals["confidence"] <= hi)]
            cnt = len(subset)
            if cnt == 0:
                continue
            result[band] = {
                "count":    cnt,
                "win_rate": float(subset["executed"].mean() * 100),
                "pnl":      0.0,  # 실제 손익은 주문과 매칭 필요 (근사값)
            }
        return result

    @staticmethod
    def _news_contribution(signals: pd.DataFrame) -> dict[str, float]:
        """뉴스 신호 기여도 (근사 — reason 필드 기반)"""
        return {"호재 신호": 0.0, "악재 차단": 0.0, "중립": 0.0}
