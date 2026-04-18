"""
core/ai_judge_advanced.py — AI 판단 고도화 엔진

개선 사항:
  1. 멀티 타임프레임 분석 (일봉 + 주봉 + 월봉)
  2. 뉴스 감성 스코어 통합
  3. 시장 국면 분류 (상승/하락/횡보)
  4. AI 신뢰도 캘리브레이션 (과거 적중률 기반 보정)
  5. 판단 체인 (Chain-of-Thought 프롬프트)
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import AI_CONFIG, GEMINI_API_KEY, DB_PATH, RISK_CONFIG


# ── 멀티 타임프레임 스냅샷 ───────────────────

@dataclass
class MultiTimeframeSnapshot:
    """일봉 + 주봉 + 월봉 지표를 통합한 스냅샷"""
    ticker:        str
    name:          str
    current_price: float

    # ── 일봉 (단기) ──────────────────────
    d_rsi:         float = 0.0
    d_macd:        float = 0.0
    d_macd_signal: float = 0.0
    d_macd_cross:  bool  = False
    d_bb_position: str   = "middle"
    d_ma5:         float = 0.0
    d_ma20:        float = 0.0
    d_vol_ratio:   float = 1.0
    d_stoch_k:     float = 50.0

    # ── 주봉 (중기) ──────────────────────
    w_rsi:         float = 0.0
    w_macd_cross:  bool  = False
    w_ma5:         float = 0.0
    w_ma20:        float = 0.0
    w_trend:       str   = "neutral"   # "up" | "down" | "neutral"

    # ── 월봉 (장기) ──────────────────────
    m_rsi:         float = 0.0
    m_ma3:         float = 0.0
    m_ma12:        float = 0.0
    m_trend:       str   = "neutral"

    # ── 시장 국면 ─────────────────────────
    market_phase:  str   = "unknown"  # "bull" | "bear" | "sideways"
    phase_confidence: float = 0.0

    # ── 뉴스 감성 ─────────────────────────
    news_sentiment: float = 0.0
    news_count:     int   = 0
    news_headlines: list  = field(default_factory=list)

    # ── 펀더멘털 ──────────────────────────
    per:           float = 0.0
    foreigner_pct: float = 0.0


@dataclass
class AdvancedVerdict:
    ticker:        str
    action:        Literal["BUY", "SELL", "HOLD"]
    confidence:    int
    raw_confidence:int           # 보정 전 AI 원본 신뢰도
    calibrated:    bool          # 캘리브레이션 적용 여부
    reason:        str
    chain_of_thought: str        # AI 중간 추론 과정
    target_price:  float
    stop_loss:     float
    position_size: Literal["SMALL", "MEDIUM", "LARGE"]
    timeframe_alignment: str     # "STRONG" | "MIXED" | "WEAK"
    generated_at:  str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_executable(self) -> bool:
        return self.confidence >= RISK_CONFIG["min_confidence"]

    @property
    def is_strong_signal(self) -> bool:
        """강한 신호: 신뢰도 85+ + 타임프레임 정렬"""
        return self.confidence >= 85 and self.timeframe_alignment == "STRONG"


# ── 멀티 타임프레임 데이터 빌더 ──────────────

class MultiTimeframeBuilder:
    """
    단일 일봉 DataFrame에서 주봉·월봉을 리샘플링하여
    MultiTimeframeSnapshot을 생성한다.
    """

    def build(
        self,
        daily_df:  pd.DataFrame,
        ticker:    str,
        name:      str = "",
        news_sentiment: float = 0.0,
        news_count: int = 0,
        news_headlines: list = None,
        per: float = 0.0,
        foreigner_pct: float = 0.0,
    ) -> MultiTimeframeSnapshot:

        current_price = float(daily_df["close"].iloc[-1])

        # 일봉 지표
        d_ind = self._calc_indicators(daily_df)

        # 주봉 리샘플링
        weekly_df = self._resample(daily_df, "W")
        w_ind     = self._calc_indicators(weekly_df) if len(weekly_df) >= 26 else {}

        # 월봉 리샘플링
        monthly_df = self._resample(daily_df, "ME")
        m_ind      = self._calc_monthly(monthly_df) if len(monthly_df) >= 12 else {}

        # 시장 국면 분류
        phase, phase_conf = self._classify_market_phase(daily_df)

        # 타임프레임 추세
        w_trend = self._trend(weekly_df)
        m_trend = self._trend(monthly_df)

        return MultiTimeframeSnapshot(
            ticker        = ticker,
            name          = name or ticker,
            current_price = current_price,
            # 일봉
            d_rsi         = d_ind.get("rsi", 50.0),
            d_macd        = d_ind.get("macd", 0.0),
            d_macd_signal = d_ind.get("macd_signal", 0.0),
            d_macd_cross  = d_ind.get("macd_cross", False),
            d_bb_position = d_ind.get("bb_position", "middle"),
            d_ma5         = d_ind.get("ma5", current_price),
            d_ma20        = d_ind.get("ma20", current_price),
            d_vol_ratio   = d_ind.get("vol_ratio", 1.0),
            d_stoch_k     = d_ind.get("stoch_k", 50.0),
            # 주봉
            w_rsi         = w_ind.get("rsi", 50.0),
            w_macd_cross  = w_ind.get("macd_cross", False),
            w_ma5         = w_ind.get("ma5", current_price),
            w_ma20        = w_ind.get("ma20", current_price),
            w_trend       = w_trend,
            # 월봉
            m_rsi         = m_ind.get("rsi", 50.0),
            m_ma3         = m_ind.get("ma3", current_price),
            m_ma12        = m_ind.get("ma12", current_price),
            m_trend       = m_trend,
            # 국면
            market_phase      = phase,
            phase_confidence  = phase_conf,
            # 뉴스
            news_sentiment  = news_sentiment,
            news_count      = news_count,
            news_headlines  = news_headlines or [],
            # 펀더멘털
            per           = per,
            foreigner_pct = foreigner_pct,
        )

    # ── 내부 헬퍼 ─────────────────────────────

    @staticmethod
    def _resample(df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """OHLCV를 주봉/월봉으로 리샘플링"""
        return df.resample(freq).agg({
            "open":   "first", "high": "max",
            "low":    "min",   "close": "last",
            "volume": "sum",
        }).dropna()

    @staticmethod
    def _calc_indicators(df: pd.DataFrame) -> dict:
        if len(df) < 26:
            return {}
        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        volume = df["volume"].astype(float)
        r = {}
        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        loss  = (-delta).clip(lower=0).ewm(com=13, min_periods=14).mean()
        r["rsi"] = round(float(100 - 100/(1 + gain.iloc[-1]/max(loss.iloc[-1],1e-9))), 2)
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        r["macd"]       = round(float(macd.iloc[-1]), 3)
        r["macd_signal"]= round(float(sig.iloc[-1]), 3)
        r["macd_cross"] = bool((macd.iloc[-2] - sig.iloc[-2]) < 0 and (macd.iloc[-1] - sig.iloc[-1]) >= 0)
        # BB
        ma20 = close.rolling(20).mean()
        std  = close.rolling(20).std()
        u, l = ma20 + 2*std, ma20 - 2*std
        p    = float(close.iloc[-1])
        r["bb_position"] = "upper" if p >= float(u.iloc[-1]) else "lower" if p <= float(l.iloc[-1]) else "middle"
        # MA
        r["ma5"]  = round(float(close.rolling(5).mean().iloc[-1]), 2)
        r["ma20"] = round(float(ma20.iloc[-1]), 2)
        # 스토캐스틱
        ll = low.rolling(14).min(); hh = high.rolling(14).max()
        r["stoch_k"] = round(float(100*(float(close.iloc[-1])-float(ll.iloc[-1]))/max(float(hh.iloc[-1])-float(ll.iloc[-1]),1e-9)), 2)
        # 거래량
        vol_ma = volume.rolling(20).mean()
        r["vol_ratio"] = round(float(volume.iloc[-1]/max(float(vol_ma.iloc[-1]),1)), 2)
        return r

    @staticmethod
    def _calc_monthly(df: pd.DataFrame) -> dict:
        if len(df) < 12:
            return {}
        close = df["close"].astype(float)
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        loss  = (-delta).clip(lower=0).ewm(com=13, min_periods=14).mean()
        rsi   = float(100 - 100/(1 + gain.iloc[-1]/max(loss.iloc[-1],1e-9)))
        return {
            "rsi":  round(rsi, 2),
            "ma3":  round(float(close.rolling(3).mean().iloc[-1]), 2),
            "ma12": round(float(close.rolling(12).mean().iloc[-1]), 2),
        }

    @staticmethod
    def _trend(df: pd.DataFrame) -> str:
        if len(df) < 10:
            return "neutral"
        close = df["close"].astype(float)
        ma5   = close.rolling(5).mean()
        ma20  = close.rolling(min(20, len(df))).mean()
        if float(ma5.iloc[-1]) > float(ma20.iloc[-1]) * 1.01:
            return "up"
        elif float(ma5.iloc[-1]) < float(ma20.iloc[-1]) * 0.99:
            return "down"
        return "neutral"

    @staticmethod
    def _classify_market_phase(df: pd.DataFrame) -> tuple[str, float]:
        """최근 60일 가격 움직임으로 시장 국면을 분류한다."""
        if len(df) < 60:
            return "unknown", 0.0
        close    = df["close"].astype(float).iloc[-60:]
        ret_60d  = float((close.iloc[-1] - close.iloc[0]) / close.iloc[0])
        vol_60d  = float(close.pct_change().std() * np.sqrt(252))
        ma20     = float(close.rolling(20).mean().iloc[-1])
        price    = float(close.iloc[-1])

        if ret_60d > 0.08 and price > ma20:
            return "bull", min(0.9, 0.5 + ret_60d)
        elif ret_60d < -0.08 and price < ma20:
            return "bear", min(0.9, 0.5 + abs(ret_60d))
        else:
            return "sideways", 0.6


# ── 캘리브레이터 ─────────────────────────────

class ConfidenceCalibrator:
    """
    과거 AI 판단 기록을 분석하여 신뢰도를 보정한다.
    데이터가 부족하면 보정 없이 그대로 반환한다.
    """

    _MIN_SAMPLES = 10   # 최소 샘플 수

    def calibrate(self, ticker: str, raw_confidence: int, action: str) -> int:
        try:
            stats = self._load_stats(ticker, action)
            if stats["count"] < self._MIN_SAMPLES:
                return raw_confidence
            # 과거 적중률로 선형 보정
            hit_rate = stats["hit_rate"]   # 0~1
            adj = (hit_rate - 0.5) * 20    # ±10점 범위 보정
            calibrated = int(max(0, min(100, raw_confidence + adj)))
            logger.debug(
                "신뢰도 캘리브레이션: {} {} | {}→{} (적중률:{:.0%})",
                ticker, action, raw_confidence, calibrated, hit_rate,
            )
            return calibrated
        except Exception:
            return raw_confidence

    @staticmethod
    def _load_stats(ticker: str, action: str) -> dict:
        """과거 판단 기록에서 적중률을 계산한다."""
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT confidence FROM orders WHERE ticker=? AND order_type=? "
                    "AND status IN ('PAPER_FILLED','FILLED') LIMIT 100",
                    (ticker, action),
                ).fetchall()
            if not rows:
                return {"count": 0, "hit_rate": 0.5}
            confs = [r[0] for r in rows if r[0]]
            return {"count": len(confs), "hit_rate": sum(1 for c in confs if c >= 70) / len(confs)}
        except Exception:
            return {"count": 0, "hit_rate": 0.5}


# ── 고도화 AI 판단 엔진 ──────────────────────

class AdvancedAIJudge:
    """
    멀티 타임프레임 + 뉴스 감성 + 캘리브레이션을 통합한
    고도화 AI 판단 엔진.
    """

    _SYSTEM = """\
