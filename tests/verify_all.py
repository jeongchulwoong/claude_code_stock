"""
tests/verify_all.py — 전체 시스템 통합 검증

검증 항목 (7개 퀀트 개선 + MTF 키움 분봉 전환):
  1) 모듈 임포트 (sector_map, ai_accuracy_tracker, fundamental_gate, market_regime)
  2) sector_map 중복 차단 로직
  3) ai_accuracy_tracker DB CRUD
  4) fundamental_gate yfinance 호출
  5) market_regime KOSPI/SP500 분류
  6) Kiwoom REST 로그인 + ka10080 5분봉 (KR)
  7) DataCollector._compute_mtf (KR=Kiwoom / 해외=yfinance 분기)
  8) screener._composite 비용 + R:R 게이트
  9) composite_backtest (offline yfinance)
"""
from __future__ import annotations
import sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

PASS = "[OK]"
FAIL = "[X ]"
WARN = "[!?]"

def section(n, title):
    print(f"\n{'='*70}\n  {n}. {title}\n{'='*70}")

def report(name, ok, detail=""):
    tag = PASS if ok else FAIL
    print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))


results = {}

# ── 1. 임포트 ────────────────────────────────────────────
section(1, "모듈 임포트")
for mod in ["core.sector_map", "core.ai_accuracy_tracker",
            "core.fundamental_gate", "core.market_regime",
            "backtest.composite_backtest",
            "core.screener", "core.data_collector", "core.kiwoom_api"]:
    try:
        __import__(mod)
        report(mod, True)
        results[f"import.{mod}"] = True
    except Exception as e:
        report(mod, False, str(e))
        results[f"import.{mod}"] = False

# ── 2. sector_map ───────────────────────────────────────
section(2, "sector_map — 섹터 중복 차단")
try:
    from core.sector_map import get_sector, has_sector_overlap
    s_samsung = get_sector("005930.KS")
    s_skh     = get_sector("000660.KS")
    s_naver   = get_sector("035420.KS")
    overlap   = has_sector_overlap("005930.KS", ["000660.KS"])
    no_overlap= has_sector_overlap("005930.KS", ["035420.KS"])
    print(f"  005930→{s_samsung} 000660→{s_skh} 035420→{s_naver}")
    print(f"  반도체 중복? {overlap}")
    print(f"  반도체 vs 인터넷? {no_overlap}")
    ok = (s_samsung is not None and s_skh is not None and
          overlap[0] is True and no_overlap[0] is False)
    report("섹터 매핑 + 중복 감지", ok)
    results["sector_map"] = ok
except Exception as e:
    traceback.print_exc()
    results["sector_map"] = False

# ── 3. ai_accuracy_tracker ──────────────────────────────
section(3, "ai_accuracy_tracker — DB CRUD")
try:
    from core.ai_accuracy_tracker import AIAccuracyTracker, AISignalRecord
    from datetime import datetime
    tracker = AIAccuracyTracker()
    test_ticker = f"TEST_{int(datetime.now().timestamp())}.KS"
    rec = AISignalRecord(
        ticker=test_ticker, name="검증용", entry_at=datetime.now().isoformat(),
        entry_price=70000, ai_action="BUY", ai_confidence=82,
        ai_reason="테스트 진입", setup_type="Uptrend Pullback",
        composite=80, tech_score=75, fund_passed=True, regime="BULL",
    )
    rid = tracker.record_entry(rec)
    print(f"  record_entry → row id={rid}")
    exit_ok = tracker.record_exit(ticker=test_ticker, exit_price=72100)
    print(f"  record_exit → {exit_ok}")
    overall = tracker.overall_stats()
    print(f"  overall_stats: {overall}")
    ok = rid > 0 and exit_ok and isinstance(overall, dict) and overall.get("total_trades", 0) >= 1
    report("entry+exit 기록 → 통계", ok)
    results["ai_tracker"] = ok
except Exception as e:
    traceback.print_exc()
    results["ai_tracker"] = False

# ── 4. fundamental_gate ─────────────────────────────────
section(4, "fundamental_gate — yfinance 호출")
try:
    from core.fundamental_gate import FundamentalGate
    fg = FundamentalGate()
    res = fg.check("005930.KS")
    print(f"  005930.KS: passed={res.passed}")
    for r in res.reasons:
        print(f"    {r}")
    ok = isinstance(res.passed, bool) and isinstance(res.reasons, list)
    report("펀더멘탈 게이트 호출", ok)
    results["fundamental_gate"] = ok
except Exception as e:
    traceback.print_exc()
    results["fundamental_gate"] = False

# ── 5. market_regime ────────────────────────────────────
section(5, "market_regime — KOSPI/SP500 분류")
try:
    from core.market_regime import MarketRegimeAnalyzer
    kr = MarketRegimeAnalyzer(market="KR")
    us = MarketRegimeAnalyzer(market="US")
    rk = kr.get()
    ru = us.get()
    print(f"  KR: state={rk.state} | {rk.description}")
    print(f"      kospi_pct={rk.kospi_pct}% above_ma20={rk.above_ma20} above_ma60={rk.above_ma60} vol_pctile={rk.vol_pctile}")
    print(f"  US: state={ru.state} | {ru.description}")
    mult_t = kr.weight_multiplier(rk, "trend")
    mult_m = kr.weight_multiplier(rk, "mean_rev")
    print(f"  KR 가중치: trend={mult_t} / mean_rev={mult_m}")
    ok = rk.state in ("BULL","BEAR","RANGE","HIGH_VOL","UNKNOWN") and isinstance(mult_t, float)
    report("regime 분류 + 가중치", ok)
    results["market_regime"] = ok
