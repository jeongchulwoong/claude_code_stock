"""
Persist Kiwoom WebSocket tick/order-book events for offline ML.

Safety boundaries:
  - market-data collection only
  - no broker/order/risk imports
  - stores raw WebSocket REAL payloads in SQLite for later feature engineering
  - does not enable any live ML gate

The script intentionally preserves raw ``values`` JSON because Kiwoom FID names
can vary across realtime types. Downstream feature builders can decode OFI,
queue imbalance, microprice, signed volume, and spread after enough samples have
been collected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.kiwoom_ws import (  # noqa: E402
    RT_HOGA,
    RT_ORDERBOOK,
    RT_TICK,
    WS_URL,
    _return_code_ok,
    get_token,
)
from scripts.collect_kiwoom_minute_bars import resolve_tickers  # noqa: E402


KST_TZ = "Asia/Seoul"
SOURCE = "kiwoom_ws"
RT_HOGA_DEPTH = "0D"
DEFAULT_WATCH_LIST = "domestic"
# domestic universe = 173 tickers (config.get_domestic_*). 200 floor 로 buffer 확보.
# ML 학습 dataset 의 169 종목 전체 커버 → forward-train 분포 갭 해소가 목표.
DEFAULT_MAX_TICKERS = 200
REGISTER_CHUNK_SIZE = 100
REAL_TYPE_ALIASES: dict[str, str] = {
    "tick": RT_TICK,
    "trade": RT_TICK,
    "0b": RT_TICK,
    "hoga": RT_HOGA,
    "order_depth": RT_HOGA,
    "0a": RT_HOGA,
    "orderbook": RT_ORDERBOOK,
    "best_orderbook": RT_ORDERBOOK,
    "0c": RT_ORDERBOOK,
    "hoga_depth": RT_HOGA_DEPTH,
    "orderbook_depth": RT_HOGA_DEPTH,
    "0d": RT_HOGA_DEPTH,
}


def parse_real_types(raw: str | None) -> list[str]:
    """Return Kiwoom realtime type codes, preserving input order."""
    text = str(raw or "tick,hoga,orderbook").strip()
    out: list[str] = []
    for part in text.split(","):
        key = part.strip().lower()
        if not key:
            continue
        code = REAL_TYPE_ALIASES.get(key, key.upper())
        if code not in {RT_TICK, RT_HOGA, RT_ORDERBOOK, RT_HOGA_DEPTH}:
            raise ValueError(f"unsupported realtime type: {part}")
        if code not in out:
            out.append(code)
    return out or [RT_TICK, RT_HOGA, RT_ORDERBOOK]


def build_register_payload(tickers: list[str], real_types: list[str]) -> dict[str, Any]:
    codes = [str(t).replace(".KS", "").replace(".KQ", "").strip() for t in tickers if str(t).strip()]
    chunks = [codes[i:i + REGISTER_CHUNK_SIZE] for i in range(0, len(codes), REGISTER_CHUNK_SIZE)]
    return {
        "trnm": "REG",
        "grp_no": "1",
        "refresh": "1",
        "data": [{"item": chunk, "type": real_types} for chunk in chunks],
    }


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS microstructure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            received_at TEXT NOT NULL,
            event_time TEXT NOT NULL,
            real_type TEXT NOT NULL,
            values_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_microstructure_events_lookup
        ON microstructure_events(received_at, ticker, real_type)
        """
    )


def _now_kst() -> str:
    return datetime.now(ZoneInfo(KST_TZ)).isoformat(timespec="microseconds")