당신은 30년 경력의 퀀트 트레이더 AI입니다.
일봉·주봉·월봉 멀티 타임프레임과 뉴스 감성을 종합하여 매매를 판단합니다.

판단 원칙:
1. 세 타임프레임이 같은 방향을 가리킬 때만 강한 신뢰도를 부여한다.
2. 뉴스 감성이 -0.5 이하이면 매수를 금지한다.
3. 시장 국면이 bear이면 매수 신뢰도에 15점을 차감한다.
4. 불확실할 때는 반드시 HOLD를 선택한다.

응답 형식 (JSON만, 다른 텍스트 금지):
{
  "chain_of_thought": "단계별 추론 과정 (3~5줄)",
  "timeframe_alignment": "STRONG|MIXED|WEAK",
  "action": "BUY|SELL|HOLD",
  "confidence": 0~100,
  "reason": "최종 판단 근거 2줄 요약 (한국어)",
  "target_price": 목표가(number),
  "stop_loss": 손절가(number),
  "position_size": "SMALL|MEDIUM|LARGE"
}"""

    def __init__(self) -> None:
        self._calibrator = ConfidenceCalibrator()
        self._builder    = MultiTimeframeBuilder()
        self._mock = not bool(GEMINI_API_KEY)
        if not self._mock:
            from google import genai
            self._client = genai.Client(api_key=GEMINI_API_KEY)

    def judge(
        self,
        mtf_snap: MultiTimeframeSnapshot,
    ) -> AdvancedVerdict:
        if self._mock:
            return self._mock_verdict(mtf_snap)

        prompt = self._build_prompt(mtf_snap)
        try:
            from google.genai import types as gtypes
            resp = self._client.models.generate_content(
                model    = AI_CONFIG["model"],
                contents = self._SYSTEM + "\n\n" + prompt,
                config   = gtypes.GenerateContentConfig(
                    temperature=0, max_output_tokens=AI_CONFIG["max_tokens"]
                ),
            )
            raw   = resp.text
            clean = re.sub(r"```json|```", "", raw).strip()
            m     = re.search(r"\{.*\}", clean, re.DOTALL)
            data  = json.loads(m.group(0) if m else clean)
        except Exception as e:
            logger.error("AI 호출 실패 [{}]: {}", mtf_snap.ticker, e)
            return self._fallback(mtf_snap)

        raw_conf  = int(data.get("confidence", 0))
        action    = data.get("action", "HOLD")
        cal_conf  = self._calibrator.calibrate(mtf_snap.ticker, raw_conf, action)
        calibrated= (cal_conf != raw_conf)

        verdict = AdvancedVerdict(
            ticker               = mtf_snap.ticker,
            action               = action,
            confidence           = cal_conf,
            raw_confidence       = raw_conf,
            calibrated           = calibrated,
            reason               = data.get("reason", ""),
            chain_of_thought     = data.get("chain_of_thought", ""),
            target_price         = float(data.get("target_price", mtf_snap.current_price)),
            stop_loss            = float(data.get("stop_loss", mtf_snap.current_price * 0.97)),
            position_size        = data.get("position_size", "SMALL"),
            timeframe_alignment  = data.get("timeframe_alignment", "MIXED"),
        )

        self._log_verdict(verdict)
        return verdict

    # ── 프롬프트 빌더 ─────────────────────────

    @staticmethod
    def _build_prompt(s: MultiTimeframeSnapshot) -> str:
        phase_kr = {"bull":"상승장","bear":"하락장","sideways":"횡보","unknown":"불명"}
        trend_kr = {"up":"↑ 상승","down":"↓ 하락","neutral":"→ 횡보"}
        sent_label = "🟢 긍정" if s.news_sentiment > 0.3 else "🔴 부정" if s.news_sentiment < -0.3 else "⚪ 중립"

        heads = ""
        if s.news_headlines:
            heads = "\n  - " + "\n  - ".join(s.news_headlines[:3])

        return f"""
