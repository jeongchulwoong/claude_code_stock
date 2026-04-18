"""
core/ai_judge.py — Claude API 기반 매수·매도·홀드 판단 엔진

입력: StockSnapshot
출력: AIVerdict (action, confidence, reason, target_price, stop_loss, position_size)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import types as gtypes
from loguru import logger

from config import AI_CONFIG, GEMINI_API_KEY, LOG_DIR, RISK_CONFIG
from core.data_collector import StockSnapshot

# ── 판단 결과 구조 ────────────────────────────

@dataclass
class AIVerdict:
    ticker:        str
    action:        Literal["BUY", "SELL", "HOLD"]
    confidence:    int           # 0~100
    reason:        str
    target_price:  int
    stop_loss:     int
    position_size: Literal["SMALL", "MEDIUM", "LARGE"]
    raw_response:  str = ""      # 디버깅용 원본 응답

    @property
    def is_executable(self) -> bool:
        """신뢰도 기준 통과 여부"""
        return self.confidence >= RISK_CONFIG["min_confidence"]

    def to_log_dict(self) -> dict:
        return {
            "ticker":        self.ticker,
            "action":        self.action,
            "confidence":    self.confidence,
            "reason":        self.reason,
            "target_price":  self.target_price,
            "stop_loss":     self.stop_loss,
            "position_size": self.position_size,
            "executable":    self.is_executable,
        }


# ── AI 판단 엔진 ──────────────────────────────

class AIJudge:
    """
    Claude API를 호출하여 매수·매도·홀드 판단을 수행한다.
    
    - temperature=0 고정 (재현성)
    - JSON 응답 파싱 실패 시 HOLD 반환 (안전 장치)
    - 모든 판단 결과를 ai_judge_YYYYMMDD.log에 기록
    """

    _SYSTEM_PROMPT = """\
You are a quant trader AI. Analyze the given stock indicators and output ONLY valid JSON.
Rules: loss prevention > profit. Use multiple indicators. When uncertain, HOLD. confidence<70 must be HOLD.
Respond with ONLY this JSON (no extra text, reason must be under 80 chars):
{"action":"BUY"|"SELL"|"HOLD","confidence":0-100,"reason":"short English reason under 80 chars","target_price":int,"stop_loss":int,"position_size":"SMALL"|"MEDIUM"|"LARGE"}
"""

    def __init__(self) -> None:
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY 미설정 — MockAIJudge 모드로 동작")
            self._mock = True
        else:
            self._client = genai.Client(api_key=GEMINI_API_KEY)
            self._mock = False

    def judge(self, snap: StockSnapshot) -> AIVerdict:
        """StockSnapshot을 받아 AIVerdict를 반환한다."""
        if self._mock:
            return self._mock_verdict(snap)

        prompt = self._build_prompt(snap)
        verdict = None
        for attempt in range(3):
            try:
                import time as _time
                if attempt > 0:
                    _time.sleep(attempt * 3)
                response = self._client.models.generate_content(
                    model    = AI_CONFIG["model"],
                    contents = self._SYSTEM_PROMPT + "\n\n" + prompt,
                    config   = gtypes.GenerateContentConfig(
                        temperature = AI_CONFIG["temperature"],
                        max_output_tokens = AI_CONFIG["max_tokens"],
                    ),
                )
                raw = response.text
                verdict = self._parse_verdict(snap.ticker, raw, snap.current_price)
                break
            except Exception as e:
                logger.warning("Gemini API 시도 {}/3 실패 [{}]: {}", attempt + 1, snap.ticker, e)
        if verdict is None:
            verdict = self._fallback_verdict(snap)

        self._log_verdict(verdict)
        return verdict

    def judge_batch(self, snaps: list[StockSnapshot]) -> list[AIVerdict]:
        """여러 종목을 순차 판단한다."""
        return [self.judge(s) for s in snaps]

    # ── 프롬프트 빌더 ────────────────────────

    @staticmethod
    def _build_prompt(snap: StockSnapshot) -> str:
        return f"""
아래 종목 데이터를 분석하여 매매 판단을 내려주세요.

종목코드: {snap.ticker}
종목명: {snap.name}
현재가: {snap.current_price:,}원

─── 가격 정보 ───
시가: {snap.open_price:,} | 고가: {snap.high_price:,} | 저가: {snap.low_price:,}
거래량: {snap.volume:,} | 거래량비율: {snap.volume_ratio:.1f}배