def store_real_message(conn: sqlite3.Connection, msg: dict[str, Any], *, received_at: str | None = None) -> int:
    """Store one Kiwoom REAL message and return inserted row count."""
    if msg.get("trnm") != "REAL":
        return 0
    stamp = received_at or _now_kst()
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for item in msg.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        ticker = str(item.get("item_no") or item.get("item") or "").strip()
        real_type = str(item.get("type") or item.get("real_type") or item.get("name") or "").strip()
        event_time = str(values.get("20") or values.get("time") or "").strip()
        if not ticker:
            continue
        rows.append(
            (
                ticker,
                stamp,
                event_time,
                real_type,
                json.dumps(values, ensure_ascii=False, sort_keys=True),
                json.dumps(item, ensure_ascii=False, sort_keys=True),
                SOURCE,
                KST_TZ,
            )
        )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO microstructure_events (
            ticker, received_at, event_time, real_type, values_json, raw_json, source, timezone
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def collect_events(
    *,
    tickers: list[str],
    real_types: list[str],
    db_path: Path,
    report_path: Path,
    max_events: int,
    max_seconds: float,
    flush_every: int,
) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install websockets") from exc

    token = get_token()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    register_payload = build_register_payload(tickers, real_types)
    started_at = _now_kst()
    inserted_total = 0
    received_messages = 0
    start_monotonic = time.monotonic()

    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        async with websockets.connect(
            WS_URL,
            ssl=True,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if not _return_code_ok(login_resp):
                raise RuntimeError(f"kiwoom websocket login failed: {login_resp.get('return_msg')}")

            await ws.send(json.dumps(register_payload))
            reg_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if not _return_code_ok(reg_resp):
                raise RuntimeError(f"kiwoom realtime register failed: {reg_resp.get('return_msg')}")

            async for raw in ws:
                if max_seconds > 0 and (time.monotonic() - start_monotonic) >= max_seconds:
                    break
                msg = json.loads(raw)
                if msg.get("trnm") == "PING":
                    await ws.send(json.dumps({"trnm": "PONG"}))
                    continue
                if msg.get("trnm") != "REAL":
                    continue
                received_messages += 1
                inserted_total += store_real_message(conn, msg)
                if flush_every > 0 and inserted_total % flush_every == 0:
                    conn.commit()
                    write_report(
                        report_path,
                        {
                            "mode": "collecting",
                            "started_at": started_at,
                            "updated_at": _now_kst(),
                            "tickers": tickers,
                            "ticker_count": len(tickers),
                            "real_types": real_types,
                            "messages_received": received_messages,
                            "rows_inserted": inserted_total,
                            "db": str(db_path),
                            "source": SOURCE,
                            "timezone": KST_TZ,
                            "notes": ["market-data only", "raw values_json retained", "does not enable live gate"],
                        },
                    )
                if max_events > 0 and inserted_total >= max_events:
                    break
        conn.commit()

    write_report(
        report_path,
        {
            "mode": "collect",
            "started_at": started_at,
            "finished_at": _now_kst(),
            "tickers": tickers,
            "ticker_count": len(tickers),
            "real_types": real_types,
            "messages_received": received_messages,
            "rows_inserted": inserted_total,
            "db": str(db_path),
            "source": SOURCE,
            "timezone": KST_TZ,
            "notes": ["market-data only", "raw values_json retained", "does not enable live gate"],
        },
    )
    return inserted_total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persist Kiwoom realtime microstructure events for offline ML.")
    parser.add_argument("--tickers", default=None, help="comma-separated names/tickers/codes")
    parser.add_argument("--watch-list", choices=("priority", "watch", "domestic"), default=DEFAULT_WATCH_LIST)
    parser.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    parser.add_argument("--types", default="tick,hoga,orderbook,hoga_depth")
    parser.add_argument("--db", default="db/kiwoom_microstructure_events.db")
    parser.add_argument("--report", default="reports/ml/kiwoom_microstructure_collect_report.json")
    parser.add_argument("--max-events", type=int, default=0, help="0 means run until max-seconds or disconnect")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="0 means run until max-events or disconnect")
    parser.add_argument("--flush-every", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    tickers = resolve_tickers(args.tickers, args.watch_list, max_tickers=int(args.max_tickers))
    real_types = parse_real_types(args.types)
    report_path = Path(args.report)
    if not tickers:
        write_report(report_path, {"error": "no_domestic_tickers", "rows_inserted": 0})
        print("[error] no domestic tickers supplied", file=sys.stderr)
        return 2

    payload = {
        "mode": "dry_run" if args.dry_run else "collect",
        "tickers": tickers,
        "ticker_count": len(tickers),
        "real_types": real_types,
        "db": str(args.db),
        "source": SOURCE,
        "timezone": KST_TZ,
        "notes": ["market-data only", "does not enable live gate"],
    }
    if args.dry_run:
        write_report(report_path, payload | {"rows_inserted": 0})
        print(f"dry-run tickers={len(tickers)} real_types={','.join(real_types)} report={report_path}")
        return 0

    inserted = asyncio.run(
        collect_events(
            tickers=tickers,
            real_types=real_types,
            db_path=Path(args.db),
            report_path=report_path,
            max_events=int(args.max_events),
            max_seconds=float(args.max_seconds),
            flush_every=int(args.flush_every),
        )
    )
    print(f"inserted {inserted} microstructure rows -> {args.db}", flush=True)
    return 0 if inserted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