종목: {s.ticker} ({s.name})
현재가: {s.current_price:,.0f}원  |  시장국면: {phase_kr.get(s.market_phase,'?')} (신뢰:{s.phase_confidence:.0%})

━━━ 일봉 (단기) ━━━
RSI:       {s.d_rsi:.1f}  {'⚠ 과매도' if s.d_rsi<30 else '⚠ 과매수' if s.d_rsi>70 else ''}
MACD:      {s.d_macd:.2f} / Signal: {s.d_macd_signal:.2f} | 골든크로스: {'✅' if s.d_macd_cross else '❌'}
BB위치:    {s.d_bb_position}
MA5/MA20:  {s.d_ma5:,.0f} / {s.d_ma20:,.0f} ({'MA5>MA20 ↑' if s.d_ma5>s.d_ma20 else 'MA5<MA20 ↓'})
거래량비율:{s.d_vol_ratio:.1f}배 | 스토캐스틱K: {s.d_stoch_k:.1f}

━━━ 주봉 (중기) ━━━
RSI:       {s.w_rsi:.1f}
MACD 크로스:{' ✅' if s.w_macd_cross else ' ❌'}
추세:       {trend_kr.get(s.w_trend,'?')}

━━━ 월봉 (장기) ━━━
RSI:       {s.m_rsi:.1f}
MA3/MA12:  {s.m_ma3:,.0f} / {s.m_ma12:,.0f}
추세:       {trend_kr.get(s.m_trend,'?')}

