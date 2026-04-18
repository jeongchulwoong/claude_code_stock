"""
foreign/signal_engine.py — 해외주식 AI 신호 생성 + 텔레그램 발송

흐름:
  ForeignSnapshot → Claude API → AIVerdict → 텔레그램 알림
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from google import genai
from google.genai import types as gtypes
import requests
from loguru import logger

from config import GEMINI_API_KEY, AI_CONFIG, DB_PATH, TELEGRAM_CONFIG
from foreign.api_client import ForeignSnapshot

# ── 해외주식 AI 판단 결과 ─────────────────────

@dataclass
class ForeignSignal:
    ticker:        str
    name:          str
    action:        Literal["BUY", "SELL", "HOLD"]
    confidence:    int
    reason:        str
    target_price:  float
    stop_loss:     float
    position_size: str
    current_price: float
    change_pct:    float
    news_sentiment:float
    rsi:           float = 0.0
    generated_at:  str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now().isoformat()

    @property
    def is_actionable(self) -> bool:
        return self.action != "HOLD" and self.confidence >= 70


# ── AI 신호 엔진 ──────────────────────────────

class ForeignSignalEngine:
    """
    해외주식 ForeignSnapshot → Claude API → ForeignSignal 생성.
    """

    _SYSTEM = """\
당신은 30년 경력의 해외주식 퀀트 트레이더 AI입니다.
기술지표와 뉴스 감성을 종합 분석하여 매수·매도·관망을 판단합니다.

원칙:
1. 뉴스 감성이 강하게 부정적(-0.5 이하)이면 매수 신호를 내지 않는다.
2. RSI > 75이면 신규 매수를 금지한다.
3. 불확실할 때는 HOLD를 선택한다.
4. 신뢰도 70점 미만이면 반드시 HOLD이다.

응답은 반드시 JSON만 출력한다:
{
  "action": "BUY"|"SELL"|"HOLD",
  "confidence": 0~100,
  "reason": "판단 근거 2줄 (한국어)",
  "target_price": 목표가(float),
  "stop_loss": 손절가(float),
  "position_size": "SMALL"|"MEDIUM"|"LARGE"
}"""

    def __init__(self) -> None:
        self._mock = not bool(GEMINI_API_KEY)
        if not self._mock:
            self._client = genai.Client(api_key=GEMINI_API_KEY)

    def generate(self, snap: ForeignSnapshot) -> ForeignSignal:
        """ForeignSnapshot → ForeignSignal"""
        if self._mock:
            return self._mock_signal(snap)

        prompt = self._build_prompt(snap)
        try:
            resp = self._client.models.generate_content(
                model    = AI_CONFIG["model"],
                contents = self._SYSTEM + "\n\n" + prompt,
                config   = gtypes.GenerateContentConfig(
                    temperature=AI_CONFIG["temperature"],
                    max_output_tokens=AI_CONFIG["max_tokens"],
                ),
            )
            raw  = resp.text
            data = json.loads(re.sub(r"```json|```", "", raw).strip())
        except Exception as e:
            logger.error("Claude API 오류 [{}]: {}", snap.ticker, e)
            data = {"action":"HOLD","confidence":0,"reason":"API 오류 — 안전 HOLD",
                    "target_price":snap.current_price,"stop_loss":snap.current_price*0.97,
                    "position_size":"SMALL"}

        signal = ForeignSignal(
            ticker        = snap.ticker,
            name          = snap.name,
            action        = data.get("action","HOLD"),
            confidence    = int(data.get("confidence",0)),
            reason        = data.get("reason",""),
            target_price  = float(data.get("target_price", snap.current_price)),
            stop_loss     = float(data.get("stop_loss", snap.current_price * 0.97)),
            position_size = data.get("position_size","SMALL"),
            current_price = snap.current_price,
            change_pct    = snap.change_pct,
            news_sentiment= snap.news_sentiment,
            rsi           = snap.rsi,
        )

        self._save_signal(signal)
        return signal

    def generate_batch(self, snaps: list[ForeignSnapshot]) -> list[ForeignSignal]:
        return [self.generate(s) for s in snaps]

    # ── 프롬프트 ─────────────────────────────

    @staticmethod
    def _build_prompt(snap: ForeignSnapshot) -> str:
        sent_label = (
            "🟢 긍정" if snap.news_sentiment > 0.3 else
            "🔴 부정" if snap.news_sentiment < -0.3 else
            "⚪ 중립"
        )
        return f"""
해외주식 분석 요청:

종목: {snap.ticker} ({snap.name})
현재가: ${snap.current_price:.2f} ({snap.change_pct:+.2f}%)

─── 기술지표 ───
RSI(14):         {snap.rsi:.1f}  {'⚠ 과매도' if snap.rsi < 30 else '⚠ 과매수' if snap.rsi > 70 else ''}
MACD:            {snap.macd:.3f} | Signal: {snap.macd_signal:.3f}
MACD 골든크로스:  {'✅ 발생' if snap.macd_cross else '❌ 미발생'}
볼린저밴드:       {snap.bb_position} (상단:{snap.bb_upper:.2f} / 하단:{snap.bb_lower:.2f})

