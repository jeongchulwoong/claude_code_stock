"""
core/alert_manager.py — 가격·조건 기반 알림 시스템

지원 알림 유형:
  PRICE_ABOVE    — 현재가 > 목표가
  PRICE_BELOW    — 현재가 < 목표가
  RSI_OVERSOLD   — RSI < 기준값
  RSI_OVERBOUGHT — RSI > 기준값
  VOLUME_SURGE   — 거래량 > N배
  MACD_CROSS     — MACD 골든/데드크로스
  STOP_LOSS      — 보유 종목 손절선 도달
  TAKE_PROFIT    — 보유 종목 익절선 도달
  NEWS_NEGATIVE  — 뉴스 악재 감지 (score ≤ -50)
  NEWS_POSITIVE  — 뉴스 호재 감지 (score ≥ +50)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import requests
from loguru import logger

from config import DB_PATH, TELEGRAM_CONFIG


# ── 알림 유형 ──────────────────────────────────

class AlertType(str, Enum):
    PRICE_ABOVE    = "PRICE_ABOVE"
    PRICE_BELOW    = "PRICE_BELOW"
    RSI_OVERSOLD   = "RSI_OVERSOLD"
    RSI_OVERBOUGHT = "RSI_OVERBOUGHT"
    VOLUME_SURGE   = "VOLUME_SURGE"
    MACD_CROSS     = "MACD_CROSS"
    STOP_LOSS      = "STOP_LOSS"
    TAKE_PROFIT    = "TAKE_PROFIT"
    NEWS_NEGATIVE  = "NEWS_NEGATIVE"
    NEWS_POSITIVE  = "NEWS_POSITIVE"


# ── 알림 규칙 ──────────────────────────────────

@dataclass
class AlertRule:
    rule_id:    str
    ticker:     str
    name:       str
    alert_type: AlertType
    threshold:  float        # 기준값 (가격 / RSI 값 / 거래량 배수 등)
    message:    str = ""     # 사용자 정의 메시지
    active:     bool = True
    triggered:  bool = False  # 이미 트리거됐으면 True (1회성)
    repeat:     bool = False  # True면 반복 알림
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AlertEvent:
    rule_id:    str
    ticker:     str
    alert_type: str
    message:    str
    value:      float    # 실제 감지된 값
    threshold:  float    # 기준값
    fired_at:   str = field(default_factory=lambda: datetime.now().isoformat())


# ── 알림 매니저 ────────────────────────────────

class AlertManager:
    """
    알림 규칙을 등록하고 매 스캔마다 조건을 체크하여
    트리거 시 텔레그램으로 발송한다.
    """

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []
        self._token   = TELEGRAM_CONFIG["bot_token"]
        self._chat_id = TELEGRAM_CONFIG["chat_id"]
        self._tg_ok   = bool(self._token and self._chat_id)
        self._init_db()
        self._load_rules_from_db()

    # ── 규칙 등록 ─────────────────────────────

    def add_price_alert(
        self, ticker: str, name: str,
        price: float, direction: str = "above",
        message: str = "", repeat: bool = False,
    ) -> str:
        """가격 알림 등록. direction: 'above' | 'below'"""
        alert_type = AlertType.PRICE_ABOVE if direction == "above" else AlertType.PRICE_BELOW
        return self._add_rule(ticker, name, alert_type, price, message, repeat)

    def add_rsi_alert(
        self, ticker: str, name: str,
        rsi_threshold: float, direction: str = "below",
        message: str = "",
    ) -> str:
        alert_type = AlertType.RSI_OVERSOLD if direction == "below" else AlertType.RSI_OVERBOUGHT
        return self._add_rule(ticker, name, alert_type, rsi_threshold, message)

    def add_volume_alert(
        self, ticker: str, name: str,
        multiplier: float = 3.0, message: str = "",
    ) -> str:
        return self._add_rule(ticker, name, AlertType.VOLUME_SURGE, multiplier, message, repeat=True)

    def add_news_alert(
        self, ticker: str, name: str, direction: str = "negative",
    ) -> str:
        alert_type = AlertType.NEWS_NEGATIVE if direction == "negative" else AlertType.NEWS_POSITIVE
        threshold  = -50.0 if direction == "negative" else 50.0
        return self._add_rule(ticker, name, alert_type, threshold, repeat=True)

    def remove_rule(self, rule_id: str) -> bool:
        self._rules = [r for r in self._rules if r.rule_id != rule_id]
        with sqlite3.connect(DB_PATH) as con:
            con.execute("UPDATE alert_rules SET active=0 WHERE rule_id=?", (rule_id,))
        return True

    def list_rules(self) -> list[AlertRule]:
        return [r for r in self._rules if r.active]

    # ── 조건 체크 ─────────────────────────────

    def check(self, snap) -> list[AlertEvent]:
        """
        등록된 모든 규칙에 대해 조건을 확인한다.
        snap: StockSnapshot 또는 유사 객체
        """
        ticker  = getattr(snap, "ticker", "")
        events: list[AlertEvent] = []

        for rule in self._rules:
            if not rule.active or rule.ticker != ticker:
                continue
            if rule.triggered and not rule.repeat:
                continue

            event = self._evaluate(rule, snap)
            if event:
                events.append(event)
                rule.triggered = True
                self._send_alert(event)
                self._save_event(event)

        return events

    def check_news(self, ticker: str, news_score: float) -> list[AlertEvent]:
        """뉴스 감성 점수 기반 알림 체크"""
        events = []
        for rule in self._rules:
            if not rule.active or rule.ticker != ticker:
                continue
            if rule.triggered and not rule.repeat:
                continue

            triggered = False
            if rule.alert_type == AlertType.NEWS_NEGATIVE and news_score <= rule.threshold:
                triggered = True
            elif rule.alert_type == AlertType.NEWS_POSITIVE and news_score >= rule.threshold:
                triggered = True

            if triggered:
                event = AlertEvent(
                    rule_id    = rule.rule_id,
                    ticker     = ticker,
                    alert_type = rule.alert_type.value,
                    message    = f"뉴스 {rule.alert_type.value}: 감성점수 {news_score:+.0f}",
                    value      = news_score,
                    threshold  = rule.threshold,
                )
                rule.triggered = True
                events.append(event)
                self._send_alert(event)
                self._save_event(event)
        return events

    # ── 내부 평가 ─────────────────────────────

    @staticmethod
    def _evaluate(rule: AlertRule, snap) -> Optional[AlertEvent]:
        price     = getattr(snap, "current_price", 0)
        rsi       = getattr(snap, "rsi",           50.0)
        vol_ratio = getattr(snap, "volume_ratio",   1.0)
        macd_cross= getattr(snap, "macd_cross",   False)

        triggered = False
        value     = 0.0

        if rule.alert_type == AlertType.PRICE_ABOVE and price >= rule.threshold:
            triggered = True; value = price
        elif rule.alert_type == AlertType.PRICE_BELOW and price <= rule.threshold:
            triggered = True; value = price
        elif rule.alert_type == AlertType.RSI_OVERSOLD and rsi <= rule.threshold:
            triggered = True; value = rsi
        elif rule.alert_type == AlertType.RSI_OVERBOUGHT and rsi >= rule.threshold:
            triggered = True; value = rsi
        elif rule.alert_type == AlertType.VOLUME_SURGE and vol_ratio >= rule.threshold:
            triggered = True; value = vol_ratio
        elif rule.alert_type == AlertType.MACD_CROSS and macd_cross:
            triggered = True; value = 1.0

        if not triggered:
            return None

        icon_map = {
            AlertType.PRICE_ABOVE:    "📈",
            AlertType.PRICE_BELOW:    "📉",
            AlertType.RSI_OVERSOLD:   "🟡",
            AlertType.RSI_OVERBOUGHT: "🔴",
            AlertType.VOLUME_SURGE:   "🔥",
            AlertType.MACD_CROSS:     "⚡",
        }
        icon = icon_map.get(rule.alert_type, "🔔")
        msg  = rule.message or f"{icon} [{rule.alert_type.value}] {rule.name}({rule.ticker}) — 현재값:{value:.2f} / 기준:{rule.threshold:.2f}"

        return AlertEvent(
            rule_id    = rule.rule_id,
            ticker     = rule.ticker,
            alert_type = rule.alert_type.value,
            message    = msg,
            value      = value,
            threshold  = rule.threshold,
        )

    # ── 텔레그램 발송 ─────────────────────────

    def _send_alert(self, event: AlertEvent) -> None:
        logger.info("알림 트리거: {} | {}", event.ticker, event.message)
        if not self._tg_ok:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": event.message},
                timeout=5,
            )
        except Exception as e:
            logger.error("알림 발송 실패: {}", e)

    # ── DB ────────────────────────────────────

    def _add_rule(
        self, ticker: str, name: str,
        alert_type: AlertType, threshold: float,
        message: str = "", repeat: bool = False,
    ) -> str:
        import uuid
        rule_id = str(uuid.uuid4())[:8]
        rule = AlertRule(
            rule_id=rule_id, ticker=ticker, name=name,
            alert_type=alert_type, threshold=threshold,
            message=message, repeat=repeat,
        )
        self._rules.append(rule)
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO alert_rules (rule_id,ticker,name,alert_type,threshold,message,repeat,active,created_at) "
                "VALUES (?,?,?,?,?,?,?,1,?)",
                (rule_id, ticker, name, alert_type.value, threshold, message, int(repeat), rule.created_at),
            )
        logger.info("알림 등록: {} {} {} >= {}", ticker, alert_type.value, name, threshold)
        return rule_id

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS alert_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT, ticker TEXT, name TEXT,
                    alert_type TEXT, threshold REAL, message TEXT,
                    repeat INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
                    created_at TEXT
                )""")
            con.execute("""
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT, ticker TEXT, alert_type TEXT,
                    message TEXT, value REAL, threshold REAL, fired_at TEXT
                )""")

    def _load_rules_from_db(self) -> None:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT rule_id,ticker,name,alert_type,threshold,message,repeat,created_at "
                    "FROM alert_rules WHERE active=1"
                ).fetchall()
            for r in rows:
                self._rules.append(AlertRule(
                    rule_id=r[0], ticker=r[1], name=r[2],
                    alert_type=AlertType(r[3]), threshold=r[4],
                    message=r[5] or "", repeat=bool(r[6]), created_at=r[7],
                ))
        except Exception:
            pass

    def _save_event(self, event: AlertEvent) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO alert_events (rule_id,ticker,alert_type,message,value,threshold,fired_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (event.rule_id, event.ticker, event.alert_type,
                 event.message, event.value, event.threshold, event.fired_at),
            )