━━━ 뉴스 감성 ━━━
감성점수: {s.news_sentiment:+.3f} {sent_label} ({s.news_count}건){heads}

━━━ 펀더멘털 ━━━
PER: {s.per:.1f} | 외인보유율: {s.foreigner_pct:.1f}%

위 멀티 타임프레임 데이터를 단계별로 분석하여 JSON으로 응답하세요.
"""

    # ── 로그 ─────────────────────────────────

    @staticmethod
    def _log_verdict(v: AdvancedVerdict) -> None:
        cal_str = f" (보정:{v.raw_confidence}→{v.confidence})" if v.calibrated else ""
        logger.info(
            "고도화 판단 | {} | {} | 신뢰:{}{} | 정렬:{} | {}",
            v.ticker, v.action, v.confidence, cal_str, v.timeframe_alignment, v.reason,
        )

    # ── Mock / Fallback ───────────────────────

    @staticmethod
    def _mock_verdict(s: MultiTimeframeSnapshot) -> AdvancedVerdict:
        import random
        actions = ["BUY","HOLD","HOLD","SELL"]
        action  = random.choice(actions)
        conf    = random.randint(60, 88)
        align   = random.choice(["STRONG","MIXED","WEAK"])
        return AdvancedVerdict(
            ticker="", action=action, confidence=conf, raw_confidence=conf,
            calibrated=False,
            reason=f"[Mock] 일봉 RSI {s.d_rsi:.1f} | 주봉 {s.w_trend} | 감성 {s.news_sentiment:+.2f}",
            chain_of_thought="[Mock] 단계별 추론 생략",
            target_price=s.current_price * 1.06,
            stop_loss=s.current_price * 0.97,
            position_size="MEDIUM",
            timeframe_alignment=align,
        )

    @staticmethod
    def _fallback(s: MultiTimeframeSnapshot) -> AdvancedVerdict:
        return AdvancedVerdict(
            ticker=s.ticker, action="HOLD", confidence=0, raw_confidence=0,
            calibrated=False, reason="API 오류 — 안전 HOLD",
            chain_of_thought="", target_price=s.current_price,
            stop_loss=s.current_price * 0.97, position_size="SMALL",
            timeframe_alignment="WEAK",
        )