─── 기술지표 ───
RSI(14):         {snap.rsi:.1f}  {'⚠ 과매도' if snap.rsi < 30 else ('⚠ 과매수' if snap.rsi > 70 else '')}
MACD:            {snap.macd:.1f} | Signal: {snap.macd_signal:.1f}
MACD 골든크로스:  {'✅ 발생' if snap.macd_cross else '❌ 미발생'}
볼린저밴드 위치:  {snap.bollinger_position} (상단:{snap.bollinger_upper:,.0f} / 하단:{snap.bollinger_lower:,.0f})
MA5 / MA20:      {snap.ma5:,.0f} / {snap.ma20:,.0f}
MA5 골든크로스:   {'✅ 발생' if snap.ma5_cross_ma20 else '❌ 미발생'}
스토캐스틱K:     {snap.stochastic_k:.1f}  {'⚠ 과매도' if snap.stochastic_k < 20 else ('⚠ 과매수' if snap.stochastic_k > 80 else '')}

─── 펀더멘털 ───
PER: {snap.per:.1f} | 외국인 보유율: {snap.foreigner_pct:.1f}%

위 데이터를 종합하여 JSON으로만 응답하세요.
"""

    # ── 응답 파싱 ─────────────────────────────

    @staticmethod
    def _parse_verdict(ticker: str, raw: str, current_price: int) -> AIVerdict:
        """Gemini 응답 JSON을 파싱하여 AIVerdict 반환"""
        clean = re.sub(r"```json|```", "", raw).strip()
        # JSON 블록만 추출 (앞뒤 텍스트 제거)
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            clean = m.group(0)
        # 잘린 JSON 복구: 마지막 완전한 key:value 까지만 사용
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            # 닫히지 않은 JSON 강제 복구
            clean = re.sub(r',\s*"[^"]*"\s*:\s*[^,}\]]*$', "", clean).rstrip(",") + "}"
            try:
                data = json.loads(clean)
            except json.JSONDecodeError as e:
                logger.error("JSON 파싱 실패: {} | raw={}", e, raw[:200])
                return AIVerdict(
                    ticker=ticker, action="HOLD", confidence=0,
                    reason="AI response parse error",
                    target_price=current_price, stop_loss=int(current_price * 0.97),
                    position_size="SMALL", raw_response=raw,
                )

        return AIVerdict(
            ticker        = ticker,
            action        = data.get("action", "HOLD"),
            confidence    = int(data.get("confidence", 0)),
            reason        = data.get("reason", ""),
            target_price  = int(data.get("target_price", current_price)),
            stop_loss     = int(data.get("stop_loss", int(current_price * 0.97))),
            position_size = data.get("position_size", "SMALL"),
            raw_response  = raw,
        )

    # ── 로깅 ─────────────────────────────────

    @staticmethod
    def _log_verdict(verdict: AIVerdict) -> None:
        from datetime import date
        log_file = LOG_DIR / f"ai_judge_{date.today().strftime('%Y%m%d')}.log"
        line = json.dumps(verdict.to_log_dict(), ensure_ascii=False)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info(
            "AI 판단 | {} | {} | 신뢰도:{} | {}",
            verdict.ticker, verdict.action, verdict.confidence, verdict.reason,
        )

    # ── Mock / Fallback ───────────────────────

    @staticmethod
    def _mock_verdict(snap: StockSnapshot) -> AIVerdict:
        """API 키 없는 환경에서 사용하는 더미 판단 (항상 HOLD)"""
        logger.info("[MOCK AI] {} → HOLD (API 키 없음)", snap.ticker)
        return AIVerdict(
            ticker=snap.ticker, action="HOLD", confidence=50,
            reason="Mock 모드 — 실제 AI 판단 없음",
            target_price=snap.current_price,
            stop_loss=int(snap.current_price * 0.97),
            position_size="SMALL",
        )

    @staticmethod
    def _fallback_verdict(snap: StockSnapshot) -> AIVerdict:
        """API 호출 실패 시 안전 HOLD 반환"""
        return AIVerdict(
            ticker=snap.ticker, action="HOLD", confidence=0,
            reason="API 오류로 인한 안전 HOLD",
            target_price=snap.current_price,
            stop_loss=int(snap.current_price * 0.97),
            position_size="SMALL",
        )
