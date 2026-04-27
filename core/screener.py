"""
core/screener.py — 자동 종목 스크리너

국내 코스피/코스닥 전체 종목 중에서
AI 매수 후보를 자동으로 탐색한다.

스크리닝 단계:
  1차 필터: 거래량 급등(2배+), RSI < 40
  2차 필터: 기술지표 복합 조건
  3차 필터: Claude AI 신뢰도 70점+

결과를 텔레그램으로 발송 + DB 저장
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from loguru import logger

from config import DB_PATH, RISK_CONFIG


# ── 스크리닝 후보 ─────────────────────────────

@dataclass
class ScreenerCandidate:
    ticker:        str
    name:          str
    current_price: float       # KRW: 정수, USD: 소수점 유지
    score:         float       # 통합 점수 (composite, 0~100)
    reasons:       list[str]   # 통과 조건 목록
    rsi:           float = 0.0
    vol_ratio:     float = 0.0
    macd_cross:    bool  = False
    bb_position:   str   = "middle"
    # 추가 지표 (퀀트 표준)
    mfi:                float = 0.0
    dist_from_52w_high: float = 0.0
    dist_from_52w_low:  float = 0.0
    value_traded:       float = 0.0
    obv_trend:          float = 0.0
    # 3중 검증 점수 분해
    tech_score:    float = 0.0      # 기술 지표 점수 (raw)
    fund_passed:   bool  = False    # 펀더멘탈 게이트 통과 여부
    fund_summary:  str   = ""       # "ROE 12% · PER 8.3 · 부채 21%" 식
    ai_score:      float = 0.0      # AI 신뢰도 (0~100)
    ai_action:     str   = ""       # "BUY" / "SELL" / "HOLD"
    ai_reason:     str   = ""
    news_score:    float = 0.0
    news_judgment: str   = ""
    expected_score: float = 0.0
    quality_score:  float = 0.0
    # 30년차 메타 분석
    setup_type:    str   = ""       # "Uptrend Pullback" | "Squeeze Breakout" | etc.
    confluence:    int   = 0        # 0~5 그룹 동시 합의도
    regime:        str   = ""       # "BULL" | "BEAR" | "RANGE" | "HIGH_VOL"
    atr_at_screening:  float = 0.0  # 진입 시점 ATR — R:R 게이트 계산용
    sector:        str   = ""       # 섹터 (상관관계 차단용)
    screened_at:        str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ScreenerResult:
    run_date:       str
    total_scanned:  int
    candidates:     list[ScreenerCandidate]
    elapsed_sec:    float

    @property
    def top_candidates(self) -> list[ScreenerCandidate]:
        return sorted(self.candidates, key=lambda x: -x.score)[:10]


# ── 스크리너 ──────────────────────────────────

class MarketScreener:
    """
    시장 전체 종목을 대상으로 매수 후보를 자동 탐색한다.
    키움 API 없이도 Watch List 기반으로 동작한다.
    """

    # 코스피 200 주요 종목 (확장 감시 목록)
    KOSPI200_SAMPLE = [
        "005930","000660","035420","051910","006400",
        "005380","000270","068270","207940","035720",
        "096770","012330","011200","028260","003550",
        "066570","105560","055550","086790","009150",
        "010950","003490","034730","018260","032830",
        "011070","017670","015760","009830","010130",
    ]

    KOSDAQ_SAMPLE = [
        "247540","086520","196170","141080","091990",
        "357780","145020","112040","078600","036570",
    ]

    NAME_MAP = {
        "005930":"삼성전자", "000660":"SK하이닉스", "035420":"NAVER",
        "051910":"LG화학",  "006400":"삼성SDI",   "005380":"현대차",
        "000270":"기아",    "068270":"셀트리온",   "207940":"삼성바이오",
        "035720":"카카오",  "096770":"SK이노베이션","012330":"현대모비스",
        "011200":"HMM",    "028260":"삼성물산",    "003550":"LG",
        "066570":"LG전자",  "105560":"KB금융",     "055550":"신한지주",
        "086790":"하나금융","009150":"삼성전기",    "010950":"S-Oil",
        "003490":"대한항공","034730":"SK",         "018260":"삼성에스디에스",
        "032830":"삼성생명","011070":"LG이노텍",   "017670":"SK텔레콤",
        "015760":"한국전력","009830":"한화솔루션",  "010130":"고려아연",
        "247540":"에코프로비엠","086520":"에코프로","196170":"알테오젠",
        "141080":"레고켐바이오","091990":"셀트리온헬스케어","357780":"솔브레인",
        "145020":"휴젤",   "112040":"위메이드",    "078600":"대주전자재료",
        "036570":"엔씨소프트",
    }

    def __init__(self, data_collector=None,
                 fundamental_gate=None, integrated_judge=None,
                 regime_analyzer=None) -> None:
        """
        data_collector: DataCollector 인스턴스
        fundamental_gate: FundamentalGate 인스턴스 (None 이면 펀더멘탈 검증 스킵)
        integrated_judge: IntegratedJudge 인스턴스 (None 이면 AI 검증 스킵)
        regime_analyzer: 단일 인스턴스 (KR/US 자동) 또는 None — 시장별로 자동 생성
        """
        self._dc       = data_collector
        self._fg       = fundamental_gate
        self._aij      = integrated_judge
        # 시장별 국면 분석기 (KR/US 따로)
        self._regimes: dict = {}
        try:
            from core.market_regime import MarketRegimeAnalyzer
            self._regimes["KR"] = MarketRegimeAnalyzer("KR")
            self._regimes["US"] = MarketRegimeAnalyzer("US")
        except Exception:
            pass
        # 하위 호환: 명시 인자
        if regime_analyzer is not None:
            self._regimes["KR"] = regime_analyzer
        self._init_db()

    def _regime_for(self, ticker: str):
        """티커 suffix로 시장 결정 후 regime 반환."""
        if not self._regimes:
            return None
        market = "KR" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "US"
        ra = self._regimes.get(market)
        return ra.get() if ra else None

    # ── 스크리닝 실행 ─────────────────────────

    def run(
        self,
        universe:     list[str] = None,
        min_score:    float = 40.0,        # 1차(기술) 컷 임계값
        max_results:  int   = 100,
        use_mock:     bool  = False,
        ai_top_n:     int   = 0,           # 기술 점수 상위 N개에 AI 호출 (0=AI 스킵)
        composite_min: float = 0.0,        # 최종 통합 점수 컷 (0=비활성)
    ) -> ScreenerResult:
        """
        2-stage 스크리닝:
          Stage 1: 모든 종목 → 기술 점수 산출 → min_score 통과만 유지
          Stage 2: 통과 종목에 펀더멘탈 게이트 적용
          Stage 3: 펀더멘탈 통과 종목 중 상위 ai_top_n개에 AI 판단 호출
          최종: composite_score 계산해서 정렬
        """
        start = time.time()
        universe = universe or (self.KOSPI200_SAMPLE + self.KOSDAQ_SAMPLE)
        candidates: list[ScreenerCandidate] = []

        # 시장 국면 (KR + US 각각 5분 캐시)
        for mkt in ("KR", "US"):
            ra = self._regimes.get(mkt)
            if ra:
                r = ra.get()
                logger.info("📊 [{}] {} ({:+.2f}%, vol {:.0f}분위)",
                            mkt, r.state, r.kospi_pct, r.vol_pctile)

        logger.info("스크리너 시작: {}개 종목 (AI top-{}, composite≥{})",
                    len(universe), ai_top_n, composite_min)

        # ── Stage 1: 기술 점수 ─────────────────────
        snap_cache: dict[str, object] = {}
        for ticker in universe:
            try:
                if use_mock or self._dc is None:
                    snap = self._mock_snapshot(ticker)
                else:
                    snap = self._dc.get_snapshot(ticker)
                    time.sleep(0.25)
                snap_cache[ticker] = snap

                cand = self._evaluate(ticker, snap)
                if cand and cand.tech_score >= min_score:
                    candidates.append(cand)
            except Exception as e:
                logger.debug("스크리닝 [{}]: {}", ticker, e)

        # ── Stage 2 & 3: 펀더멘탈 + AI ──────────
        if (self._fg or self._aij) and candidates:
            candidates.sort(key=lambda x: -x.tech_score)
            # 펀더멘탈은 상위 50개까지 검증, AI 는 펀더 통과 + 기술 ≥70 인 것만
            top_for_validation = candidates[:max(ai_top_n * 3, 50)]
            ai_calls_left = ai_top_n
            ai_tech_threshold = 70.0   # AI 호출 사전 필터: 기술 점수 70+ 인 것만

            for c in top_for_validation:
                snap = snap_cache.get(c.ticker)
                # 1) 펀더멘탈
                if self._fg:
                    fc = self._fg.check(c.ticker)
                    c.fund_passed = fc.passed
                    raw = fc.raw or {}
                    if raw:
                        c.fund_summary = (
                            f"ROE {raw.get('roe', 0)*100:.1f}% · "
                            f"영업익 {raw.get('op_margin', 0)*100:.1f}% · "
                            f"PER {raw.get('per', 0):.1f} · "
                            f"부채 {raw.get('debt_eq', 0):.0f}%"
                        )
                # 2) AI (펀더 통과 + 기술 70+ + 베어리시 다이버전스 X + 한도 안)
                meta_obj = getattr(snap, "_meta_cache", None)
                bearish = bool(meta_obj and meta_obj.get("bearish_div"))
                if (self._aij and snap is not None and c.fund_passed
                        and c.tech_score >= ai_tech_threshold
                        and not bearish
                        and ai_calls_left > 0):
                    try:
                        v = self._aij.judge(snap, fetch_news=True)
                        c.ai_score  = float(v.confidence or 0)
                        c.ai_action = v.action
                        c.ai_reason = (v.reason or "")[:100]
                        c.news_score = float(getattr(v, "news_score", 0) or 0)
                        c.news_judgment = str(getattr(v, "news_judgment", "") or "")
                        if getattr(v, "news_blocked", False):
                            c.ai_action = "SELL"
                            c.reasons.append("news blocked")
                        ai_calls_left -= 1
                    except Exception as e:
                        logger.debug("AI judge [{}]: {}", c.ticker, e)

                # 3) Composite Score
                c.score = self._composite(c)

                # 4) 시장 국면 가중치 (티커 시장별)
                regime = self._regime_for(c.ticker)
                if regime:
                    c.regime = regime.state
                    setup_to_type = {
                        "🚀 Uptrend Pullback":     "trend",
                        "⚡ Squeeze Breakout":     "breakout",
                        "🔥 New High Breakout":   "breakout",
                        "🔼 Bullish Divergence":   "momentum",
                        "🎯 Oversold Bounce":      "mean_rev",
                        "🔄 Mean Reversion":       "mean_rev",
                        "⚓ Support Bounce":        "trend",
                        "🌊 Wave 3 Continuation":  "trend",
                    }
                    sig_type = setup_to_type.get(c.setup_type, "momentum")
                    from core.market_regime import MarketRegimeAnalyzer as _MRA
                    mult = _MRA.weight_multiplier(regime, sig_type)
                    if mult != 1.0:
                        c.score = round(c.score * mult, 1)
                        c.reasons.append(f"📊 {regime.state}장 ×{mult:.2f}")

            # composite_min 컷 + 베어리시 다이버전스 hard cut
            if composite_min > 0:
                candidates = [c for c in candidates if c.score >= composite_min]
            ai_filled = sum(1 for c in candidates if c.ai_action)
            logger.info("스크리너 검증: 후보 {}개 / AI 호출 {}회",
                        len(candidates), ai_filled)

        # 통합 점수 내림차순
        candidates.sort(key=lambda x: -x.score)
        elapsed = time.time() - start

        result = ScreenerResult(
            run_date      = date.today().isoformat(),
            total_scanned = len(universe),
            candidates    = candidates[:max_results],
            elapsed_sec   = elapsed,
        )

        self._save_result(result)
        self._log_result(result)
        return result

    @staticmethod
    def _composite(c: ScreenerCandidate) -> float:
        """Composite 0~100 score using technicals, fundamentals, AI, news, cost and R:R."""
        from config import RISK_CONFIG

        def clamp(value: float, low: float, high: float) -> float:
            return max(low, min(float(value or 0.0), high))

        tech = clamp(c.tech_score, 0.0, 100.0) * 0.35
        fund = 15.0 if c.fund_passed else -20.0

        ai = 0.0
        ai_score = clamp(c.ai_score, 0.0, 100.0)
        if c.ai_action == "BUY":
            ai = ai_score * 0.30
        elif c.ai_action == "HOLD":
            ai = max(0.0, ai_score - 55.0) * 0.10
        elif c.ai_action == "SELL":
            ai = -30.0

        news_raw = clamp(getattr(c, "news_score", 0.0), -100.0, 100.0)
        news = news_raw * 0.12
        if news_raw >= 40:
            c.reasons.append(f"news +{news_raw:.0f}")
        elif news_raw <= -30:
            news -= 8.0
            c.reasons.append(f"news {news_raw:.0f}")

        setup_bonus = {5: 10.0, 4: 7.0, 3: 4.0}.get(int(getattr(c, "confluence", 0) or 0), 0.0)
        setup_text = str(getattr(c, "setup_type", "") or "").lower()
        if any(k in setup_text for k in ("breakout", "pullback", "divergence", "continuation")):
            setup_bonus += 3.0
        if "bearish" in setup_text or "downtrend" in setup_text:
            setup_bonus -= 18.0

        atr_pct = float(getattr(c, "atr_pct", 0.0)) if hasattr(c, "atr_pct") else 0.0
        if atr_pct == 0.0 and c.atr_at_screening and c.current_price:
            atr_pct = c.atr_at_screening / c.current_price * 100
        if atr_pct >= 3.0:
            cost_penalty = -6.0
        elif atr_pct >= 2.0:
            cost_penalty = -10.0
        elif atr_pct >= 1.0:
            cost_penalty = -16.0
        else:
            cost_penalty = -22.0

        rr_penalty = 0.0
        if c.atr_at_screening and c.current_price:
            sl_mult = RISK_CONFIG.get("stop_loss_atr_mult", 1.5)
            tp_mult = RISK_CONFIG.get("take_profit_atr_mult", 3.0)
            cost = RISK_CONFIG.get("cost_roundtrip_pct", 0.004) * c.current_price
            sl_dist = c.atr_at_screening * sl_mult + cost
            tp_dist = c.atr_at_screening * tp_mult - cost
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                min_rr = RISK_CONFIG.get("min_effective_rr", 1.8)
                if rr < min_rr:
                    rr_penalty -= 15.0
                    c.reasons.append(f"effective R:R {rr:.2f} < {min_rr}")
            expected_profit_pct = round(tp_dist / c.current_price * 100, 2) if c.current_price else 0.0
            if 0 < expected_profit_pct < 1.0:
                rr_penalty -= 8.0
                c.reasons.append(f"expected profit {expected_profit_pct}% < 1%")

        raw = tech + fund + ai + news + setup_bonus + cost_penalty + rr_penalty
        c.expected_score = round(max(0.0, min(raw, 100.0)), 1)
        c.quality_score = round(max(0.0, min(tech + fund + setup_bonus, 100.0)), 1)
        return c.expected_score

    # ── 1차 스코어 평가 ───────────────────────

    @staticmethod
    def _meta_analyze(snap) -> dict:
        """
        5그룹 합의 (Trend/Momentum/Volume/Position/Volatility)
        + 다이버전스 + 스퀴즈 + 강화된 셋업 분류 (10종).
        """
        # 기본 지표
        rsi      = getattr(snap, "rsi", 50.0)
        mfi      = getattr(snap, "mfi", 0.0)
        vol      = getattr(snap, "volume_ratio", 1.0)
        macd     = getattr(snap, "macd", 0.0)
        bb_pos   = getattr(snap, "bollinger_position", "middle")
        ma5      = getattr(snap, "ma5", 0.0)
        ma20     = getattr(snap, "ma20", 0.0)
        ma120    = getattr(snap, "ma120", 0.0)
        stoch    = getattr(snap, "stochastic_k", 50.0)
        obv      = getattr(snap, "obv_trend", 0.0)
        d_high   = getattr(snap, "dist_from_52w_high", 0.0)
        cur_px   = getattr(snap, "current_price", 0)
        atr_pct  = getattr(snap, "atr_pct", 0.0)
        squeeze  = getattr(snap, "bb_squeeze", False)
        bb_w     = getattr(snap, "bb_width_pct", 0.0)
        # 다이버전스
        bull_div_rsi = getattr(snap, "bull_div_rsi", False)
        bear_div_rsi = getattr(snap, "bear_div_rsi", False)
        bull_div_obv = getattr(snap, "bull_div_obv", False)
        bear_div_obv = getattr(snap, "bear_div_obv", False)
        # 신규 지표
        adx       = getattr(snap, "adx", 0.0)
        plus_di   = getattr(snap, "plus_di", 0.0)
        minus_di  = getattr(snap, "minus_di", 0.0)
        will_r    = getattr(snap, "williams_r", 0.0)
        force_idx = getattr(snap, "force_index", 0.0)
        cmf       = getattr(snap, "cmf", 0.0)
        above_cl  = getattr(snap, "above_cloud", False)
        below_cl  = getattr(snap, "below_cloud", False)

        # 신규 지표 (MA20 기울기 + 추세 일관성 + 매집 비율)
        ma20_slope = getattr(snap, "ma20_slope_pct", 0.0)
        uptrend_pct = getattr(snap, "uptrend_consistency", 0.0)
        accum_ratio = getattr(snap, "accumulation_ratio", 1.0)

        # ── 그룹 1: Trend (7점, MA20 slope 추가) ──
        trend = 0
        # ma5>ma20 + ma20 우상향 → 진짜 추세 (평평한 ma20 위 cross 는 가짜)
        if ma5 > 0 and ma20 > 0 and ma5 > ma20 and ma20_slope > 0: trend += 1
        if ma120 > 0 and cur_px > ma120: trend += 1
        if macd > 0: trend += 1
        if adx > 25 and plus_di > minus_di: trend += 1   # 강한 추세
        if above_cl: trend += 1                           # Ichimoku 구름 위
        if uptrend_pct >= 70: trend += 1                  # 20일 중 70% 이상 MA20 위 = 일관 추세
        # MTF 정렬 (1d + 5m + 15m 같은 방향) — 단타에 결정적
        if getattr(snap, "mtf_aligned", False): trend += 1

        # ── 그룹 2: Momentum (6점, 5분봉 RSI 추가) ──
        momo = 0
        if 30 < rsi < 65: momo += 1
        if mfi and 30 < mfi < 70: momo += 1
        if 20 < stoch < 80: momo += 1
        if -80 < will_r < -20: momo += 1                  # Williams %R 정상권
        if force_idx > 0: momo += 1                       # Force Index 양수
        # 5분봉 RSI (인트라데이) — 단타 핵심 시그널
        rsi_5m = getattr(snap, "rsi_5m", 0.0)
        if 30 < rsi_5m < 70: momo += 1

        # ── 그룹 3: Volume + 마이크로구조 (6점, 매집비율 추가) ───
        volg = 0
        if vol >= 1.5: volg += 1
        if obv > 5: volg += 1
        if cmf > 0.05: volg += 1                          # 자금 유입
        if mfi > 50: volg += 1
        # 한국 시장 마이크로구조 (외국인/호가 잔량)
        bid_ask = getattr(snap, "bid_ask_ratio", 0.0)
        foreign = getattr(snap, "foreign_net", 0)
        if bid_ask > 1.5 or foreign > 0:                  # 매수세 또는 외국인 순매수
            volg += 1
        # 매집 비율: up-day vol / down-day vol > 1.3 = 매집 우위
        if accum_ratio > 1.3: volg += 1

        # ── 그룹 4: Position (4점) ────────────────
        posg = 0
        if bb_pos in ("lower", "middle"): posg += 1
        if d_high > -10: posg += 1
        if ma20 > 0 and cur_px > ma20: posg += 1
        if not below_cl: posg += 1

        # ── 그룹 5: Volatility (3점) ──────────────
        volag = 0
        if 1.0 < atr_pct < 4.0: volag += 1                # 단타 적정 변동성
        if squeeze: volag += 1                            # 폭발 임박
        if 2 < bb_w < 8: volag += 1                       # 너무 좁지도 넓지도 않음

        # 5그룹 합의 (각 그룹 절반 이상 통과 = 강세)
        # Trend 7/Momentum 6/Volume 6/Position 4/Volatility 3 만점
        thresholds = {"trend":5, "momo":4, "volg":4, "posg":2, "volag":2}
        groups_bullish = sum([
            trend >= thresholds["trend"],
            momo  >= thresholds["momo"],
            volg  >= thresholds["volg"],
            posg  >= thresholds["posg"],
            volag >= thresholds["volag"],
        ])

        bullish_div = bull_div_rsi or bull_div_obv
        bearish_div = bear_div_rsi or bear_div_obv
        squeeze_breakout = bool(squeeze and vol >= 2.0 and ma5 > ma20)

        # ── 강화 셋업 분류 (10종) ─────────────────
        setup = "—"
        # ⚠️ 위험 패턴 먼저
        if bearish_div or (above_cl is False and adx > 25 and minus_di > plus_di):
            setup = "⚠️ Bearish Divergence" if bearish_div else "⚠️ Strong Downtrend"
        # ⚡ 강한 신호 패턴
        elif squeeze_breakout:
            setup = "⚡ Squeeze Breakout"
        # 신고가 돌파: 매집비율 1.3+ 와 추세 일관성 60%+ 추가 요구 (가짜 돌파 차단)
        elif (d_high > -2 and vol >= 2.5 and macd > 0 and adx > 25
              and accum_ratio > 1.3 and uptrend_pct >= 60):
            setup = "🔥 New High Breakout"
        elif bullish_div and rsi < 40:
            setup = "🔼 Bullish Divergence"
        # 🌊 추세 지속: ma20 우상향 추가
        elif (above_cl and adx > 30 and plus_di > minus_di + 10
              and 40 < rsi < 70 and force_idx > 0 and ma20_slope > 0):
            setup = "🌊 Wave 3 Continuation"
        # 🚀 Uptrend Pullback: ma20 우상향 (평평하면 가짜 풀백)
        elif (ma120 > 0 and cur_px > ma120 and ma5 > ma20 and ma20_slope > 0 and
              ma20 > 0 and abs(cur_px - ma20) / ma20 < 0.03 and 38 < rsi < 60):
            setup = "🚀 Uptrend Pullback"
        # ⚓ 지지 반등
        elif (ma120 > 0 and abs(cur_px - ma120) / ma120 < 0.02 and
              vol >= 1.5 and rsi < 45):
            setup = "⚓ Support Bounce"
        # 🎯 과매도 / 평균회귀
        elif rsi < 25 and bb_pos == "lower" and vol >= 1.5 and will_r < -85:
            setup = "🎯 Oversold Bounce"
        elif bb_pos == "lower" and rsi < 35 and vol >= 1.0:
            setup = "🔄 Mean Reversion"

        return {
            "confluence":        groups_bullish,
            "bullish_div":       bullish_div,
            "bearish_div":       bearish_div,
            "squeeze_breakout":  squeeze_breakout,
            "setup":             setup,
            "groups": {"trend": trend, "momentum": momo, "volume": volg,
                       "position": posg, "volatility": volag},
        }

    def _evaluate(self, ticker: str, snap) -> Optional[ScreenerCandidate]:
        """
        종목에 점수를 매기고 ScreenerCandidate를 반환한다.
        점수 기준은 AI 판단 가중치 테이블과 동일.
        """
        score   = 0.0
        reasons = []

        rsi       = getattr(snap, "rsi",           50.0)
        vol_ratio = getattr(snap, "volume_ratio",   1.0)
        macd_cross= getattr(snap, "macd_cross",   False)
        bb_pos    = getattr(snap, "bollinger_position", "middle")
        ma5       = getattr(snap, "ma5",             0.0)
        ma20      = getattr(snap, "ma20",            0.0)
        stoch_k   = getattr(snap, "stochastic_k",  50.0)
        price     = getattr(snap, "current_price",   0)
        # 새 지표 4개
        mfi          = getattr(snap, "mfi",                0.0)
        dist_high    = getattr(snap, "dist_from_52w_high", 0.0)
        dist_low     = getattr(snap, "dist_from_52w_low",  0.0)
        value_traded = getattr(snap, "value_traded",       0.0)
        obv_trend    = getattr(snap, "obv_trend",          0.0)

        # 가격 필터: KRW 종목은 100원 미만, USD 종목은 $1 미만 제외
        snap_ticker = getattr(snap, "ticker", ticker)
        is_kr = snap_ticker.endswith(".KS") or snap_ticker.endswith(".KQ")
        min_price = 100 if is_kr else 1
        if price < min_price:
            return None

        # ── 과매도 클러스터 (RSI/MFI/Stoch/Williams) — 합산 상한 +25 ───
        # 같은 신호를 4개 지표가 동시에 외칠 때 점수 폭주 방지.
        oversold_pts = 0.0
        if rsi < 25:
            oversold_pts += 12; reasons.append(f"RSI 강한 과매도({rsi:.1f})")
        elif rsi < 35:
            oversold_pts += 8;  reasons.append(f"RSI 과매도({rsi:.1f})")
        elif rsi < 45:
            oversold_pts += 5;  reasons.append(f"RSI 낮음({rsi:.1f})")
        elif rsi < 52:
            oversold_pts += 2;  reasons.append(f"RSI neutral-low({rsi:.0f})")

        # 거래량 급등
        if vol_ratio >= 4.0:
            score += 25; reasons.append(f"거래량 폭등({vol_ratio:.1f}배)")
        elif vol_ratio >= 2.5:
            score += 18; reasons.append(f"거래량 급등({vol_ratio:.1f}배)")
        elif vol_ratio >= 1.5:
            score += 12; reasons.append(f"거래량 증가({vol_ratio:.1f}배)")
        elif vol_ratio >= 1.2:
            score += 5;  reasons.append(f"거래량 소폭증가({vol_ratio:.1f}배)")

        # MACD 골든크로스
        if macd_cross:
            score += 20; reasons.append("MACD 골든크로스")

        # 볼린저밴드
        if bb_pos == "lower":
            score += 15; reasons.append("볼린저밴드 하단")
        elif bb_pos == "middle":
            score += 5;  reasons.append("볼린저밴드 중단")
        elif bb_pos == "upper":
            score -= 10  # 과매수 구간 패널티

        # MA 배열 — 기울기 동반 시 가중치 ↑ (평평한 ma20 위 cross 는 가짜)
        ma20_slope_e = getattr(snap, "ma20_slope_pct", 0.0)
        if ma5 > 0 and ma20 > 0:
            if ma5 > ma20 and ma20_slope_e > 0:
                score += 15; reasons.append(f"MA5>MA20 + 우상향({ma20_slope_e:+.1f}%)")
            elif ma5 > ma20:
                score += 5;  reasons.append("MA5>MA20 (slope 0)")
            else:
                score -= 5

        # 스토캐스틱 (oversold 클러스터)
        if stoch_k < 20:
            oversold_pts += 4; reasons.append(f"스토캐스틱 과매도({stoch_k:.1f})")
        elif stoch_k < 40:
            oversold_pts += 2; reasons.append(f"스토캐스틱 낮음({stoch_k:.1f})")

        # MFI (oversold 클러스터)
        if 0 < mfi < 20:
            oversold_pts += 7; reasons.append(f"MFI 강한 과매도({mfi:.0f})")
        elif 0 < mfi < 30:
            oversold_pts += 5; reasons.append(f"MFI 과매도({mfi:.0f})")
        elif mfi > 80:
            score -= 10  # 과매수 패널티는 별도

        # Williams %R (oversold 클러스터)
        will_r_e = getattr(snap, "williams_r", 0.0)
        if will_r_e < -85:
            oversold_pts += 4; reasons.append(f"Williams 강한 과매도({will_r_e:.0f})")
        elif will_r_e < -70:
            oversold_pts += 2

        # 클러스터 상한 적용 — 과매도만으로 점수 폭주 방지
        score += min(oversold_pts, 25.0)

        # ── 52주 신고가 임박 (강한 상승추세) ──────────────────
        if -3 < dist_high <= 0:
            score += 15; reasons.append(f"52주 신고가 임박({dist_high:+.1f}%)")
        elif -10 < dist_high <= -3:
            score += 8;  reasons.append(f"52주 고점 근처({dist_high:+.1f}%)")

        # ── 52주 신저가 반등 시그널 (oversold bounce) ─────────
        if 0 < dist_low < 5:
            score += 10; reasons.append(f"52주 신저가 반등({dist_low:+.1f}%)")

        # ── OBV 추세 (자금 흐름 모멘텀) ───────────────────────
        if obv_trend > 30:
            score += 12; reasons.append(f"OBV 강한 매집(+{obv_trend:.0f}%)")
        elif obv_trend > 10:
            score += 6;  reasons.append(f"OBV 매집(+{obv_trend:.0f}%)")
        elif obv_trend < -30:
            score -= 10  # 가격 vs OBV 다이버전스 = 분산

        # ── 유동성 필터 (단타 적합성) ─────────────────────────
        # 국내: 거래대금 50억 미만 = 단타 부적합 (slippage 심함)
        # 해외: 거래대금 $100M 미만 = 같은 이유
        snap_t = getattr(snap, "ticker", ticker)
        is_kr_t = snap_t.endswith(".KS") or snap_t.endswith(".KQ")
        liq_threshold = 5_000_000_000 if is_kr_t else 100_000_000
        if value_traded > 0 and value_traded < liq_threshold:
            score -= 15
            reasons.append(f"⚠️ 거래대금 부족")

        # ── 30년차 메타 분석: 5그룹 합의 + 다이버전스 + 스퀴즈 ─
        meta = self._meta_analyze(snap)
        # 캐시 (run() 단계에서 bearish_div 참조용)
        try: setattr(snap, "_meta_cache", meta)
        except Exception: pass
        # 5그룹 합의 보너스 (만점 5)
        if meta["confluence"] == 5:
            score += 30; reasons.append(f"💎💎 5그룹 만장일치(+30)")
        elif meta["confluence"] == 4:
            score += 20; reasons.append(f"💎 4그룹 합의(+20)")
        elif meta["confluence"] == 3:
            score += 10; reasons.append(f"3그룹 합의(+10)")
        elif meta["confluence"] == 2:
            score += 3
        # 다이버전스
        if meta["bullish_div"]:
            score += 18; reasons.append("🔼 다이버전스(불리시)")
        if meta["bearish_div"]:
            score -= 20; reasons.append("🔽 다이버전스(베어리시)")
        # 스퀴즈 + 거래량 = 돌파 임박
        if meta["squeeze_breakout"]:
            score += 20; reasons.append("⚡ Squeeze 돌파")
        elif getattr(snap, "bb_squeeze", False):
            reasons.append("🤐 BB Squeeze (대기)")

        # 최소 1개 이상 조건 충족 요구
        if len(reasons) < 1:
            return None

        # snap.ticker는 resolve() 후 실제 코드(예: "005930.KS", "NKE")
        actual_ticker = getattr(snap, "ticker", ticker)
        name = getattr(snap, "name", None) or self.NAME_MAP.get(actual_ticker, actual_ticker)
        return ScreenerCandidate(
            ticker        = actual_ticker,
            name          = name,
            current_price = round(float(price), 2),
            score         = round(score, 1),       # 초기엔 tech score 와 같음 (composite 가 오버라이드)
            tech_score    = round(score, 1),
            reasons       = reasons,
            rsi           = rsi,
            vol_ratio     = vol_ratio,
            macd_cross    = macd_cross,
            bb_position   = bb_pos,
            mfi                = mfi,
            dist_from_52w_high = dist_high,
            dist_from_52w_low  = dist_low,
            value_traded       = value_traded,
            obv_trend          = obv_trend,
            setup_type         = meta["setup"],
            confluence         = meta["confluence"],
            atr_at_screening   = getattr(snap, "atr", 0.0),
        )

    # ── Mock 스냅샷 ───────────────────────────

    @staticmethod
    def _mock_snapshot(ticker: str):
        """DC 없는 환경에서 사용하는 Mock 스냅샷"""
        import random
        random.seed(hash(ticker) % 10000)

        class MockSnap:
            pass

        snap = MockSnap()
        snap.ticker        = ticker
        snap.current_price = random.randint(10000, 500000)
        snap.rsi           = random.uniform(18, 75)
        snap.volume_ratio  = random.uniform(0.5, 5.0)
        snap.macd_cross    = random.random() < 0.15
        snap.bollinger_position = random.choice(["lower","middle","middle","upper"])
        snap.ma5           = snap.current_price * random.uniform(0.97, 1.03)
        snap.ma20          = snap.current_price * random.uniform(0.96, 1.04)
        snap.stochastic_k  = random.uniform(10, 90)
        return snap

    # ── DB + 로그 ─────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS screener_results (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date     TEXT,
                    ticker       TEXT,
                    name         TEXT,
                    price        INTEGER,
                    score        REAL,
                    reasons      TEXT,
                    screened_at  TEXT
                )
            """)

    def _save_result(self, result: ScreenerResult) -> None:
        import json
        with sqlite3.connect(DB_PATH) as con:
            for c in result.candidates:
                # 통합 점수 breakdown 을 reasons 에 prepend 해서 대시보드에 표시되게
                breakdown_lines = []
                # 셋업 타입 (가장 위, 한눈에)
                if c.setup_type and c.setup_type != "—":
                    breakdown_lines.append(c.setup_type)
                # Confluence + Regime 메타
                meta_bits = []
                if c.confluence:
                    meta_bits.append(f"합의 {c.confluence}/4그룹")
                if c.regime:
                    meta_bits.append(f"{c.regime}장")
                if meta_bits:
                    breakdown_lines.append(" · ".join(meta_bits))
                # Tech / Fund / AI breakdown
                breakdown_lines.append(f"📊 Tech {c.tech_score:.0f}")
                if c.fund_summary:
                    fund_icon = "✅" if c.fund_passed else "❌"
                    breakdown_lines.append(f"{fund_icon} Fund: {c.fund_summary}")
                if c.ai_action:
                    ai_icon = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪"}.get(c.ai_action, "")
                    breakdown_lines.append(f"{ai_icon} AI {c.ai_action} {c.ai_score:.0f}점")
                full_reasons = breakdown_lines + c.reasons
                con.execute(
                    "INSERT INTO screener_results "
                    "(run_date,ticker,name,price,score,reasons,screened_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (result.run_date, c.ticker, c.name, c.current_price,
                     c.score, json.dumps(full_reasons, ensure_ascii=False), c.screened_at),
                )

    @staticmethod
    def _log_result(result: ScreenerResult) -> None:
        logger.info("=" * 55)
        logger.info("  📡 스크리닝 완료: {}개 후보 / {}개 스캔 ({:.1f}초)",
                    len(result.candidates), result.total_scanned, result.elapsed_sec)
        logger.info("=" * 55)
        for c in result.top_candidates:
            logger.info(
                "  [{:>5.1f}점] {:8} {:14} | RSI:{:5.1f} | 거래량:{:4.1f}배 | {}",
                c.score, c.ticker, c.name,
                c.rsi, c.vol_ratio, " / ".join(c.reasons[:2]),
            )

    # ── 텔레그램 알림 형식 ────────────────────

    def to_telegram(self, result: ScreenerResult) -> str:
        tops = result.top_candidates
        if not tops:
            return f"📡 스크리닝 완료 ({result.total_scanned}종목)\n후보 없음"

        lines = [
            f"📡 종목 스크리닝 결과",
            f"━" * 26,
            f"스캔: {result.total_scanned}종목 | 후보: {len(tops)}개",
            f"기준일: {result.run_date}",
            "",
        ]
        for i, c in enumerate(tops[:8], 1):
            icon = "🔥" if c.score >= 60 else "⭐" if c.score >= 40 else "🔹"
            is_kr = c.ticker.endswith(".KS") or c.ticker.endswith(".KQ")
            if is_kr:
                price_str = f"{int(c.current_price):,}원"
            elif c.ticker.endswith(".T"):
                price_str = f"¥{int(c.current_price):,}"
            else:
                price_str = f"${c.current_price:.2f}"
            lines.append(
                f"{icon} {i}. {c.name}({c.ticker})\n"
                f"   점수:{c.score:.0f} | {price_str}\n"
                f"   {' | '.join(c.reasons[:2])}"
            )
        lines.append(f"\n⏰ {datetime.now().strftime('%H:%M:%S')}")
        return "\n".join(lines)

    def hot_alerts(self, result: ScreenerResult, threshold: float = 70.0) -> list[str]:
        """threshold 이상 종목을 개별 알림 메시지 리스트로 반환"""
        msgs = []
        for c in result.candidates:
            if c.score < threshold:
                continue
            is_kr = c.ticker.endswith(".KS") or c.ticker.endswith(".KQ")
            if is_kr:
                price_str = f"{int(c.current_price):,}원"
            elif c.ticker.endswith(".T"):
                price_str = f"¥{int(c.current_price):,}"
            else:
                price_str = f"${c.current_price:.2f}"
            reasons_str = " · ".join(c.reasons[:3])
            msgs.append(
                f"🚨 매수 신호 강도 높음\n"
                f"━" * 22 + "\n"
                f"종목: {c.name} ({c.ticker})\n"
                f"점수: {c.score:.0f}점 🔥\n"
                f"가격: {price_str}\n"
                f"근거: {reasons_str}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
        return msgs
