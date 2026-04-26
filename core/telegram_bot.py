"""
Concise Telegram notifications.

Only sends actionable summaries: orders, high-score candidates, halts, and
explicit lifecycle messages. Failed notifications are logged and never block
trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests
from loguru import logger

from config import TELEGRAM_CONFIG, fmt_price
from core.ai_judge import AIVerdict


class TelegramBot:
    _API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self) -> None:
        self._token = TELEGRAM_CONFIG["bot_token"]
        self._chat_id = TELEGRAM_CONFIG["chat_id"]
        self._enabled = bool(self._token and self._chat_id)
        logger.info("TelegramBot {}", "enabled" if self._enabled else "disabled")

    def notify_verdict(self, verdict: AIVerdict, current_price: float) -> None:
        if verdict.action == "HOLD":
            return

        try:
            pct_target = (float(verdict.target_price) - current_price) / current_price * 100
            pct_stop = (float(verdict.stop_loss) - current_price) / current_price * 100
        except Exception:
            pct_target = 0.0
            pct_stop = 0.0

        action = "BUY" if verdict.action == "BUY" else "SELL"
        msg = (
            f"[{action}] {verdict.ticker} | {int(verdict.confidence)}점\n"
            f"현재 {fmt_price(verdict.ticker, current_price)} | 목표 {pct_target:+.1f}% | 손절 {pct_stop:+.1f}%\n"
            f"{str(verdict.reason)[:160]}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(msg)

    def notify_order_filled(
        self,
        ticker: str,
        order_type: str,
        qty: int,
        price: float,
        pnl: Optional[float] = None,
    ) -> None:
        pnl_str = f" | PnL {pnl:+,.0f}원" if pnl is not None else ""
        msg = (
            f"[ORDER {order_type}] {ticker}\n"
            f"{qty}주 @ {fmt_price(ticker, price)} | 금액 {fmt_price(ticker, qty * price)}{pnl_str}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(msg)

    def notify_halt(self, daily_pnl: float) -> None:
        self._send(
            f"[거래 중단]\n일일 손익 {daily_pnl:+,.0f}원\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def notify_hot_candidates(self, candidates: list, title: str = "70점 이상 후보") -> None:
        if not candidates:
            return
        lines = [f"{title} ({len(candidates)}개)"]
        for i, c in enumerate(candidates[:5], 1):
            is_kr = c.ticker.endswith(".KS") or c.ticker.endswith(".KQ")
            price = f"{int(c.current_price):,}원" if is_kr else f"${c.current_price:.2f}"
            ai = f"AI {c.ai_action} {c.ai_score:.0f}" if getattr(c, "ai_action", "") else "AI -"
            news_score = float(getattr(c, "news_score", 0) or 0)
            news = f"뉴스 {news_score:+.0f}"
            setup = getattr(c, "setup_type", "") or ""
            reasons = " / ".join([r for r in [setup, *c.reasons[:2]] if r])[:120]
            lines.append(
                f"{i}. {c.name} {c.ticker} | {c.score:.0f}점 | {price}\n"
                f"   {ai} | {news} | {reasons}"
            )
        lines.append(datetime.now().strftime("%H:%M:%S"))
        self._send("\n".join(lines))

    def notify_text(self, text: str) -> None:
        self._send(self._compact(text))

    def _send(self, text: str) -> None:
        if not self._enabled:
            logger.debug("[telegram disabled] {}", text[:120])
            return
        try:
            resp = requests.post(
                self._API.format(token=self._token),
                json={"chat_id": self._chat_id, "text": text[:3500], "parse_mode": ""},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("Telegram send failed: {} {}", resp.status_code, resp.text[:120])
        except Exception as e:
            logger.error("Telegram error ignored: {}", e)

    @staticmethod
    def _compact(text: str) -> str:
        lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        return "\n".join(lines[:8])[:1200]
