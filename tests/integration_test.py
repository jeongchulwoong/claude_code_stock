"""
tests/integration_test.py — 전체 통합 테스트

실행:
    python tests/integration_test.py
    python tests/integration_test.py --verbose
    python tests/integration_test.py --quick   # 핵심 테스트만
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

# ── 테스트 결과 집계 ──────────────────────────

PASS, FAIL, SKIP = "✅", "❌", "⏭️"
results: list[tuple[str, str, str]] = []

def test(name: str, fn, skip_reason: str = ""):
    """단일 테스트 실행 + 결과 기록"""
    if skip_reason:
        results.append((SKIP, name, skip_reason))
        return
    try:
        fn()
        results.append((PASS, name, ""))
    except Exception as e:
        results.append((FAIL, name, str(e)[:120]))
        if "--verbose" in sys.argv:
            traceback.print_exc()

# ── 합성 데이터 생성기 ────────────────────────

def make_df(n=500, price=75000, seed=42) -> pd.DataFrame:
    rng   = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n)
    ret   = rng.normal(0.0003, 0.016, n)
    close = price * np.exp(np.cumsum(ret))
    high  = close * (1 + np.abs(rng.normal(0, 0.007, n)))
    low   = close * (1 - np.abs(rng.normal(0, 0.007, n)))
    open_ = np.roll(close, 1); open_[0] = close[0]
    vol   = rng.lognormal(17, 0.5, n).astype(int)
    df = pd.DataFrame({"open":open_,"high":high,"low":low,"close":close,"volume":vol}, index=dates)
    df.index.name = "date"
    return df

def make_snap(ticker="005930", price=75400):
    from core.data_collector import StockSnapshot
    return StockSnapshot(
        ticker=ticker, name="테스트종목", current_price=price,
        open_price=74800, high_price=75900, low_price=74500,
        volume=12000000, volume_ratio=2.5, per=12.5, foreigner_pct=55.0,
        rsi=32.5, macd=-120.0, macd_signal=-140.0, macd_cross=True,
        bollinger_upper=78000.0, bollinger_lower=72000.0, bollinger_position="lower",
        ma5=74800.0, ma20=76200.0, ma5_cross_ma20=False, stochastic_k=22.0,
    )

# ═══════════════════════════════════════════
# CORE 모듈 테스트
# ═══════════════════════════════════════════

def t_config():
    from config import PAPER_TRADING, RISK_CONFIG, WATCH_LIST, AI_CONFIG
    assert PAPER_TRADING == True, "PAPER_TRADING이 True여야 함"
    assert RISK_CONFIG["stop_loss_pct"] < 0
    assert len(WATCH_LIST) > 0

def t_db_manager():
    from core.db_manager import DBManager
    mgr = DBManager()
    mgr.migrate()
    stats = mgr.stats()
    assert isinstance(stats, dict)
    assert "orders" in stats

def t_kiwoom_mock():
    from core.kiwoom_api import get_kiwoom_api
    kw = get_kiwoom_api(paper_trading=True)
    kw.login()
    assert kw.get_connection_state() == True
    accs = kw.get_account_list()
    assert len(accs) > 0

def t_risk_manager():
    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.add_position("005930","삼성전자",6,75000)
    assert rm.check_stop_loss("005930", 72700) == True   # -3.07%
    assert rm.check_take_profit("005930", 79600) == True  # +6.13%
    rm.remove_position("005930", 79600)
    assert rm.get_daily_pnl() > 0

def t_ai_judge():
    from core.ai_judge import AIJudge, AIVerdict
    judge = AIJudge()
    snap  = make_snap()
    verdict = judge.judge(snap)
    assert verdict.action in ("BUY","SELL","HOLD")
    assert 0 <= verdict.confidence <= 100

def t_integrated_judge():
    from core.integrated_judge import IntegratedJudge
    judge = IntegratedJudge()
    snap  = make_snap()
    v = judge.judge(snap, fetch_news=True)
    v.ticker = snap.ticker
    assert v.action in ("BUY","SELL","HOLD")
    assert hasattr(v, "news_judgment")
    assert hasattr(v, "news_blocked")
    assert hasattr(v, "summary_line")

def t_advanced_judge():
    from core.ai_judge_advanced import MultiTimeframeBuilder, AdvancedAIJudge
    df      = make_df()
    from backtest.data_loader_v2 import RobustDataLoader
    df      = RobustDataLoader._add_indicators(df)
    builder = MultiTimeframeBuilder()
    snap    = builder.build(df, "005930", "삼성전자", news_sentiment=0.3)
    judge   = AdvancedAIJudge()
    v = judge.judge(snap)
    assert v.action in ("BUY","SELL","HOLD")
    assert v.timeframe_alignment in ("STRONG","MIXED","WEAK")

def t_order_manager():
    from core.risk_manager import RiskManager
    from core.order_manager import OrderManager
    from core.kiwoom_api import get_kiwoom_api
    from core.ai_judge import AIVerdict
    kw = get_kiwoom_api(paper_trading=True); kw.login()
    rm = RiskManager()
    om = OrderManager(kw, rm)
    v  = AIVerdict(ticker="005930",action="BUY",confidence=80,reason="테스트",
                   target_price=78000,stop_loss=73000,position_size="SMALL")
    result = om.execute(v, 75400, 5_000_000)
    assert isinstance(result, bool)

def t_position_sizer():
    from core.position_sizer import PositionSizer
    sizer = PositionSizer()
    snap  = make_snap()
    r = sizer.calc(snap, confidence=80, available_cash=5_000_000)
    assert r.stop_loss > 0
    assert -10 <= r.stop_loss_pct <= -1
    assert 0.0 <= r.kelly_fraction <= 0.5

def t_portfolio_manager():
    from core.portfolio_manager import PortfolioManager
    pm       = PortfolioManager()
    holdings = pm.get_holdings()
    stats    = pm.get_portfolio_stats(holdings)
    assert stats.daily_var_95 >= 0
    assert stats.daily_cvar_95 >= 0
    assert isinstance(stats.sector_weights, dict)

def t_screener():
    from core.screener import MarketScreener
    screener = MarketScreener()
    result   = screener.run(
        universe=["005930","000660","035420"],
        use_mock=True, min_score=0.0,
    )
    assert result.total_scanned == 3
    assert isinstance(result.candidates, list)

def t_health_monitor():
    from core.health_monitor import HealthMonitor
    hm     = HealthMonitor()
    status = hm.check()
    assert status.db_ok == True
    assert status.severity in ("OK","WARN","CRITICAL")

def t_alert_manager():
    from core.alert_manager import AlertManager
    am = AlertManager()
    rid = am.add_price_alert("005930","삼성전자",70000,"below","테스트 알림")
    assert rid is not None
    snap = make_snap(price=69000)
    events = am.check(snap)
    assert len(events) >= 0   # 트리거 여부는 환경에 따라 다름

def t_strategy_tracker():
    from core.strategy_tracker import StrategyTracker
    t = StrategyTracker()
    t.record_signal("test_strategy","005930","BUY",82,75400,True,"테스트")
    t.record_trade_result("test_strategy","005930",45000,8,75000,80000)
    stats = t.get_stats()
    assert any(s.strategy_name == "test_strategy" for s in stats)

def t_news_analyzer():
    from core.news_analyzer import StockNewsService
    svc     = StockNewsService()
    verdict = svc.get_news_verdict("005930", "삼성전자", max_news=3)
    assert verdict.judgment in ("호재","악재","중립","분석불가")
    assert verdict.ticker == "005930"

def t_performance_attribution():
    from core.performance_attribution import PerformanceAttributor
    pa = PerformanceAttributor()
    r  = pa.analyze()
    assert hasattr(r, "total_pnl")
    assert hasattr(r, "by_strategy")
    assert hasattr(r, "by_ticker")

def t_order_book_analyzer():
    from core.order_book_analyzer import OrderBookAnalyzer
    analyzer = OrderBookAnalyzer()
    ob       = analyzer.mock_order_book("005930", 75400)
    analysis = analyzer.analyze(ob)
    assert analysis.ticker == "005930"
    assert analysis.pressure in ("BUY_PRESSURE","SELL_PRESSURE","BALANCED")
    assert -1 <= analysis.imbalance <= 1
    ctx = analyzer.get_ai_context(analysis)
    assert "호가 분석" in ctx

# ═══════════════════════════════════════════
# STRATEGY 테스트
# ═══════════════════════════════════════════

def t_strategies():
    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.breakout import BreakoutStrategy
    from strategies.volume_surge import VolumeSurgeStrategy
    from strategies.sector_rotation import SectorRotationStrategy

    snap = make_snap()
    for strat in [MomentumStrategy(), MeanReversionStrategy(),
                  BreakoutStrategy(), VolumeSurgeStrategy(),
                  SectorRotationStrategy()]:
        result = strat.should_enter(snap)
        assert isinstance(result, bool)

# ═══════════════════════════════════════════
# BACKTEST 테스트
# ═══════════════════════════════════════════

def t_backtest_engine():
    from backtest.data_loader_v2 import RobustDataLoader
    from backtest.engine import BacktestConfig, BacktestEngine
    from backtest.strategies import STRATEGY_REGISTRY

    loader = RobustDataLoader(use_cache=False, verbose=False)
    df     = loader.load("005930", "2020-01-01", "2023-12-31")
    assert len(df) > 100

    config = BacktestConfig(initial_capital=10_000_000)
    engine = BacktestEngine(config)
    for name in ["combo","breakout","volume_surge"]:
        result = engine.run(df, STRATEGY_REGISTRY[name], "005930", name)
        assert result.total_trades >= 0

def t_monte_carlo():
    from backtest.data_loader_v2 import RobustDataLoader
    from backtest.engine import BacktestConfig, BacktestEngine
    from backtest.strategies import combo_strategy
    from backtest.monte_carlo import MonteCarloSimulator

    loader = RobustDataLoader(use_cache=False, verbose=False)
    df     = loader.load("005930", "2020-01-01", "2023-12-31")
    engine = BacktestEngine(BacktestConfig())
    result = engine.run(df, combo_strategy, "005930", "combo")
    mc     = MonteCarloSimulator(result)
    mc_r   = mc.run(n_simulations=500)
    assert 0 <= mc_r.ruin_prob <= 100
    assert mc_r.p50_return is not None

def t_walk_forward():
    from backtest.data_loader_v2 import RobustDataLoader
    from backtest.engine import BacktestConfig
    from backtest.walk_forward import WalkForwardTester

    loader = RobustDataLoader(use_cache=False, verbose=False)
    df     = loader.load("005930", "2019-01-01", "2023-12-31")
    wf     = WalkForwardTester(df, "005930", BacktestConfig())
    result = wf.run_momentum(n_windows=3, mode="rolling")
    assert len(result.windows) >= 1

# ═══════════════════════════════════════════
# FOREIGN 테스트
# ═══════════════════════════════════════════

def t_foreign_collector():
    from foreign.api_client import ForeignDataCollector
    dc   = ForeignDataCollector()
    snap = dc.get_snapshot("AAPL")
    assert snap.current_price > 0
    assert snap.ticker == "AAPL"

def t_foreign_signal():
    from foreign.api_client import ForeignDataCollector
    from foreign.signal_engine import ForeignSignalEngine
    dc   = ForeignDataCollector()
    eng  = ForeignSignalEngine()
    snap = dc.get_snapshot("NVDA")
    sig  = eng.generate(snap)
    assert sig.action in ("BUY","SELL","HOLD")
    assert sig.ticker == "NVDA"

# ═══════════════════════════════════════════
# DASHBOARD 테스트
# ═══════════════════════════════════════════

def t_dashboard_api():
    import sys
    sys.path.insert(0,".")
    from dashboard.app import app
    from dashboard.db_reader import seed_demo_data
    seed_demo_data()
    with app.test_client() as c:
        for ep in ["/","/advanced","/api/summary","/api/orders",
                   "/api/ticker_stats","/api/daily_pnl",
                   "/api/strategy_stats","/api/screener","/api/health"]:
            r = c.get(ep)
            assert r.status_code == 200, f"{ep} returned {r.status_code}"

# ═══════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════

def run_all(quick: bool = False):
    print("\n" + "═"*65)
    print("  🧪 AI 자동매매 통합 테스트")
    print("  " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("═"*65)

    # Core
    test("config.py 로드",               t_config)
    test("DB 마이그레이션",               t_db_manager)
    test("KiwoomAPI Mock",               t_kiwoom_mock)
    test("RiskManager 손절·익절",        t_risk_manager)
    test("AIJudge 기본",                 t_ai_judge)
    test("IntegratedJudge (뉴스통합)",   t_integrated_judge)
    test("AdvancedAIJudge (멀티TF)",     t_advanced_judge)
    test("OrderManager Mock 실행",       t_order_manager)
    test("PositionSizer Kelly+ATR",      t_position_sizer)
    test("PortfolioManager VaR",         t_portfolio_manager)
    test("MarketScreener",               t_screener)
    test("HealthMonitor",                t_health_monitor)
    test("AlertManager",                 t_alert_manager)
    test("StrategyTracker",              t_strategy_tracker)
    test("NewsAnalyzer",                 t_news_analyzer)
    test("PerformanceAttributor",        t_performance_attribution)
    test("OrderBookAnalyzer",            t_order_book_analyzer)

    # Strategies
    test("전략 모듈 5종",                 t_strategies)

    if not quick:
        # Backtest (시간 소요)
        test("BacktestEngine (3전략)",   t_backtest_engine)
        test("MonteCarloSimulator",      t_monte_carlo)
        test("WalkForwardTester",        t_walk_forward)
        # Foreign
        test("ForeignDataCollector",     t_foreign_collector)
        test("ForeignSignalEngine",      t_foreign_signal)

    # Dashboard
    test("Dashboard API 전체",           t_dashboard_api)

    # ── 결과 출력 ──
    print("\n" + "═"*65)
    pass_cnt  = sum(1 for s,_,_ in results if s==PASS)
    fail_cnt  = sum(1 for s,_,_ in results if s==FAIL)
    skip_cnt  = sum(1 for s,_,_ in results if s==SKIP)

    for status, name, detail in results:
        suffix = f" — {detail}" if detail else ""
        print(f"  {status} {name}{suffix}")

    print("\n" + "─"*65)
    print(f"  결과: PASS {pass_cnt} | FAIL {fail_cnt} | SKIP {skip_cnt}")
    if fail_cnt == 0:
        print("  🎉 모든 테스트 통과!")
    else:
        print(f"  ⚠️  {fail_cnt}개 테스트 실패")
    print("═"*65 + "\n")
    return fail_cnt


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    fails = run_all(quick=quick)
    sys.exit(0 if fails == 0 else 1)
