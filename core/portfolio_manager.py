"""
core/portfolio_manager.py — 포트폴리오 관리 + 상관관계 분석 + VaR/CVaR

기능:
  1. 전체 포트폴리오 현황 (보유 종목, 평가손익, 비중)
  2. 종목 간 상관관계 분석 (상관계수 행렬)
  3. 포트폴리오 VaR / CVaR 계산
  4. 섹터별 분산 현황
  5. 리밸런싱 제안
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import DB_PATH

# ── 데이터 구조 ───────────────────────────────

@dataclass
class HoldingInfo:
    ticker:       str
    name:         str
    qty:          int
    avg_price:    float
    current_price: float
    market_value: float        # 평가금액
    pnl:          float        # 평가손익
    pnl_pct:      float        # 손익률 %
    weight:       float        # 포트폴리오 내 비중 %
    sector:       str = "기타"


@dataclass
class PortfolioStats:
    total_invested:   float    # 총 투자금
    total_value:      float    # 총 평가금액
    total_pnl:        float    # 총 평가손익
    total_pnl_pct:    float    # 총 손익률 %
    realized_pnl:     float    # 실현손익 (DB 기반)
    daily_var_95:     float    # 95% VaR (일간)
    daily_cvar_95:    float    # 95% CVaR (일간)
    max_single_weight: float   # 최대 단일 종목 비중 %
    holdings_count:   int
    sector_weights:   dict[str, float]   # 섹터별 비중
    generated_at:     str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CorrelationResult:
    tickers:    list[str]
    matrix:     pd.DataFrame    # 상관계수 행렬
    high_corr_pairs: list[tuple]  # 상관계수 > 0.7인 쌍
    avg_correlation: float


# ── 포트폴리오 매니저 ─────────────────────────

class PortfolioManager:
    """
    현재 보유 포지션 전체를 관리하고
    리스크 지표를 계산하는 클래스.
    """

    SECTOR_MAP = {
        "005930": "반도체", "000660": "반도체", "042700": "반도체",
        "035420": "IT",     "035720": "IT",     "259960": "IT",
        "051910": "화학",   "006400": "화학",   "096770": "에너지",
        "005380": "자동차", "000270": "자동차", "012330": "자동차",
        "068270": "바이오", "207940": "바이오", "326030": "바이오",
        "AAPL":  "Tech",   "MSFT":  "Tech",   "GOOGL": "Tech",
        "NVDA":  "Tech",   "AMD":   "Tech",   "META":  "Tech",
        "TSLA":  "EV",     "AMZN":  "Consumer","NFLX": "Media",
    }
    NAME_MAP = {
        "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
        "051910": "LG화학",   "006400": "삼성SDI",   "005380": "현대차",
        "068270": "셀트리온", "207940": "삼성바이오",
        "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
        "GOOGL": "Alphabet", "TSLA": "Tesla", "META": "Meta",
    }

    def __init__(self, risk_manager=None) -> None:
        self._rm = risk_manager
        self._init_portfolio_db()

    # ── 포트폴리오 현황 ───────────────────────

    def get_holdings(self, price_map: dict[str, float] = None) -> list[HoldingInfo]:
        """현재 보유 종목 목록. price_map: {ticker: current_price}"""
        positions = {}
        if self._rm:
            positions = self._rm.get_positions()
        else:
            positions = self._load_positions_from_db()

        if not positions:
            return []

        holdings = []
        total_value = 0.0

        for ticker, pos in positions.items():
            current = (price_map or {}).get(ticker, pos.avg_price)
            mv  = pos.qty * current
            pnl = mv - pos.qty * pos.avg_price
            total_value += mv
            holdings.append(HoldingInfo(
                ticker        = ticker,
                name          = self.NAME_MAP.get(ticker, ticker),
                qty           = pos.qty,
                avg_price     = pos.avg_price,
                current_price = current,
                market_value  = mv,
                pnl           = pnl,
                pnl_pct       = pnl / (pos.qty * pos.avg_price) * 100,
                weight        = 0.0,
                sector        = self.SECTOR_MAP.get(ticker, "기타"),
            ))

        # 비중 계산
        for h in holdings:
            h.weight = h.market_value / total_value * 100 if total_value > 0 else 0

        return sorted(holdings, key=lambda x: -x.market_value)

    def get_portfolio_stats(
        self,
        holdings:  list[HoldingInfo],
        daily_returns_map: dict[str, pd.Series] = None,
    ) -> PortfolioStats:
        """포트폴리오 전체 통계 + VaR/CVaR"""
        if not holdings:
            return PortfolioStats(
                total_invested=0, total_value=0, total_pnl=0,
                total_pnl_pct=0, realized_pnl=self._get_realized_pnl(),
                daily_var_95=0, daily_cvar_95=0,
                max_single_weight=0, holdings_count=0, sector_weights={},
            )

        total_invested = sum(h.qty * h.avg_price for h in holdings)
        total_value    = sum(h.market_value for h in holdings)
        total_pnl      = total_value - total_invested
        total_pnl_pct  = total_pnl / total_invested * 100 if total_invested else 0

        # 섹터 비중
        sector_weights: dict[str, float] = {}
        for h in holdings:
            sector_weights[h.sector] = sector_weights.get(h.sector, 0) + h.weight

        # VaR / CVaR
        var_95, cvar_95 = self._calc_var_cvar(holdings, daily_returns_map, total_value)

        return PortfolioStats(
            total_invested    = total_invested,
            total_value       = total_value,
            total_pnl         = total_pnl,
            total_pnl_pct     = total_pnl_pct,
            realized_pnl      = self._get_realized_pnl(),
            daily_var_95      = var_95,
            daily_cvar_95     = cvar_95,
            max_single_weight = max(h.weight for h in holdings),
            holdings_count    = len(holdings),
            sector_weights    = sector_weights,
        )

    # ── 상관관계 분석 ─────────────────────────

    def calc_correlation(
        self,
        returns_map: dict[str, pd.Series],
        threshold:   float = 0.7,
    ) -> CorrelationResult:
        """
        종목 간 일간 수익률 상관계수 행렬 계산.
        상관계수 > threshold 인 쌍을 high_corr_pairs로 반환.
        (과도한 분산 집중 경고)
        """
        tickers = list(returns_map.keys())
        df = pd.DataFrame(returns_map).dropna()
        matrix = df.corr().round(3)

        high_pairs = []
        for i, t1 in enumerate(tickers):
            for t2 in tickers[i+1:]:
                if t1 in matrix.columns and t2 in matrix.columns:
                    corr = matrix.loc[t1, t2]
                    if abs(corr) >= threshold:
                        high_pairs.append((t1, t2, corr))

        # 평균 상관계수 (대각선 제외)
        vals = []
        for i in range(len(matrix)):
            for j in range(i+1, len(matrix)):
                vals.append(matrix.iloc[i, j])
        avg_corr = float(np.mean(vals)) if vals else 0.0

        if high_pairs:
            logger.warning(
                "⚠️ 고상관 종목 쌍 발견 (>{:.0%}): {}",
                threshold,
                [(t1, t2, f"{c:.2f}") for t1, t2, c in high_pairs],
            )

        return CorrelationResult(
            tickers          = tickers,
            matrix           = matrix,
            high_corr_pairs  = high_pairs,
            avg_correlation  = avg_corr,
        )

    # ── 리밸런싱 제안 ─────────────────────────

    def suggest_rebalancing(
        self,
        holdings: list[HoldingInfo],
        target_weight: float = None,   # None이면 균등 비중
    ) -> list[dict]:
        """
        목표 비중 대비 현재 비중을 비교하여
        매수/매도 제안을 반환한다.
        """
        if not holdings:
            return []

        n = len(holdings)
        target = target_weight or (100 / n)
        suggestions = []

        for h in holdings:
            diff = h.weight - target
            if abs(diff) < 3:   # 3% 미만 차이는 무시
                continue
            action = "매도 (비중 축소)" if diff > 0 else "매수 (비중 확대)"
            suggestions.append({
                "ticker":   h.ticker,
                "name":     h.name,
                "current":  f"{h.weight:.1f}%",
                "target":   f"{target:.1f}%",
                "diff":     f"{diff:+.1f}%",
                "action":   action,
            })

        return sorted(suggestions, key=lambda x: abs(float(x["diff"].rstrip("%"))), reverse=True)

    # ── VaR / CVaR ────────────────────────────

    @staticmethod
    def _calc_var_cvar(
        holdings:          list[HoldingInfo],
        daily_returns_map: Optional[dict[str, pd.Series]],
        total_value:       float,
        confidence:        float = 0.95,
        n_scenarios:       int   = 10_000,
    ) -> tuple[float, float]:
        """
        Historical VaR + CVaR (95% 신뢰수준).
        returns_map 없으면 정규분포 몬테카를로로 대체.
        """
        if total_value <= 0:
            return 0.0, 0.0

        weights = np.array([h.weight / 100 for h in holdings])

        if daily_returns_map and len(daily_returns_map) == len(holdings):
            # Historical VaR
            tickers = [h.ticker for h in holdings]
            rets_df = pd.DataFrame({
                t: daily_returns_map.get(t, pd.Series(dtype=float))
                for t in tickers
            }).dropna()
            if len(rets_df) > 30:
                port_rets = (rets_df * weights).sum(axis=1)
                var_95  = float(np.percentile(port_rets, (1 - confidence) * 100))
                tail    = port_rets[port_rets <= var_95]
                cvar_95 = float(tail.mean()) if len(tail) > 0 else var_95
                return abs(var_95 * total_value), abs(cvar_95 * total_value)

        # 몬테카를로 VaR (일간 변동성 가정: 국내 평균 1.5%)
        rng     = np.random.default_rng(42)
        mu      = 0.0005    # 일간 기대수익 0.05%
        sigma   = 0.015     # 일간 변동성 1.5%
        sim_rets= rng.normal(mu, sigma, n_scenarios)
        var_95  = float(np.percentile(sim_rets, (1 - confidence) * 100))
        tail    = sim_rets[sim_rets <= var_95]
        cvar_95 = float(tail.mean()) if len(tail) > 0 else var_95

        return abs(var_95 * total_value), abs(cvar_95 * total_value)

    # ── DB 헬퍼 ──────────────────────────────

    def _init_portfolio_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT,
                    total_value   REAL,
                    total_pnl     REAL,
                    holdings_json TEXT
                )
            """)

    def save_snapshot(self, stats: PortfolioStats, holdings: list[HoldingInfo]) -> None:
        """일별 포트폴리오 스냅샷 저장"""
        import json
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO portfolio_snapshots VALUES (NULL,?,?,?,?)",
                (
                    date.today().isoformat(),
                    stats.total_value,
                    stats.total_pnl,
                    json.dumps([{
                        "ticker": h.ticker, "qty": h.qty,
                        "avg": h.avg_price, "current": h.current_price,
                        "pnl_pct": h.pnl_pct,
                    } for h in holdings]),
                )
            )

    def _get_realized_pnl(self) -> float:
        """DB에서 실현손익 계산"""
        try:
            with sqlite3.connect(DB_PATH) as con:
                buy_amt  = con.execute(
                    "SELECT COALESCE(SUM(qty*price),0) FROM orders "
                    "WHERE order_type='BUY' AND status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0]
                sell_amt = con.execute(
                    "SELECT COALESCE(SUM(qty*price),0) FROM orders "
                    "WHERE order_type='SELL' AND status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0]
            return sell_amt - buy_amt
        except Exception:
            return 0.0

    def _load_positions_from_db(self) -> dict:
        """DB orders 테이블에서 포지션 재구성"""
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT ticker, order_type, qty, price FROM orders "
                    "WHERE status IN ('FILLED','PAPER_FILLED') ORDER BY timestamp"
                ).fetchall()
        except Exception:
            return {}

        positions = {}
        for ticker, otype, qty, price in rows:
            if ticker not in positions:
                positions[ticker] = {"qty": 0, "cost": 0.0}
            if otype == "BUY":
                positions[ticker]["cost"] += qty * price
                positions[ticker]["qty"]  += qty
            elif otype == "SELL":
                positions[ticker]["qty"]  -= qty
                if positions[ticker]["qty"] > 0:
                    positions[ticker]["cost"] *= (1 - qty / max(positions[ticker]["qty"] + qty, 1))

        # 잔여 포지션만
        class MockPos:
            def __init__(self, qty, avg_price):
                self.qty = qty
                self.avg_price = avg_price

        return {
            t: MockPos(d["qty"], d["cost"] / d["qty"] if d["qty"] > 0 else 0)
            for t, d in positions.items()
            if d["qty"] > 0
        }

    # ── 터미널 출력 ───────────────────────────

    def print_holdings(self, holdings: list[HoldingInfo], stats: PortfolioStats) -> None:
        print("\n" + "═"*70)
        print("  💼 포트폴리오 현황")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("═"*70)
        print(f"  총 평가금액: {stats.total_value:>14,.0f}원")
        print(f"  총 투자금액: {stats.total_invested:>14,.0f}원")
        c = "\033[92m" if stats.total_pnl >= 0 else "\033[91m"
        R = "\033[0m"
        print(f"  평가 손익:   {c}{stats.total_pnl:>+14,.0f}원  ({stats.total_pnl_pct:+.2f}%){R}")
        print(f"  실현 손익:   {stats.realized_pnl:>+14,.0f}원")
        print(f"  일간 VaR(95%): {stats.daily_var_95:>10,.0f}원  |  CVaR: {stats.daily_cvar_95:>10,.0f}원")
        print("─"*70)
        print(f"  {'종목':<14} {'섹터':<8} {'수량':>5} {'평단':>10} {'현재가':>10} {'손익%':>8} {'비중':>6}")
        print("─"*70)
        for h in holdings:
            c = "\033[92m" if h.pnl >= 0 else "\033[91m"
            print(
                f"  {h.name[:8]:<8}({h.ticker[:6]}) {h.sector:<8} "
                f"{h.qty:>5} {h.avg_price:>10,.0f} {h.current_price:>10,.0f} "
                f"{c}{h.pnl_pct:>+7.2f}%{R} {h.weight:>5.1f}%"
            )
        print("─"*70)
        print("  [섹터 비중]")
        for sector, w in sorted(stats.sector_weights.items(), key=lambda x: -x[1]):
            bar = "█" * int(w / 5)
            print(f"  {sector:<10} {bar:<20} {w:>5.1f}%")
        print("═"*70 + "\n")