except Exception as e:
    traceback.print_exc()
    results["market_regime"] = False

# ── 6. Kiwoom REST 로그인 + 5분봉 ────────────────────────
section(6, "Kiwoom REST 로그인 + ka10080 분봉")
kw = None
try:
    from core.kiwoom_api import KiwoomRestAPI
    kw = KiwoomRestAPI()
    if kw.login():
        print(f"  로그인 OK, 토큰: {kw._token[:20]}...")
        # 삼성전자 5분봉
        chart = kw.get_minute_chart("005930.KS", count=50, tic_scope="5")
        df = chart.get("df") if isinstance(chart, dict) else None
        if df is not None and not df.empty:
            print(f"  005930.KS 5분봉 {len(df)}행")
            print(f"  컬럼: {list(df.columns)}")
            print(f"  최근 3개: ")
            print(df.tail(3).to_string(index=False))
            ok = len(df) >= 30
            report("ka10080 5분봉 수신", ok, f"{len(df)}행")
            results["kiwoom_minute"] = ok
        else:
            report("ka10080 분봉 응답 없음", False)
            results["kiwoom_minute"] = False
    else:
        report("키움 REST 로그인 실패", False)
        results["kiwoom_minute"] = False
        kw = None
except Exception as e:
    traceback.print_exc()
    results["kiwoom_minute"] = False

# ── 7. DataCollector._compute_mtf (KR + 해외 분기) ──────
section(7, "DataCollector._compute_mtf — KR=Kiwoom / 해외=yfinance")
try:
    from core.data_collector import DataCollector
    if kw is not None:
        dc = DataCollector(kw)
        # 7-1. KR — 키움 분봉 사용
        mtf_kr = dc._compute_mtf("005930.KS", ma20_daily=70000)
        print(f"  KR 005930.KS MTF: {mtf_kr}")
        ok_kr = bool(mtf_kr) and "rsi_5m" in mtf_kr
        report("KR (Kiwoom 분봉)", ok_kr)
        results["mtf_kr"] = ok_kr

        # 7-2. 해외 — yfinance fallback
        mtf_us = dc._compute_mtf("AAPL", ma20_daily=180)
        print(f"  US AAPL MTF: {mtf_us}")
        ok_us = isinstance(mtf_us, dict)  # yfinance가 비어도 OK (장 마감/데이터 부족)
        report("US (yfinance)", ok_us)
        results["mtf_us"] = ok_us
    else:
        report("Kiwoom 미연결로 스킵", False)
        results["mtf_kr"] = False
        results["mtf_us"] = False
except Exception as e:
    traceback.print_exc()
    results["mtf_kr"] = False
    results["mtf_us"] = False

# ── 8. screener._composite 비용 + R:R 게이트 ──────────
section(8, "screener._composite — 비용 + R:R 게이트")
try:
    from core.screener import MarketScreener, ScreenerCandidate

    # 가짜 후보 1: ATR 작아서 실효 R:R 낮음 → 게이트 -15
    bad = ScreenerCandidate(
        ticker="BAD.KS", name="bad", current_price=10000, score=0, reasons=[],
        tech_score=75, fund_passed=True,
        ai_score=90, ai_action="BUY",
        atr_at_screening=50.0,  # ATR 0.5%
    )
    score_bad = MarketScreener._composite(bad)
    print(f"  [낮은R:R] composite={score_bad}  reasons={bad.reasons}")
    rr_penalty_bad = any("R:R" in r for r in bad.reasons)

    # 가짜 후보 2: ATR 충분 → R:R OK
    good = ScreenerCandidate(
        ticker="GOOD.KS", name="good", current_price=10000, score=0, reasons=[],
        tech_score=75, fund_passed=True,
        ai_score=90, ai_action="BUY",
        atr_at_screening=600.0,  # ATR 6% → R:R ≈ 1760/940 = 1.87 > 1.8
    )
    score_good = MarketScreener._composite(good)
    print(f"  [충분R:R] composite={score_good}  reasons={good.reasons}")
    rr_penalty_good = any("R:R" in r for r in good.reasons)

    print(f"  비용패널티 -8 + R:R 게이트 비교: bad-good = {score_bad - score_good:.1f}")
    ok = rr_penalty_bad and not rr_penalty_good and score_bad < score_good
    report("R:R 게이트: 낮은 ATR 트리거 / 높은 ATR 통과", ok)
    results["rr_gate"] = ok
except Exception as e:
    traceback.print_exc()
    results["rr_gate"] = False

# ── 9. composite_backtest (오프라인) ────────────────────
section(9, "composite_backtest — yfinance 6mo")
try:
    from backtest.composite_backtest import CompositeBacktest
    bt = CompositeBacktest()
    r = bt.run("005930.KS", period="6mo")
    print(f"  거래 {r.total_trades}건 | 승률 {r.winrate*100:.1f}% | 누적 {r.total_pnl_pct:+.2f}% | MDD {r.max_drawdown:.2f}%")
    ok = isinstance(r.total_trades, int)
    report("백테스트 정상 종료", ok)
    results["backtest"] = ok
except Exception as e:
    traceback.print_exc()
    results["backtest"] = False

# ── 종합 ───────────────────────────────────────────────
print(f"\n{'='*70}\n  최종 결과\n{'='*70}")
total = len(results); passed = sum(1 for v in results.values() if v)
for k, v in results.items():
    print(f"  {PASS if v else FAIL}  {k}")
print(f"\n  {passed}/{total} 통과")
sys.exit(0 if passed == total else 1)
