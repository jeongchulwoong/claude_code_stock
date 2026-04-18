"""
core/scheduler.py — APScheduler 기반 정교한 작업 스케줄러

등록 작업:
  - 장 중 매 5분:   시장 스캔 + AI 판단
  - 장 중 매 1분:   포지션 손절·익절 체크
  - 09:00 장 시작:  일일 초기화 + 스크리너 실행
  - 15:30 장 마감:  일일 리포트 + 포트폴리오 스냅샷
  - 15:35 장 마감:  주간 리포트 (금요일만)
  - 매 시간:        헬스체크
  - 매 30분:        해외주식 스캔 (미국 장 중)

실행:
    scheduler = TradingScheduler(...)
    scheduler.start()   # 블로킹
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("APScheduler 미설치 — pip install apscheduler")


class TradingScheduler:
    """
    자동매매 작업 스케줄러.
    APScheduler 없으면 간단한 시간 루프로 대체한다.
    """

    def __init__(
        self,
        scan_fn:          Optional[Callable] = None,    # 5분 스캔 함수
        position_fn:      Optional[Callable] = None,    # 1분 포지션 체크
        screener_fn:      Optional[Callable] = None,    # 스크리너
        daily_report_fn:  Optional[Callable] = None,    # 일일 리포트
        weekly_report_fn: Optional[Callable] = None,    # 주간 리포트
        health_fn:        Optional[Callable] = None,    # 헬스체크
        foreign_scan_fn:  Optional[Callable] = None,    # 해외주식 스캔
        scan_interval_min: int = 5,
    ) -> None:
        self._jobs = {
            "scan":          scan_fn,
            "position":      position_fn,
            "screener":      screener_fn,
            "daily_report":  daily_report_fn,
            "weekly_report": weekly_report_fn,
            "health":        health_fn,
            "foreign_scan":  foreign_scan_fn,
        }
        self._interval = scan_interval_min
        self._scheduler = None

    def start(self, blocking: bool = True) -> None:
        """스케줄러를 시작한다."""
        if not HAS_APSCHEDULER:
            logger.warning("APScheduler 없음 — 단순 루프 모드로 실행")
            self._simple_loop()
            return

        self._scheduler = (
            BlockingScheduler(timezone="Asia/Seoul") if blocking
            else BackgroundScheduler(timezone="Asia/Seoul")
        )

        self._register_jobs()
        logger.info("스케줄러 시작 (APScheduler)")
        logger.info("등록 작업:")
        for job in self._scheduler.get_jobs():
            logger.info("  - {} | {}", job.name, job.next_run_time)

        self._scheduler.start()

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("스케줄러 중단")

    # ── 작업 등록 ─────────────────────────────

    def _register_jobs(self) -> None:
        s = self._scheduler

        # ── 장 중 스캔 (매 N분, 09:01~15:29) ──
        if self._jobs["scan"]:
            s.add_job(
                self._safe(self._jobs["scan"]),
                CronTrigger(
                    minute=f"*/{self._interval}",
                    hour="9-15",
                    day_of_week="mon-fri",
                    second="0",
                ),
                name="장중 스캔",
                max_instances=1,
                misfire_grace_time=60,
            )

        # ── 포지션 체크 (매 1분, 09:00~15:30) ──
        if self._jobs["position"]:
            s.add_job(
                self._safe(self._jobs["position"]),
                CronTrigger(
                    minute="*", hour="9-15",
                    day_of_week="mon-fri", second="30",
                ),
                name="포지션 손절·익절",
                max_instances=1,
                misfire_grace_time=30,
            )

        # ── 장 시작: 스크리너 (09:05) ──
        if self._jobs["screener"]:
            s.add_job(
                self._safe(self._jobs["screener"]),
                CronTrigger(hour="9", minute="5", day_of_week="mon-fri"),
                name="종목 스크리너",
                max_instances=1,
            )

        # ── 장 마감: 일일 리포트 (15:32) ──
        if self._jobs["daily_report"]:
            s.add_job(
                self._safe(self._jobs["daily_report"]),
                CronTrigger(hour="15", minute="32", day_of_week="mon-fri"),
                name="일일 리포트",
                max_instances=1,
            )

        # ── 장 마감: 주간 리포트 (금 15:40) ──
        if self._jobs["weekly_report"]:
            s.add_job(
                self._safe(self._jobs["weekly_report"]),
                CronTrigger(hour="15", minute="40", day_of_week="fri"),
                name="주간 리포트",
                max_instances=1,
            )

        # ── 헬스체크 (매 시간) ──
        if self._jobs["health"]:
            s.add_job(
                self._safe(self._jobs["health"]),
                IntervalTrigger(hours=1),
                name="헬스체크",
                max_instances=1,
            )

        # ── 해외주식 (30분, 미국 장 중 23:30~06:00 KST) ──
        if self._jobs["foreign_scan"]:
            s.add_job(
                self._safe(self._jobs["foreign_scan"]),
                CronTrigger(
                    minute="0,30",
                    hour="23,0,1,2,3,4,5",
                    day_of_week="mon-fri,sun",
                ),
                name="해외주식 스캔",
                max_instances=1,
                misfire_grace_time=120,
            )

    # ── 안전 래퍼 ─────────────────────────────

    @staticmethod
    def _safe(fn: Callable) -> Callable:
        """예외를 잡아 스케줄러가 멈추지 않도록 보호하는 래퍼"""
        def wrapper(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.error("스케줄 작업 예외: {} | {}", fn.__name__ if hasattr(fn,"__name__") else "?", e)
        return wrapper

    # ── APScheduler 없을 때 단순 루프 ──────────

    def _simple_loop(self) -> None:
        """APScheduler 없는 환경에서의 시간 기반 루프"""
        import time
        from datetime import time as dtime
        logger.info("단순 시간 루프 시작")

        last_scan   = 0
        last_health = 0

        while True:
            now = datetime.now()
            t   = now.time()

            # 장 중 여부
            market_open  = dtime(9, 0)
            market_close = dtime(15, 30)
            in_market    = market_open <= t <= market_close and now.weekday() < 5

            # 스캔
            if in_market and time.time() - last_scan >= self._interval * 60:
                if self._jobs["scan"]:
                    self._safe(self._jobs["scan"])()
                if self._jobs["position"]:
                    self._safe(self._jobs["position"])()
                last_scan = time.time()

            # 헬스체크 (1시간)
            if time.time() - last_health >= 3600:
                if self._jobs["health"]:
                    self._safe(self._jobs["health"])()
                last_health = time.time()

            # 장 마감 리포트 (15:31~15:33)
            if dtime(15, 31) <= t <= dtime(15, 33) and now.weekday() < 5:
                if self._jobs["daily_report"]:
                    self._safe(self._jobs["daily_report"])()
                if now.weekday() == 4 and self._jobs["weekly_report"]:
                    self._safe(self._jobs["weekly_report"])()
                time.sleep(120)   # 리포트 중복 방지

            time.sleep(30)


# ── 편의 팩토리 함수 ──────────────────────────

def build_scheduler(
    scan_fn=None,
    position_fn=None,
    screener_fn=None,
    daily_report_fn=None,
    weekly_report_fn=None,
    health_fn=None,
    foreign_scan_fn=None,
    scan_interval_min: int = 5,
    blocking: bool = False,
) -> TradingScheduler:
    """TradingScheduler 인스턴스를 생성하고 바로 시작한다."""
    scheduler = TradingScheduler(
        scan_fn=scan_fn,
        position_fn=position_fn,
        screener_fn=screener_fn,
        daily_report_fn=daily_report_fn,
        weekly_report_fn=weekly_report_fn,
        health_fn=health_fn,
        foreign_scan_fn=foreign_scan_fn,
        scan_interval_min=scan_interval_min,
    )
    if blocking:
        scheduler.start(blocking=True)
    else:
        scheduler.start(blocking=False)
    return scheduler
