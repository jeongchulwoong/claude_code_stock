"""
core/integrated_judge.py — 뉴스 호재/악재 + 기술지표 통합 판단 엔진

기존 AIJudge (기술지표만) + NewsAnalyzer (뉴스 호재/악재)를
함께 Claude에 제출하여 더 정확한 매매 판단을 내린다.

판단 우선순위:
  1. 뉴스 악재 score ≤ -60  → 매수 신호 강제 차단
  2. 뉴스 호재 score ≥ +60  → 신뢰도 +10점 보너스
  3. 뉴스 악재 score ≤ -30  → 신뢰도 -15점 패널티
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from loguru import logger

from config import AI_CONFIG, GEMINI_API_KEY, RISK_CONFIG
from core.data_collector import StockSnapshot
from core.news_analyzer import NewsVerdict, StockNewsService


# ── 통합 판단 결과 ─────────────────────────────

@dataclass
class IntegratedVerdict:
    ticker:         str
    action:         Literal["BUY", "SELL", "HOLD"]
    confidence:     int
    reason:         str
    target_price:   int
    stop_loss:      int
    position_size:  Literal["SMALL", "MEDIUM", "LARGE"]
    # 뉴스 분석 결과
    news_judgment:  str       # 호재 / 악재 / 중립 / 분석불가
    news_score:     int       # -100 ~ +100
    news_reason:    str
    news_key_points: list[str]
    # 조정 정보
    confidence_adj: int       # 뉴스로 인한 신뢰도 조정값
    news_blocked:   bool      # 뉴스 악재로 매수 차단 여부
    generated_at:   str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_executable(self) -> bool:
        return (
            not self.news_blocked
            and self.action != "HOLD"
            and self.confidence >= RISK_CONFIG["min_confidence"]
        )

    @property
    def summary_line(self) -> str:
        news_icon = {"호재":"🟢","악재":"🔴","중립":"⚪","분석불가":"❓"}.get(self.news_judgment,"")
        act_icon  = {"BUY":"📈","SELL":"📉","HOLD":"⏸"}.get(self.action,"")
        block_str = " [뉴스차단]" if self.news_blocked else ""
        return (
            f"{act_icon} {self.ticker} | {self.action} | 신뢰:{self.confidence}점"
            f" | 뉴스:{news_icon}{self.news_judgment}({self.news_score:+d}){block_str}"
        )


# ── 통합 판단 엔진 ────────────────────────────

class IntegratedJudge:
    """
    기술지표 스냅샷 + 뉴스 호재/악재를 통합하여
    최종 매매 판단을 내리는 엔진.
    """

    _SYSTEM = """\
당신은 30년 경력의 퀀트 트레이더 AI입니다.
기술지표와 최신 뉴스 호재/악재를 종합하여 매수·매도·홀드를 판단합니다.

핵심 원칙:
1. 기술지표가 매수 신호여도 뉴스 악재가 강하면(score ≤ -60) 절대 매수하지 않는다.
2. 뉴스 호재는 확신을 높여주는 보조지표이지, 단독 매수 근거가 되지 않는다.
3. 뉴스와 기술지표가 같은 방향을 가리킬 때만 높은 신뢰도를 부여한다.
4. 불확실하면 HOLD를 선택한다.

