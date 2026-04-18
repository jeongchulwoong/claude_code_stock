"""
core/telegram_bot.py — 텔레그램 알림 발송 모듈

알림 실패 시 로그만 기록하고 거래는 계속 진행한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import requests
from loguru import logger

from config import PAPER_TRADING, TELEGRAM_CONFIG
from core.ai_judge import AIVerdict


def _fmt_price(ticker: str, price: float) -> str:
    """티커에 맞는 통화 단위로 가격 포맷"""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"{int(price):,}원"
    if ticker.endswith(".T"):
        return f"¥{int(price):,}"
    if ticker.endswith(".HK"):
        return f"HK${price:.2f}"
    return f"${price:.2f}"


class TelegramBot:
    """
    텔레그램 Bot API를 통해 매매 신호와 체결 알림을 전송한다.
    """

    _API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self) -> None:
        self._token   = TELEGRAM_CONFIG["bot_token"]
        self._chat_id = TELEGRAM_CONFIG["chat_id"]
        self._enabled = bool(self._token and self._chat_id)

        if not self._enabled:
            logger.warning("텔레그램 설정 없음 — 알림 비활성화")
        else:
            logger.info("TelegramBot 초기화 완료")

    # ── 퍼블릭 API ────────────────────────────

    def notify_verdict(self, verdict: AIVerdict, current_price: int) -> None:
        """AI 판단 결과 알림"""
        if verdict.action == "HOLD":
            return

        icon = "🟢" if verdict.action == "BUY" else "🔴"
        action_str = "매수 신호" if verdict.action == "BUY" else "매도 신호"
        mode_tag = "📄 페이퍼 " if PAPER_TRADING else ""

        pct_target = (verdict.target_price - current_price) / current_price * 100
        pct_stop   = (verdict.stop_loss - current_price) / current_price * 100

        fp = lambda p: _fmt_price(verdict.ticker, p)
        msg = (
            f"{icon} {mode_tag}[{action_str}] {verdict.ticker}\n"
            f"{'━' * 22}\n"
            f"현재가: {fp(current_price)}\n"
            f"신뢰도: {verdict.confidence}점 | 포지션: {verdict.position_size}\n"
            f"\nAI 판단 근거:\n  {verdict.reason}\n"
            f"\n목표가: {fp(verdict.target_price)} ({pct_target:+.1f}%)\n"
            f"손절가: {fp(verdict.stop_loss)} ({pct_stop:+.1f}%)\n"
            f"{'━' * 22}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._send(msg)

    def notify_order_filled(
        self,
        ticker: str,
        order_type: str,
        qty: int,
        price: int,
        pnl: Optional[float] = None,
    ) -> None:
        """체결 알림"""
        icon = "✅ 매수체결" if order_type == "BUY" else "✅ 매도체결"
        mode_tag = "[페이퍼] " if PAPER_TRADING else ""
        pnl_str = f"\n실현손익: {pnl:+,.0f}원" if pnl is not None else ""

        fp = lambda p: _fmt_price(ticker, p)
        msg = (
            f"{icon} {mode_tag}{ticker}\n"
            f"{'━' * 22}\n"
            f"수량: {qty}주 | 단가: {fp(price)}\n"
            f"금액: {fp(qty * price)}{pnl_str}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(msg)

    def notify_halt(self, daily_pnl: float) -> None:
        """일일 손실 한도 초과 — 거래 중단 알림"""
        msg = (
            f"⛔ 거래 자동 중단\n"
            f"{'━' * 22}\n"
            f"일일 누적 손실: {daily_pnl:+,.0f}원\n"
            f"손실 한도 초과로 금일 거래를 중단합니다.\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._send(msg)

    def notify_text(self, text: str) -> None:
        """일반 텍스트 알림"""
        self._send(text)

    # ── 내부 발송 ─────────────────────────────

    def _send(self, text: str) -> None:
        if not self._enabled:
            logger.debug("[텔레그램 미전송] {}", text[:80])
            return
        try:
            url = self._API.format(token=self._token)
            resp = requests.post(
                url,
                json={"chat_id": self._chat_id, "text": text, "parse_mode": ""},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("텔레그램 발송 실패: {} {}", resp.status_code, resp.text[:100])
            else:
                logger.debug("텔레그램 발송 성공")
        except Exception as e:
            # 알림 실패 시 거래는 계속 진행
            logger.error("텔레그램 오류 (무시): {}", e)
