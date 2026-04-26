"""
core/health_monitor.py — 시스템 헬스체크 + 자동 복구

주기적으로 다음 항목을 감시한다:
  - API 연결 상태
  - DB 접근 가능 여부
  - 메모리 사용량
  - 스캔 주기 지연 여부
  - 일일 손실 한도 접근 경보
  - 텔레그램 발송 실패 연속 횟수

이상 감지 시:
  - 로그 기록
  - 텔레그램 경보
  - 가능한 경우 자동 복구
"""

from __future__ import annotations

import os
import platform
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional

from loguru import logger

from config import DB_PATH, RISK_CONFIG, SCHEDULE_CONFIG, TELEGRAM_CONFIG


def _is_market_hours() -> bool:
    """국내 정규장(09:00~15:30, 평일) 여부."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    o = dtime(*map(int, SCHEDULE_CONFIG["market_open"].split(":")))
    c = dtime(*map(int, SCHEDULE_CONFIG["market_close"].split(":")))
    t = now.time()
    return o <= t <= c


# ── 헬스 상태 ─────────────────────────────────

@dataclass
class HealthStatus:
    timestamp:    str = field(default_factory=lambda: datetime.now().isoformat())
    api_ok:       bool = True
    db_ok:        bool = True
    memory_mb:    float = 0.0
    memory_pct:   float = 0.0
    scan_delay:   float = 0.0     # 초 단위 지연
    daily_pnl:    float = 0.0
    loss_ratio:   float = 0.0     # 손실 한도 대비 비율 (0~1)
    tg_fail_cnt:  int   = 0
    issues:       list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.api_ok and self.db_ok and not self.issues

    @property
    def severity(self) -> str:
        """심각도 반환: OK / WARN / CRITICAL"""
        if not self.db_ok:
            return "CRITICAL"
        if self.loss_ratio >= 0.9 or not self.api_ok:
            return "WARN"
        if self.issues:
            return "WARN"
        return "OK"


# ── 헬스 모니터 ───────────────────────────────

class HealthMonitor:
    """
    시스템 상태를 주기적으로 점검하는 모니터.
    main.py 루프 안에서 또는 별도 스레드에서 호출한다.
    """

    # 경보 임계값
    MEMORY_WARN_PCT    = 80.0   # 메모리 80% 이상 경고
    SCAN_DELAY_WARN    = 60.0   # 스캔 60초 이상 지연 경고
    LOSS_WARN_RATIO    = 0.70   # 손실 한도 70% 도달 경고
    TG_FAIL_WARN       = 5      # 텔레그램 5회 연속 실패 경고

    def __init__(self, kiwoom_api=None, risk_manager=None) -> None:
        self._kw           = kiwoom_api
        self._rm           = risk_manager
        self._last_scan_ts = time.time()
        self._tg_fail_cnt  = 0
        self._prev_status: Optional[HealthStatus] = None

    def check(self) -> HealthStatus:
        """전체 헬스체크를 실행하고 HealthStatus를 반환한다."""
        status = HealthStatus()
        status.daily_pnl = self._rm.get_daily_pnl() if self._rm else 0.0
        limit = RISK_CONFIG["daily_loss_limit"]
        status.loss_ratio = (
            abs(status.daily_pnl) / abs(limit)
            if limit != 0 and status.daily_pnl < 0 else 0.0
        )
        status.tg_fail_cnt = self._tg_fail_cnt

        # 1. DB 접근
        status.db_ok = self._check_db()
        if not status.db_ok:
            status.issues.append("❌ DB 접근 불가")

        # 2. API 연결
        status.api_ok = self._check_api()
        if not status.api_ok:
            status.issues.append("⚠️ 키움 API 연결 끊김")

        # 3. 메모리
        status.memory_mb, status.memory_pct = self._check_memory()
        if status.memory_pct >= self.MEMORY_WARN_PCT:
            status.issues.append(f"⚠️ 메모리 {status.memory_pct:.0f}% 사용 중")

        # 4. 스캔 지연 — 정규장 시간에만 의미 있음 (장외엔 의도적으로 안 도는 거)
        status.scan_delay = time.time() - self._last_scan_ts
        if _is_market_hours() and status.scan_delay > self.SCAN_DELAY_WARN:
            status.issues.append(f"⚠️ 스캔 {status.scan_delay:.0f}초 지연")

        # 5. 손실 한도 경보
        if status.loss_ratio >= self.LOSS_WARN_RATIO:
            status.issues.append(
                f"⚠️ 일손실 {status.loss_ratio:.0%} 도달 ({status.daily_pnl:+,.0f}원)"
            )

        # 6. 텔레그램 연속 실패
        if self._tg_fail_cnt >= self.TG_FAIL_WARN:
            status.issues.append(f"⚠️ 텔레그램 {self._tg_fail_cnt}회 연속 실패")

        # 로깅
        self._log_status(status)

        # 이전 상태와 비교: 새 이슈 발생 시 텔레그램 경보
        if status.issues and (
            self._prev_status is None or
            set(status.issues) != set(self._prev_status.issues)
        ):
            self._send_alert(status)

        self._prev_status = status
        return status

    def ping_scan(self) -> None:
        """스캔 완료 시 호출하여 지연 타이머를 리셋한다."""
        self._last_scan_ts = time.time()

    def record_tg_fail(self) -> None:
        self._tg_fail_cnt += 1

    def record_tg_success(self) -> None:
        self._tg_fail_cnt = 0

    # ── 자동 복구 시도 ────────────────────────

    def try_recover(self, status: HealthStatus) -> bool:
        """
        감지된 이슈에 대해 자동 복구를 시도한다.
        성공 시 True 반환.
        """
        recovered = False

        if not status.api_ok and self._kw:
            logger.warning("API 재연결 시도...")
            try:
                self._kw.login()
                logger.success("API 재연결 성공")
                recovered = True
            except Exception as e:
                logger.error("API 재연결 실패: {}", e)

        if not status.db_ok:
            logger.warning("DB 복구 시도...")
            try:
                # WAL 체크포인트 실행
                with sqlite3.connect(DB_PATH) as con:
                    con.execute("PRAGMA wal_checkpoint(RESTART)")
                logger.success("DB 복구 성공")
                recovered = True
            except Exception as e:
                logger.error("DB 복구 실패: {}", e)

        return recovered

    # ── 내부 체크 ─────────────────────────────

    @staticmethod
    def _check_db() -> bool:
        try:
            with sqlite3.connect(DB_PATH, timeout=3) as con:
                con.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _check_api(self) -> bool:
        if self._kw is None:
            return True   # Mock 모드면 항상 OK
        try:
            return self._kw.get_connection_state()
        except Exception:
            return False

    @staticmethod
    def _check_memory() -> tuple[float, float]:
        try:
            import psutil
            proc   = psutil.Process(os.getpid())
            mb     = proc.memory_info().rss / 1024 / 1024
            pct    = psutil.virtual_memory().percent
            return round(mb, 1), round(pct, 1)
        except ImportError:
            # psutil 없으면 0 반환
            return 0.0, 0.0

    @staticmethod
    def _log_status(status: HealthStatus) -> None:
        sev = status.severity
        msg = (
            f"헬스체크 [{sev}] | "
            f"API:{status.api_ok} DB:{status.db_ok} | "
            f"MEM:{status.memory_pct:.0f}% | "
            f"손실:{status.loss_ratio:.0%} | "
            f"이슈:{len(status.issues)}개"
        )
        if sev == "CRITICAL":
            logger.critical(msg)
        elif sev == "WARN":
            logger.warning(msg)
        else:
            logger.debug(msg)

    @staticmethod
    def _send_alert(status: HealthStatus) -> None:
        """텔레그램 경보 발송"""
        token   = TELEGRAM_CONFIG["bot_token"]
        chat_id = TELEGRAM_CONFIG["chat_id"]
        if not (token and chat_id):
            return
        try:
            import requests
            sev_icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}[status.severity]
            issues   = "\n".join(f"  {iss}" for iss in status.issues)
            text = (
                f"{sev_icon} 시스템 경보 [{status.severity}]\n"
                f"━" * 24 + "\n"
                f"{issues}\n\n"
                f"메모리: {status.memory_mb:.0f}MB ({status.memory_pct:.0f}%)\n"
                f"일일 손익: {status.daily_pnl:+,.0f}원\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=5,
            )
        except Exception as e:
            logger.error("헬스체크 경보 발송 실패: {}", e)

    # ── 시스템 정보 출력 ──────────────────────

    @staticmethod
    def system_info() -> dict:
        """시스템 정보 딕셔너리 반환"""
        info = {
            "os":      platform.system(),
            "python":  platform.python_version(),
            "pid":     os.getpid(),
            "cwd":     os.getcwd(),
        }
        try:
            import psutil
            info["cpu_pct"]  = psutil.cpu_percent(interval=1)
            info["mem_pct"]  = psutil.virtual_memory().percent
            info["disk_pct"] = psutil.disk_usage("/").percent
        except ImportError:
            pass
        return info
