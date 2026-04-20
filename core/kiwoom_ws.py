"""
core/kiwoom_ws.py — 키움 WebSocket 실시간 시세 클라이언트

사용법:
    python core/kiwoom_ws.py                      # 기본 감시 종목
    python core/kiwoom_ws.py --tickers 005930 000660
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import API_CONFIG

WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"

# 실시간 타입 코드
RT_TICK    = "0B"   # 주식체결
RT_HOGA    = "0A"   # 주식호가잔량
RT_ORDERBOOK = "0C" # 주식우선호가

# FID 매핑 (0B 체결 기준)
FID_MAP = {
    "20": "체결시간",
    "10": "현재가",
    "11": "전일대비",
    "12": "등락률",
    "13": "누적거래량",
    "14": "누적거래대금",
    "16": "시가",
    "17": "고가",
    "18": "저가",
    "25": "전일대비구분",
}


def get_token() -> str:
    appkey    = API_CONFIG["appkey"]
    secretkey = API_CONFIG["secretkey"]
    r = requests.post(
        "https://api.kiwoom.com/oauth2/token",
        json={"grant_type": "client_credentials",
              "appkey": appkey, "secretkey": secretkey},
        timeout=5,
    )
    body = r.json()
    if body.get("return_code") != 0:
        raise RuntimeError(f"토큰 발급 실패: {body.get('return_msg')}")
    return body["token"]


class KiwoomWebSocket:
    """
    키움 WebSocket 실시간 시세 수신 클라이언트.
    on_tick 콜백으로 체결 데이터를 전달한다.
    """

    def __init__(
        self,
        tickers: list[str],
        on_tick: Callable[[dict], None] | None = None,
    ) -> None:
        # .KS/.KQ 접미사 제거
        self._tickers = [t.replace(".KS","").replace(".KQ","") for t in tickers]
        self._on_tick = on_tick or self._default_print
        self._token: str = ""
        self._ws = None
        self._running = False

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("pip install websockets 필요")
            return

        self._running = True

        while self._running:
            try:
                logger.info("토큰 발급 중...")
                self._token = get_token()
                logger.info("토큰 발급 성공")
                logger.info("WebSocket 연결 시도: {}", WS_URL)
                async with asyncio.timeout(15):
                    async with websockets.connect(
                        WS_URL, ssl=True, open_timeout=10,
                        ping_interval=20, ping_timeout=10,
                    ) as ws:
                        self._ws = ws
                        await self._login(ws)
                        await self._register(ws, self._tickers)
                        await self._recv_loop(ws)
            except TimeoutError:
                logger.warning("WebSocket 연결 타임아웃 (서버 응답 없음) — 30초 후 재연결")
                await asyncio.sleep(30)
            except RuntimeError as e:
                logger.error("연결 오류: {} — 30초 후 재연결", e)
                await asyncio.sleep(30)
            except Exception as e:
                logger.warning("WebSocket 연결 끊김: {} — 10초 후 재연결", e)
                await asyncio.sleep(10)

    async def _login(self, ws) -> None:
        await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if resp.get("return_code") != 0:
            raise RuntimeError(f"WS 로그인 실패: {resp.get('return_msg')}")
        logger.info("WebSocket 로그인 성공")

    async def _register(self, ws, tickers: list[str]) -> None:
        await ws.send(json.dumps({
            "trnm":    "REG",
            "grp_no":  "1",
            "refresh": "1",
            "data": [{"item": tickers, "type": [RT_TICK]}],
        }))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if resp.get("return_code") != 0:
            raise RuntimeError(f"실시간 등록 실패: {resp.get('return_msg')}")
        logger.info("실시간 등록 완료: {} 종목", len(tickers))

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
                trnm = msg.get("trnm", "")

                if trnm == "PING":
                    await ws.send(json.dumps({"trnm": "PONG"}))
                    continue

                if trnm == "REAL":
                    self._handle_real(msg)

            except Exception as e:
                logger.error("수신 처리 오류: {}", e)

    def _handle_real(self, msg: dict) -> None:
        data_list = msg.get("data", [])
        for item in data_list:
            ticker = item.get("item_no", "")
            values = item.get("values", {})
            tick = {
                "ticker":    ticker,
                "time":      values.get("20", ""),
                "price":     int(values.get("10", "0").replace(",","").lstrip("+-") or 0),
                "change":    values.get("11", "0"),
                "change_pct": values.get("12", "0"),
                "volume":    int(values.get("13", "0").replace(",","") or 0),
                "high":      int(values.get("17", "0").replace(",","") or 0),
                "low":       int(values.get("18", "0").replace(",","") or 0),
                "open":      int(values.get("16", "0").replace(",","") or 0),
                "ts":        datetime.now().isoformat(),
            }
            self._on_tick(tick)

    @staticmethod
    def _default_print(tick: dict) -> None:
        t = tick
        print(f"[{t['time']}] {t['ticker']} "
              f"현재가:{t['price']:,}  등락:{t['change_pct']}%  "
              f"거래량:{t['volume']:,}")

    def stop(self) -> None:
        self._running = False


def main():
    from config import WATCH_LIST

    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=None)
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        # WATCH_LIST 이름 → 티커 변환, 국내 종목만 (KS/KQ)
        from stock_universe import get_ticker
        tickers = []
        for name in WATCH_LIST:
            t = get_ticker(name)
            if t and (t.endswith(".KS") or t.endswith(".KQ")):
                tickers.append(t.replace(".KS","").replace(".KQ",""))

    logger.info("실시간 시세 수신 시작 | 종목: {} ({}개)", tickers, len(tickers))
    logger.info("Ctrl+C 로 종료")

    client = KiwoomWebSocket(tickers=tickers)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        logger.info("종료")


if __name__ == "__main__":
    main()
