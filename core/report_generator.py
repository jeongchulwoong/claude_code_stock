"""
core/report_generator.py — 자동 일일/주간 리포트 생성 + 텔레그램 전송

일일 리포트 (장 마감 후 자동 전송):
  - 오늘 거래 요약 (매수/매도/차단)
  - 실현손익
  - AI 판단 현황
  - 포트폴리오 현황
  - VaR/CVaR

주간 리포트 (금요일 장 마감 후):
  - 주간 누적 손익
  - 전략별 승률
  - 최고/최저 종목
  - 다음 주 주목 종목
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from loguru import logger

from config import DB_PATH, RISK_CONFIG, TELEGRAM_CONFIG

LOG_DIR    = Path(__file__).parent.parent / "logs"
REPORT_DIR = Path(__file__).parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


class ReportGenerator:
    """
    자동 리포트 생성 + 텔레그램 전송 클래스.
    main.py 장 마감 처리 또는 APScheduler에서 호출한다.
    """

    def __init__(self) -> None:
        self._token   = TELEGRAM_CONFIG["bot_token"]
        self._chat_id = TELEGRAM_CONFIG["chat_id"]
        self._tg_ok   = bool(self._token and self._chat_id)

    # ══════════════════════════════════════════
    # 일일 리포트
    # ══════════════════════════════════════════

    def generate_daily_report(self, target_date: date = None) -> str:
        """일일 리포트 텍스트 생성 + 텔레그램 전송"""
        target = target_date or date.today()
        ds     = target.isoformat()

        stats  = self._daily_trade_stats(ds)
        ai_log = self._daily_ai_log(ds)
        pnl    = self._daily_pnl(ds)

        lines = [
            f"📊 일일 자동매매 리포트 — {ds}",
            "━" * 32,
            "",
            "【오늘 거래 요약】",
            f"  총 주문:   {stats['total']}건",
            f"  체결:      {stats['filled']}건",
            f"  차단:      {stats['blocked']}건",
            f"  매수 체결: {stats['buy']}건  /  매도 체결: {stats['sell']}건",
            "",
            "【손익】",
            f"  오늘 실현손익: {pnl['today']:+,.0f}원",
            f"  누적 실현손익: {pnl['total']:+,.0f}원",
            "",
            "【AI 판단 현황】",
            f"  BUY:  {ai_log['buy']}건  |  SELL: {ai_log['sell']}건  |  HOLD: {ai_log['hold']}건",
            f"  평균 신뢰도: {ai_log['avg_conf']:.1f}점",
            "",
        ]

        # 차단 이유
        if stats.get("block_reasons"):
            lines.append("【리스크 차단 상세】")
            for r in stats["block_reasons"][:3]:
                lines.append(f"  • {r}")
            lines.append("")

        # 리스크 경고
        warnings = self._check_risk_warnings(stats, pnl)
        if warnings:
            lines.append("【⚠️ 리스크 경고】")
            for w in warnings:
                lines.append(f"  {w}")
            lines.append("")

        lines += [
            "━" * 32,
            f"⏰ {datetime.now().strftime('%H:%M:%S')} | 실거래",
        ]

        report = "\n".join(lines)
        self._send_telegram(report)
        self._save_report(report, f"daily_{ds}.txt")
        logger.info("일일 리포트 생성 완료: {}", ds)
        return report

    # ══════════════════════════════════════════
    # 주간 리포트
    # ══════════════════════════════════════════

    def generate_weekly_report(self) -> str:
        """주간 리포트 생성 + 텔레그램 전송"""
        today     = date.today()
        week_start= today - timedelta(days=today.weekday())
        week_end  = today

        stats = self._weekly_stats(week_start, week_end)

        lines = [
            f"📈 주간 자동매매 리포트",
            f"   {week_start} ~ {week_end}",
            "━" * 32,
            "",
            "【주간 성과】",
            f"  총 거래:      {stats['total_trades']}건",
            f"  주간 실현손익: {stats['weekly_pnl']:+,.0f}원",
            f"  승리 거래:    {stats['win_trades']}건  "
            f"/ 패배: {stats['lose_trades']}건",
            f"  승률:         {stats['win_rate']:.1f}%",
            "",
            "【최고 / 최저 종목】",
            f"  🏆 최고: {stats['best_ticker']}  ({stats['best_pnl']:+,.0f}원)",
            f"  📉 최저: {stats['worst_ticker']} ({stats['worst_pnl']:+,.0f}원)",
            "",
            "【AI 판단 정확도】",
            f"  총 판단: {stats['ai_total']}건",
            f"  실행 가능 신호: {stats['ai_executable']}건",
            f"  평균 신뢰도: {stats['ai_avg_conf']:.1f}점",
            "",
            "【리스크 현황】",
            f"  최대 단일 손실: {stats['max_loss']:+,.0f}원",
            f"  손실 한도 대비: {stats['loss_ratio']:.1f}%",
            "",
            "━" * 32,
            f"다음 주 감시 종목: {', '.join(stats['watchlist'])}",
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]

        report = "\n".join(lines)
        self._send_telegram(report)
        self._save_report(report, f"weekly_{week_start}.txt")
        logger.info("주간 리포트 생성 완료: {}~{}", week_start, week_end)
        return report

    # ══════════════════════════════════════════
    # HTML 일일 리포트 (브라우저용)
    # ══════════════════════════════════════════

    def generate_html_daily(self, target_date: date = None) -> Path:
        """HTML 형식 일일 리포트 파일 저장"""
        target = target_date or date.today()
        ds     = target.isoformat()
        stats  = self._daily_trade_stats(ds)
        pnl    = self._daily_pnl(ds)
        ai_log = self._daily_ai_log(ds)

        pnl_color = "#27500A" if pnl["today"] >= 0 else "#A32D2D"
        pnl_icon  = "📈" if pnl["today"] >= 0 else "📉"

        html = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<title>일일 리포트 — {ds}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f5f4f0;color:#2c2c2a;padding:24px}}
h1{{font-size:20px;font-weight:500;margin-bottom:4px}}
.sub{{color:#5f5e5a;font-size:13px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
.card{{background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:16px}}
.label{{font-size:10px;color:#888780;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.val{{font-size:22px;font-weight:500}}
.section{{background:#fff;border-radius:12px;border:1px solid #e0dfd8;padding:20px;margin-bottom:16px}}
.section h2{{font-size:13px;font-weight:500;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse}}
th{{font-size:11px;color:#5f5e5a;padding:8px;border-bottom:1px solid #e0dfd8;text-align:left}}
td{{font-size:12px;padding:8px;border-bottom:1px solid #f1efe8;color:#3d3d3a}}
</style>
</head><body>
<h1>📊 일일 자동매매 리포트</h1>
<p class="sub">{ds} | 실거래 | 생성: {datetime.now().strftime('%H:%M:%S')}</p>

<div class="grid">
  <div class="card">
    <div class="label">오늘 실현손익</div>
    <div class="val" style="color:{pnl_color}">{pnl_icon} {pnl['today']:+,.0f}원</div>
  </div>
  <div class="card">
    <div class="label">총 주문</div>
    <div class="val">{stats['total']}건</div>
  </div>
  <div class="card">
    <div class="label">체결 / 차단</div>
    <div class="val">{stats['filled']} / {stats['blocked']}</div>
  </div>
  <div class="card">
    <div class="label">AI 평균 신뢰도</div>
    <div class="val">{ai_log['avg_conf']:.0f}점</div>
  </div>
</div>

<div class="section">
  <h2>거래 내역 (오늘)</h2>
  <table>
    <thead><tr><th>시각</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th><th>상태</th><th>근거</th></tr></thead>
    <tbody id="trades">
      {''.join(self._trade_rows(ds))}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>리스크 현황</h2>
  <table>
    <thead><tr><th>항목</th><th>현재값</th><th>한도</th><th>상태</th></tr></thead>
    <tbody>
      <tr><td>일일 손실</td><td>{pnl['today']:+,.0f}원</td>
          <td>{RISK_CONFIG['daily_loss_limit']:,.0f}원</td>
          <td>{'⚠️ 경고' if pnl['today'] < RISK_CONFIG['daily_loss_limit']*0.7 else '✅ 정상'}</td></tr>
      <tr><td>체결 건수</td><td>{stats['filled']}건</td><td>—</td><td>✅</td></tr>
    </tbody>
  </table>
</div>
</body></html>"""

        path = REPORT_DIR / f"daily_{ds}.html"
        path.write_text(html, encoding="utf-8")
        logger.info("HTML 일일 리포트: {}", path)
        return path

    # ── DB 조회 헬퍼 ─────────────────────────

    def _daily_trade_stats(self, date_str: str) -> dict:
        try:
            with sqlite3.connect(DB_PATH) as con:
                total   = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=?", (date_str,)
                ).fetchone()[0]
                filled  = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=? "
                    "AND status IN ('FILLED','PAPER_FILLED')", (date_str,)
                ).fetchone()[0]
                blocked = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=? "
                    "AND status='BLOCKED'", (date_str,)
                ).fetchone()[0]
                buy = con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=? "
                    "AND order_type='BUY' AND status IN ('FILLED','PAPER_FILLED')",
                    (date_str,)
                ).fetchone()[0]
                sell= con.execute(
                    "SELECT COUNT(*) FROM orders WHERE DATE(timestamp)=? "
                    "AND order_type='SELL' AND status IN ('FILLED','PAPER_FILLED')",
                    (date_str,)
                ).fetchone()[0]
                reasons = con.execute(
                    "SELECT reason FROM orders WHERE DATE(timestamp)=? "
                    "AND status='BLOCKED' LIMIT 3", (date_str,)
                ).fetchall()
            return {
                "total": total, "filled": filled, "blocked": blocked,
                "buy": buy, "sell": sell,
                "block_reasons": [r[0] for r in reasons if r[0]],
            }
        except Exception:
            return {"total":0,"filled":0,"blocked":0,"buy":0,"sell":0,"block_reasons":[]}

    def _daily_pnl(self, date_str: str) -> dict:
        try:
            with sqlite3.connect(DB_PATH) as con:
                today_pnl = con.execute(
                    "SELECT COALESCE(SUM(CASE WHEN order_type='SELL' THEN qty*price "
                    "ELSE -qty*price END),0) FROM orders "
                    "WHERE DATE(timestamp)=? AND status IN ('FILLED','PAPER_FILLED')",
                    (date_str,)
                ).fetchone()[0]
                total_pnl = con.execute(
                    "SELECT COALESCE(SUM(CASE WHEN order_type='SELL' THEN qty*price "
                    "ELSE -qty*price END),0) FROM orders "
                    "WHERE status IN ('FILLED','PAPER_FILLED')"
                ).fetchone()[0]
            return {"today": today_pnl or 0, "total": total_pnl or 0}
        except Exception:
            return {"today": 0, "total": 0}

    def _daily_ai_log(self, date_str: str) -> dict:
        log_file = LOG_DIR / f"ai_judge_{date_str.replace('-','')}.log"
        buy = sell = hold = confs = 0
        total = 0
        if log_file.exists():
            for line in log_file.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    total += 1
                    action = d.get("action","")
                    if action == "BUY":   buy  += 1
                    elif action == "SELL": sell += 1
                    else:                  hold += 1
                    confs += d.get("confidence", 0)
                except Exception:
                    pass
        return {
            "buy": buy, "sell": sell, "hold": hold,
            "avg_conf": confs / total if total else 0,
        }

    def _weekly_stats(self, start: date, end: date) -> dict:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT ticker, order_type, qty, price FROM orders "
                    "WHERE DATE(timestamp) BETWEEN ? AND ? "
                    "AND status IN ('FILLED','PAPER_FILLED')",
                    (start.isoformat(), end.isoformat())
                ).fetchall()
        except Exception:
            rows = []

        ticker_pnl: dict[str, float] = {}
        total_pnl = 0.0
        for ticker, otype, qty, price in rows:
            val = qty * price * (1 if otype == "SELL" else -1)
            ticker_pnl[ticker] = ticker_pnl.get(ticker, 0) + val
            total_pnl += val

        wins  = [v for v in ticker_pnl.values() if v > 0]
        loses = [v for v in ticker_pnl.values() if v <= 0]
        total = len(ticker_pnl)

        best_t  = max(ticker_pnl, key=ticker_pnl.get) if ticker_pnl else "—"
        worst_t = min(ticker_pnl, key=ticker_pnl.get) if ticker_pnl else "—"

        from config import WATCH_LIST
        return {
            "total_trades":  len(rows),
            "weekly_pnl":    total_pnl,
            "win_trades":    len(wins),
            "lose_trades":   len(loses),
            "win_rate":      len(wins) / total * 100 if total else 0,
            "best_ticker":   best_t,
            "best_pnl":      ticker_pnl.get(best_t, 0),
            "worst_ticker":  worst_t,
            "worst_pnl":     ticker_pnl.get(worst_t, 0),
            "ai_total":      0,
            "ai_executable": 0,
            "ai_avg_conf":   0.0,
            "max_loss":      min(loses) if loses else 0,
            "loss_ratio":    abs(min(loses) / RISK_CONFIG["daily_loss_limit"] * 100) if loses else 0,
            "watchlist":     WATCH_LIST[:5],
        }

    def _trade_rows(self, date_str: str) -> list[str]:
        try:
            with sqlite3.connect(DB_PATH) as con:
                rows = con.execute(
                    "SELECT timestamp, ticker, order_type, qty, price, status, reason "
                    "FROM orders WHERE DATE(timestamp)=? ORDER BY timestamp DESC LIMIT 20",
                    (date_str,)
                ).fetchall()
        except Exception:
            return ["<tr><td colspan='7'>데이터 없음</td></tr>"]

        html_rows = []
        for ts, ticker, otype, qty, price, status, reason in rows:
            t = ts[11:16] if len(ts) > 16 else ts
            tc = "#27500A" if otype == "BUY" else "#A32D2D"
            sc = "#185FA5" if "FILLED" in (status or "") else "#854F0B"
            html_rows.append(
                f"<tr><td>{t}</td><td><b>{ticker}</b></td>"
                f"<td style='color:{tc}'>{otype}</td>"
                f"<td>{qty}주</td><td>{(price or 0):,.0f}원</td>"
                f"<td style='color:{sc}'>{status}</td>"
                f"<td style='font-size:10px;max-width:200px'>{(reason or '')[:40]}</td></tr>"
            )
        return html_rows or ["<tr><td colspan='7' style='text-align:center;color:#888'>오늘 거래 없음</td></tr>"]

    def _check_risk_warnings(self, stats: dict, pnl: dict) -> list[str]:
        warnings = []
        if pnl["today"] < RISK_CONFIG["daily_loss_limit"] * 0.8:
            warnings.append(f"⚠️ 일손실 한도 80% 도달: {pnl['today']:+,.0f}원")
        if stats["blocked"] > stats["filled"]:
            warnings.append(f"⚠️ 차단 주문이 체결보다 많음 ({stats['blocked']}건 차단)")
        return warnings

    # ── 텔레그램 / 파일 저장 ──────────────────

    def _send_telegram(self, text: str) -> None:
        if not self._tg_ok:
            logger.info("[텔레그램 미전송]\n{}", text[:200])
            return
        try:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            requests.post(url, json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "",
            }, timeout=5)
        except Exception as e:
            logger.error("텔레그램 리포트 전송 실패: {}", e)

    def _save_report(self, text: str, filename: str) -> None:
        path = REPORT_DIR / filename
        path.write_text(text, encoding="utf-8")
        logger.debug("리포트 저장: {}", path)