응답은 반드시 JSON만:
{
  "action": "BUY"|"SELL"|"HOLD",
  "confidence": 0~100,
  "reason": "판단 근거 2~3줄 (한국어, 뉴스와 지표 통합 근거 포함)",
  "target_price": 목표가(int),
  "stop_loss": 손절가(int),
  "position_size": "SMALL"|"MEDIUM"|"LARGE"
}"""

    def __init__(self) -> None:
        self._news_service = StockNewsService()
        self._mock = not bool(GEMINI_API_KEY)
        if not self._mock:
            from google import genai
            self._client = genai.Client(api_key=GEMINI_API_KEY)

    def judge(
        self,
        snap:      StockSnapshot,
        fetch_news: bool = True,
    ) -> IntegratedVerdict:
        """
        기술지표 스냅샷 + 뉴스를 통합하여 판단한다.
        fetch_news=False면 뉴스 없이 기술지표만 사용.
        """
        # 1. 뉴스 수집 + 호재/악재 판단
        if fetch_news:
            news_v = self._news_service.get_news_verdict(snap.ticker)
        else:
            from core.news_analyzer import NewsVerdict
            news_v = NewsVerdict(
                ticker=snap.ticker, ticker_name=snap.ticker,
                judgment="분석불가", score=0, reason="뉴스 수집 생략",
                key_points=[], news_count=0, news_titles=[],
            )

        # 2. 강한 악재 → 즉시 차단
        if news_v.score <= -60:
            logger.warning(
                "뉴스 악재로 매수 차단: {} | score={} | {}",
                snap.ticker, news_v.score, news_v.reason
            )
            return self._blocked_verdict(snap, news_v)

        # 3. Claude 통합 판단
        if self._mock:
            base = self._mock_judge(snap)
        else:
            base = self._claude_judge(snap, news_v)

        # 4. 신뢰도 조정 (뉴스 영향)
        adj = self._calc_adjustment(news_v)
        final_conf = max(0, min(100, base["confidence"] + adj))

        return IntegratedVerdict(
            ticker          = snap.ticker,
            action          = base["action"],
            confidence      = final_conf,
            reason          = base["reason"],
            target_price    = int(base.get("target_price", snap.current_price)),
            stop_loss       = int(base.get("stop_loss", snap.current_price * 0.97)),
            position_size   = base.get("position_size", "SMALL"),
            news_judgment   = news_v.judgment,
            news_score      = news_v.score,
            news_reason     = news_v.reason,
            news_key_points = news_v.key_points,
            confidence_adj  = adj,
            news_blocked    = False,
        )

    def judge_batch(
        self,
        snaps:      list[StockSnapshot],
        fetch_news: bool = True,
    ) -> list[IntegratedVerdict]:
        return [self.judge(s, fetch_news) for s in snaps]

    # ── 내부 메서드 ───────────────────────────

    def _claude_judge(self, snap: StockSnapshot, news_v: NewsVerdict) -> dict:
        prompt = self._build_prompt(snap, news_v)
        try:
            from google.genai import types as gtypes
            resp = self._client.models.generate_content(
                model    = AI_CONFIG["model"],
                contents = self._SYSTEM + "\n\n" + prompt,
                config   = gtypes.GenerateContentConfig(
                    temperature=0, max_output_tokens=AI_CONFIG["max_tokens"]
                ),
            )
            raw = resp.text
            clean = re.sub(r"```json|```", "", raw).strip()
            m = re.search(r"\{.*\}", clean, re.DOTALL)
            return json.loads(m.group(0) if m else clean)
        except Exception as e:
            logger.error("통합 판단 Gemini 오류 [{}]: {}", snap.ticker, e)
            return {"action":"HOLD","confidence":0,"reason":"API 오류",
                    "target_price":snap.current_price,"stop_loss":int(snap.current_price*0.97),"position_size":"SMALL"}

    @staticmethod
    def _build_prompt(snap: StockSnapshot, news_v: NewsVerdict) -> str:
        news_icon = {"호재":"🟢","악재":"🔴","중립":"⚪","분석불가":"❓"}.get(news_v.judgment,"")
        key_pts = "\n".join(f"  • {p}" for p in news_v.key_points) if news_v.key_points else "  • 없음"

        return f"""
종목: {snap.ticker} | 현재가: {snap.current_price:,}원

━━━ 기술지표 ━━━
RSI(14):      {snap.rsi:.1f}  {'⚠ 과매도' if snap.rsi<30 else '⚠ 과매수' if snap.rsi>70 else ''}
MACD 크로스:  {'✅ 골든크로스' if snap.macd_cross else '❌'}
볼린저밴드:    {snap.bollinger_position}
MA5/MA20:     {snap.ma5:,.0f} / {snap.ma20:,.0f}  ({'↑' if snap.ma5>snap.ma20 else '↓'})
거래량비율:    {snap.volume_ratio:.1f}배
스토캐스틱K:   {snap.stochastic_k:.1f}

━━━ 뉴스 호재/악재 분석 ━━━
판정:      {news_icon} {news_v.judgment}  (스코어: {news_v.score:+d}점)
근거:      {news_v.reason}
핵심 포인트:
{key_pts}
분석 뉴스:  {news_v.news_count}건

위 기술지표와 뉴스를 종합하여 JSON으로만 응답하세요.
뉴스 악재가 있으면 매수 신뢰도를 낮추고, 호재면 신뢰도를 높이세요.
"""

    @staticmethod
    def _calc_adjustment(news_v: NewsVerdict) -> int:
        """뉴스 스코어에 따른 신뢰도 조정값 계산"""
        s = news_v.score
        if s >= 60:   return +10   # 강한 호재
        if s >= 30:   return +5    # 약한 호재
        if s <= -30:  return -15   # 약한 악재
        if s <= -60:  return -25   # 강한 악재 (이미 차단됐지만 SELL에는 적용)
        return 0                   # 중립

    @staticmethod
    def _blocked_verdict(snap: StockSnapshot, news_v: NewsVerdict) -> IntegratedVerdict:
        return IntegratedVerdict(
            ticker="", action="HOLD", confidence=0,
            reason=f"뉴스 강한 악재로 매수 차단 — {news_v.reason[:80]}",
            target_price=snap.current_price,
            stop_loss=int(snap.current_price * 0.97),
            position_size="SMALL",
            news_judgment=news_v.judgment, news_score=news_v.score,
            news_reason=news_v.reason, news_key_points=news_v.key_points,
            confidence_adj=0, news_blocked=True,
        )

    @staticmethod
    def _mock_judge(snap: StockSnapshot) -> dict:
        import random
        actions = ["BUY","HOLD","HOLD","SELL"]
        action  = random.choice(actions)
        return {
            "action":       action,
            "confidence":   random.randint(55, 88),
            "reason":       f"[Mock] RSI {snap.rsi:.1f} 기반 {action} 판단",
            "target_price": int(snap.current_price * 1.06),
            "stop_loss":    int(snap.current_price * 0.97),
            "position_size":"MEDIUM",
        }
