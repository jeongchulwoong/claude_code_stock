"""
core/telegram_commander.py — 텔레그램 양방향 명령 처리

사용자가 텔레그램으로 보낸 명령을 실시간으로 처리한다.

지원 명령어:
  /status     — 현재 포트폴리오 현황
  /positions  — 보유 종목 목록
  /pnl        — 오늘 / 누적 손익
  /orders     — 최근 주문 내역 10건
  /ai         — 오늘 AI 판단 로그
  /risk       — 리스크 파라미터 현황
  /halt       — 거래 즉시 중단 (긴급)
  /resume     — 거래 재개
  /report     — 일일 리포트 즉시 생성
  /help       — 명령어 목록

실행:
    commander = TelegramCommander(risk_manager, report_generator)
    commander.start_polling()   # 별도 스레드에서 실행
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional, Callable

import requests
from loguru import logger

from config import DB_PATH, RISK_CONFIG, TELEGRAM_CONFIG


class TelegramCommander:
    """
    텔레그램 봇 롱폴링(Long Polling) 기반 명령 처리기.
    실거래 / 페이퍼 트레이딩 모두 사용 가능.
    """

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        risk_manager=None,
        report_generator=None,
        order_manager=None,
    ) -> None:
        self._token    = TELEGRAM_CONFIG["bot_token"]
        self._chat_id  = TELEGRAM_CONFIG["chat_id"]
        self._enabled  = bool(self._token and self._chat_id)
        self._rm       = risk_manager
        self._rg       = report_generator
        self._om       = order_manager
        self._offset   = 0
        self._running  = False

        # 명령어 → 핸들러 매핑
        self._handlers: dict[str, Callable] = {
            "/status":    self._cmd_status,
            "/positions": self._cmd_positions,
            "/pnl":       self._cmd_pnl,
            "/orders":    self._cmd_orders,
            "/ai":        self._cmd_ai_log,
            "/risk":      self._cmd_risk,
            "/halt":      self._cmd_halt,
            "/resume":    self._cmd_resume,
            "/report":    self._cmd_report,
            "/help":      self._cmd_help,
            "/start":     self._cmd_help,
        }

        if not self._enabled:
            logger.warning("텔레그램 설정 없음 — Commander 비활성화")
        else:
            logger.info("TelegramCommander 초기화 완료")

    # ── 폴링 루프 ─────────────────────────────

    def start_polling(self, poll_interval: float = 3.0) -> None:
        """별도 스레드에서 롱폴링 시작"""
        if not self._enabled:
            return
        self._running = True
        t = threading.Thread(
            target=self._poll_loop,
            args=(poll_interval,),
            daemon=True,
        )
        t.start()
        logger.info("텔레그램 Commander 폴링 시작 ({}초 간격)", poll_interval)

    def stop(self) -> None:
        self._running = False

    def _poll_loop(self, interval: float) -> None:
        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except Exception as e:
                logger.error("폴링 오류: {}", e)
            time.sleep(interval)

    def _get_updates(self) -> list[dict]:
        url  = self._API.format(token=self._token, method="getUpdates")
        resp = requests.get(
            url,
            params={"offset": self._offset + 1, "timeout": 10},
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            return []
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"]
        return updates

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message", {})
        if not msg:
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()

        # 허가된 채팅만 처리
        if chat_id != str(self._chat_id):
            logger.warning("미허가 채팅 무시: {}", chat_id)
            return

        # 명령어 처리
        cmd = text.split()[0].lower().split("@")[0]  # @봇이름 제거
        handler = self._handlers.get(cmd)
        if handler:
            logger.info("명령 수신: {}", cmd)
            try:
                reply = handler(text)
                self._send(reply)
            except Exception as e:
                self._send(f"❌ 명령 처리 오류: {e}")
        else:
            self._send(
                f"❓ 알 수 없는 명령: {cmd}\n/help 로 명령어 목록 확인"
            )

    # ── 명령 핸들러 ───────────────────────────

    def _cmd_help(self, _: str = "") -> str:
        return (
            "🤖 AI 자동매매 봇 명령어\n"
            "━" * 24 + "\n"
            "/status    — 포트폴리오 현황\n"
            "/positions — 보유 종목 목록\n"
            "/pnl       — 오늘 / 누적 손익\n"
            "/orders    — 최근 주문 10건\n"
            "/ai        — AI 판단 로그\n"
            "/risk      — 리스크 파라미터\n"
            "/halt      — ⛔ 거래 즉시 중단\n"
            "/resume    — ▶️ 거래 재개\n"
            "/report    — 일일 리포트 생성\n"
            "/help      — 이 도움말"
        )

    def _cmd_status(self, _: str = "") -> str:
        try:
            with sqlite3.connect(DB_PATH) as con:
                total  = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                today  = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=DATE('now')"
                ).fetchone()[0]
                filled = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0]
        except Exception:
            total = today = filled = 0

        halted = self._rm.is_halted() if self._rm else False
        halt_str = "⛔ 거래 중단 중" if halted else "✅ 정상 운영 중"

        return (
            f"📊 시스템 현황\n"
            f"━" * 24 + "\n"
            f"상태:     {halt_str}\n"
            f"총 주문:  {total}건\n"
            f"오늘 주문: {today}건\n"
            f"체결:     {filled}건\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )

    def _cmd_positions(self, _: str = "") -> str:
        positions = {}
        if self._rm:
            positions = self._rm.get_positions()
        else:
            positions = self._load_positions_from_db()

        if not positions:
            return "📂 현재 보유 종목 없음"

        lines = ["💼 보유 종목 현황\n" + "━"*24]
        for ticker, pos in positions.items():
            lines.append(
                f"  {ticker}: {pos.qty}주 "
                f"@{pos.avg_price:,.0f}원"
            )
        lines.append(f"\n총 {len(positions)}종목")
        return "\n".join(lines)

    def _cmd_pnl(self, _: str = "") -> str:
        try:
            with sqlite3.connect(DB_PATH) as con:
                today = con.execute(
                    "SELECT COALESCE(SUM(CASE WHEN order_type='SELL' THEN qty*price "
                    "ELSE -qty*price END),0) FROM orders "
                    "WHERE DATE(timestamp)=DATE('now') "
                    "AND status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0] or 0
                total = con.execute(
                    "SELECT COALESCE(SUM(CASE WHEN order_type='SELL' THEN qty*price "
                    "ELSE -qty*price END),0) FROM orders "
                    "WHERE status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0] or 0
        except Exception:
            today = total = 0

        t_icon = "📈" if today >= 0 else "📉"
        c_icon = "📈" if total >= 0 else "📉"

        return (
            f"💰 손익 현황\n"
            f"━" * 24 + "\n"
            f"오늘 실현손익: {t_icon} {today:+,.0f}원\n"
            f"누적 실현손익: {c_icon} {total:+,.0f}원\n"
            f"일손실 한도:    {RISK_CONFIG['daily_loss_limit']:,.0f}원\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )

    def _cmd_orders(self, _: str = "") -> str:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT timestamp, ticker, order_type, qty, price, status "
                    "FROM orders ORDER BY timestamp DESC LIMIT 10"
                ).fetchall()
        except Exception:
            return "❌ 주문 내역 조회 실패"

        if not rows:
            return "📭 주문 내역 없음"

        lines = ["📋 최근 주문 10건\n" + "━"*24]
        for ts, ticker, otype, qty, price, status in rows:
            t = ts[5:16] if len(ts) > 16 else ts
            icon = "🟢" if otype == "BUY" else "🔴"
            st   = "✅" if "FILLED" in (status or "") else "⛔"
            lines.append(f"{icon}{st} {t} {ticker} {otype} {qty}주 @{(price or 0):,.0f}")
        return "\n".join(lines)

    def _cmd_ai_log(self, _: str = "") -> str:
        import json
        from pathlib import Path
        log_dir  = Path(__file__).parent.parent / "logs"
        today_str= datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"ai_judge_{today_str}.log"

        if not log_file.exists():
            return "📭 오늘 AI 판단 로그 없음"

        records = []
        for line in log_file.read_text(encoding="utf-8").splitlines()[-10:]:
            try:
                records.append(json.loads(line))
            except Exception:
                pass

        if not records:
            return "📭 AI 로그 파싱 실패"

        icon_map = {"BUY":"🟢","SELL":"🔴","HOLD":"🟡"}
        lines = [f"🤖 AI 판단 로그 (최근 {len(records)}건)\n" + "━"*24]
        for r in records:
            icon = icon_map.get(r.get("action",""),"⚪")
            lines.append(
                f"{icon} {r.get('ticker','?')} {r.get('action','?')} "
                f"신뢰:{r.get('confidence',0)}점"
            )
        return "\n".join(lines)

    def _cmd_risk(self, _: str = "") -> str:
        halted = self._rm.is_halted() if self._rm else False
        daily_pnl = self._rm.get_daily_pnl() if self._rm else 0

        return (
            f"⚖️ 리스크 파라미터\n"
            f"━" * 24 + "\n"
            f"거래 상태:    {'⛔ 중단' if halted else '✅ 정상'}\n"
            f"일일 손익:    {daily_pnl:+,.0f}원\n"
            f"일손실 한도:  {RISK_CONFIG['daily_loss_limit']:,.0f}원\n"
            f"손절선:       {RISK_CONFIG['stop_loss_pct']:.0%}\n"
            f"익절선:       {RISK_CONFIG['take_profit_pct']:.0%}\n"
            f"최소 신뢰도:  {RISK_CONFIG['min_confidence']}점\n"
            f"최대 종목:    {RISK_CONFIG['max_positions']}개\n"
            f"1회 투자한도: {RISK_CONFIG['max_invest_per_trade']:,.0f}원"
        )

    def _cmd_halt(self, _: str = "") -> str:
        if self._rm:
            self._rm._halted = True
            logger.critical("텔레그램 명령으로 거래 강제 중단")
        return (
            "⛔ 거래 강제 중단 완료\n"
            "모든 신규 주문이 차단됩니다.\n"
            "재개하려면 /resume 을 입력하세요."
        )

    def _cmd_resume(self, _: str = "") -> str:
        if self._rm:
            self._rm._halted = False
            self._rm._daily_pnl = 0.0
            logger.info("텔레그램 명령으로 거래 재개")
        return "▶️ 거래 재개 완료\n일일 손익이 초기화됩니다."

    def _cmd_report(self, _: str = "") -> str:
        if self._rg:
            try:
                self._rg.generate_daily_report()
                return "📊 일일 리포트 생성 완료 (별도 메시지로 전송됨)"
            except Exception as e:
                return f"❌ 리포트 생성 실패: {e}"
        return "❌ 리포트 생성기 미연결"

    # ── 메시지 전송 ───────────────────────────

    def _send(self, text: str) -> None:
        if not self._enabled:
            print(f"[TG CMD 응답]\n{text}")
            return
        try:
            url = self._API.format(token=self._token, method="sendMessage")
            requests.post(url, json={
                "chat_id": self._chat_id,
                "text": text,
            }, timeout=5)
        except Exception as e:
            logger.error("Commander 메시지 전송 실패: {}", e)

    def send_startup_message(self) -> None:
        """시스템 시작 알림"""
        from config import PAPER_TRADING, WATCH_LIST
        mode = "📄 페이퍼 트레이딩" if PAPER_TRADING else "💰 실거래"
        self._send(
            f"🤖 AI 자동매매 시스템 시작\n"
            f"━" * 24 + "\n"
            f"모드:       {mode}\n"
            f"감시 종목:  {', '.join(WATCH_LIST[:5])}\n"
            f"손절선:     {RISK_CONFIG['stop_loss_pct']:.0%}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"/help 로 명령어 확인"
        )

    def _load_positions_from_db(self) -> dict:
        """risk_manager 없을 때 DB에서 포지션 로드"""
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT ticker, order_type, qty, price FROM orders "
                    "WHERE status IN ('FILLED','PAPER_FILLED') ORDER BY timestamp"
                ).fetchall()
        except Exception:
            return {}
        pos: dict[str, dict] = {}
        for ticker, otype, qty, price in rows:
            if ticker not in pos:
                pos[ticker] = {"qty": 0, "avg_price": 0.0}
            if otype == "BUY":
                total_cost = pos[ticker]["avg_price"] * pos[ticker]["qty"] + qty * price
                pos[ticker]["qty"] += qty
                pos[ticker]["avg_price"] = total_cost / pos[ticker]["qty"] if pos[ticker]["qty"] else 0
            elif otype == "SELL":
                pos[ticker]["qty"] -= qty
        class MockPos:
            def __init__(self, q, a): self.qty=q; self.avg_price=a
        return {t: MockPos(d["qty"], d["avg_price"]) for t, d in pos.items() if d["qty"] > 0}
