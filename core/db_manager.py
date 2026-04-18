"""
core/db_manager.py — DB 마이그레이션 + 유지보수

기능:
  - 스키마 버전 관리 (마이그레이션)
  - 자동 인덱스 생성
  - 오래된 데이터 정리 (30일 이상)
  - DB 통계 조회
  - WAL 체크포인트 + VACUUM
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from loguru import logger

from config import DB_PATH

# ── 마이그레이션 정의 ─────────────────────────
# (version, sql) 형식. 한 번 실행된 버전은 재실행하지 않는다.

MIGRATIONS = [
    (1, """
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES (1);
    """),
    (2, """
        CREATE INDEX IF NOT EXISTS idx_orders_ticker     ON orders(ticker);
        CREATE INDEX IF NOT EXISTS idx_orders_timestamp  ON orders(timestamp);
        CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status);
    """),
    (3, """
        CREATE TABLE IF NOT EXISTS daily_pnl_cache (
            cache_date TEXT PRIMARY KEY,
            pnl        REAL,
            trade_count INTEGER
        );
    """),
    (4, """
        CREATE INDEX IF NOT EXISTS idx_signals_strategy ON strategy_signals(strategy);
        CREATE INDEX IF NOT EXISTS idx_signals_ticker   ON strategy_signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_screener_date    ON screener_results(run_date);
    """),
    (5, """
        ALTER TABLE orders ADD COLUMN strategy TEXT DEFAULT '';
    """),
]


class DBManager:
    """
    SQLite DB 스키마 마이그레이션과 유지보수를 담당한다.
    앱 시작 시 한 번 호출하면 된다.
    """

    def __init__(self, db_path: Path = None) -> None:
        self._path = db_path or DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── 마이그레이션 ──────────────────────────

    def migrate(self) -> None:
        """최신 스키마로 DB를 업그레이드한다."""
        with sqlite3.connect(self._path) as con:
            # 버전 테이블 생성
            con.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            current = self._get_version(con)

        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            logger.info("DB 마이그레이션: v{} → v{}", current, version)
            try:
                with sqlite3.connect(self._path) as con:
                    for stmt in sql.strip().split(";"):
                        stmt = stmt.strip()
                        if stmt:
                            try:
                                con.execute(stmt)
                            except sqlite3.OperationalError as e:
                                # "column already exists" 등 무시 가능한 오류
                                if "already exists" in str(e) or "duplicate column" in str(e).lower():
                                    logger.debug("마이그레이션 skip (이미 존재): {}", e)
                                else:
                                    raise
                    con.execute(
                        "INSERT OR REPLACE INTO schema_version VALUES (?)", (version,)
                    )
                current = version
                logger.success("마이그레이션 완료: v{}", version)
            except Exception as e:
                logger.error("마이그레이션 실패 v{}: {}", version, e)

    # ── 유지보수 ─────────────────────────────

    def cleanup(self, retain_days: int = 90) -> dict[str, int]:
        """오래된 데이터를 정리하고 삭제 건수를 반환한다."""
        cutoff = (date.today() - timedelta(days=retain_days)).isoformat()
        deleted = {}
        tables = [
            ("strategy_signals", "DATE(signal_date)"),
            ("screener_results", "run_date"),
            ("alert_events",     "DATE(fired_at)"),
        ]
        with sqlite3.connect(self._path) as con:
            for table, col in tables:
                try:
                    cur = con.execute(
                        f"DELETE FROM {table} WHERE {col} < ?", (cutoff,)
                    )
                    cnt = cur.rowcount
                    if cnt > 0:
                        deleted[table] = cnt
                        logger.info("정리: {} {}건 삭제 ({}일 이전)", table, cnt, retain_days)
                except sqlite3.OperationalError:
                    pass  # 테이블 없으면 skip

        return deleted

    def vacuum(self) -> None:
        """DB 파일 크기 최적화"""
        try:
            with sqlite3.connect(self._path) as con:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
            logger.info("DB VACUUM 완료: {}", self._path)
        except Exception as e:
            logger.error("VACUUM 실패: {}", e)

    def stats(self) -> dict[str, int]:
        """테이블별 행 수 반환"""
        tables = [
            "orders", "strategy_signals", "strategy_trades",
            "screener_results", "alert_rules", "alert_events",
            "portfolio_snapshots", "foreign_signals",
        ]
        result = {}
        with sqlite3.connect(self._path) as con:
            for table in tables:
                try:
                    cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    result[table] = cnt
                except sqlite3.OperationalError:
                    result[table] = 0
        return result

    def print_stats(self) -> None:
        """DB 통계를 터미널에 출력한다."""
        s = self.stats()
        db_size = self._path.stat().st_size / 1024 if self._path.exists() else 0
        print(f"\n  📁 DB 통계: {self._path.name} ({db_size:.1f} KB)")
        print("  " + "─"*35)
        for table, cnt in s.items():
            print(f"  {table:<28} {cnt:>6}행")
        print()

    # ── 내부 ──────────────────────────────────

    @staticmethod
    def _get_version(con: sqlite3.Connection) -> int:
        try:
            row = con.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0


# ── 편의 함수 ─────────────────────────────────

def init_db() -> DBManager:
    """앱 시작 시 호출: 마이그레이션 + 통계 출력"""
    mgr = DBManager()
    mgr.migrate()
    mgr.print_stats()
    return mgr