─── 뉴스 감성 ───
감성 점수: {snap.news_sentiment:+.3f} {sent_label} ({snap.news_count}건)
주요 뉴스: {snap.news_summary}

위 데이터를 종합하여 JSON으로만 응답하세요.
"""

    # ── DB 저장 ───────────────────────────────

    @staticmethod
    def _save_signal(signal: ForeignSignal) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS foreign_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT, ticker TEXT, name TEXT,
                    action TEXT, confidence INTEGER, reason TEXT,
                    target_price REAL, stop_loss REAL,
                    current_price REAL, change_pct REAL,
                    news_sentiment REAL, position_size TEXT
                )
            """)
            con.execute(
                "INSERT INTO foreign_signals VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                (signal.generated_at, signal.ticker, signal.name,
                 signal.action, signal.confidence, signal.reason,
                 signal.target_price, signal.stop_loss,
                 signal.current_price, signal.change_pct,
                 signal.news_sentiment, signal.position_size),
            )

    # ── Mock ──────────────────────────────────

    @staticmethod
    def _mock_signal(snap: ForeignSnapshot) -> ForeignSignal:
        import random
        actions = ["BUY","HOLD","HOLD","SELL"]
        action  = random.choice(actions)
        conf    = random.randint(55, 90) if action != "HOLD" else random.randint(30, 65)
        return ForeignSignal(
            ticker        = snap.ticker,
            name          = snap.name,
            action        = action,
            confidence    = conf,
            reason        = f"[Mock] RSI {snap.rsi:.1f} | 뉴스 감성 {snap.news_sentiment:+.2f}",
            target_price  = snap.current_price * 1.06,
            stop_loss     = snap.current_price * 0.97,
            position_size = "MEDIUM",
            current_price = snap.current_price,
            change_pct    = snap.change_pct,
            news_sentiment= snap.news_sentiment,
            rsi           = snap.rsi,
        )


# ── 텔레그램 알림 발송 ────────────────────────

class ForeignTelegramNotifier:
    """
    해외주식 신호를 텔레그램으로 발송한다.
    프로젝트 지식의 알림 형식을 그대로 구현.
    """

    _API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self) -> None:
        self._token   = TELEGRAM_CONFIG["bot_token"]
        self._chat_id = TELEGRAM_CONFIG["chat_id"]
        self._enabled = bool(self._token and self._chat_id)

    def send_signal(self, signal: ForeignSignal) -> bool:
        """매수/매도 신호 알림 발송. HOLD는 전송하지 않는다."""
        if signal.action == "HOLD":
            return False

        icon   = "🟢" if signal.action == "BUY" else "🔴"
        label  = "매수 신호" if signal.action == "BUY" else "매도 신호"
        pct_t  = (signal.target_price - signal.current_price) / signal.current_price * 100
        pct_sl = (signal.stop_loss    - signal.current_price) / signal.current_price * 100
        sent_emoji = "🟢" if signal.news_sentiment > 0.3 else "🔴" if signal.news_sentiment < -0.3 else "⚪"

        msg = (
            f"{icon} [{label}] {signal.ticker}\n"
            f"{'━' * 22}\n"
            f"현재가: ${signal.current_price:.2f} ({signal.change_pct:+.2f}%)\n"
            f"\nAI 판단 근거:\n  {signal.reason}\n"
            f"\n뉴스 감성: {sent_emoji} {signal.news_sentiment:+.3f}\n"
            f"\n목표가:  ${signal.target_price:.2f} ({pct_t:+.1f}%)\n"
            f"손절가:  ${signal.stop_loss:.2f} ({pct_sl:+.1f}%)\n"
            f"신뢰도:  {signal.confidence}점 | 포지션: {signal.position_size}\n"
            f"{'━' * 22}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self._send(msg)

    def send_watchlist_summary(self, signals: list[ForeignSignal]) -> bool:
        """전체 감시 종목 요약 알림"""
        actionable = [s for s in signals if s.is_actionable]
        lines = [f"📊 해외주식 스캔 완료 ({len(signals)}종목)\n{'━'*22}"]
        for s in signals:
            icon = "🟢" if s.action=="BUY" else "🔴" if s.action=="SELL" else "🟡"
            lines.append(
                f"{icon} {s.ticker:6} ${s.current_price:>8.2f}  "
                f"RSI:{s.rsi:>5.1f}  신뢰:{s.confidence}점"
            )
        lines.append(f"{'━'*22}")
        lines.append(f"실행 가능 신호: {len(actionable)}건")
        lines.append(f"⏰ {datetime.now().strftime('%H:%M:%S')}")
        return self._send("\n".join(lines))

    def _send(self, text: str) -> bool:
        if not self._enabled:
            logger.info("[텔레그램 미전송 — 설정 없음]\n{}", text)
            return False
        try:
            url  = self._API.format(token=self._token)
            resp = requests.post(
                url, json={"chat_id": self._chat_id, "text": text}, timeout=5
            )
            if resp.status_code == 200:
                logger.info("텔레그램 발송 성공: {}자", len(text))
                return True
            logger.warning("텔레그램 실패: {}", resp.status_code)
        except Exception as e:
            logger.error("텔레그램 오류 (무시): {}", e)
        return False
