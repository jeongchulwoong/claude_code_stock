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
from datetime import datetime, time as dtime
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


def _return_code_ok(body: dict) -> bool:
    return str(body.get("return_code", "")).strip() in ("0", "0000")


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
    if not _return_code_ok(body):
        raise RuntimeError(f"토큰 발급 실패: {body.get('return_msg')}")
    token = body.get("token") or body.get("access_token")
    if not token:
        raise RuntimeError("토큰 발급 실패: 응답에 token/access_token 없음")
    return token


class KiwoomWebSocket:
    """
    키움 WebSocket 실시간 시세 수신 클라이언트.
    on_tick 콜백으로 체결 데이터를 전달한다.
    수신된 가격은 price_cache에도 기록되어 main_v2.py와 공유된다.
    """

    def __init__(
        self,
        tickers: list[str],
        on_tick: Callable[[dict], None] | None = None,
    ) -> None:
        # .KS/.KQ 접미사 제거
        self._tickers = [t.replace(".KS","").replace(".KQ","") for t in tickers]
        self._user_callback = on_tick
        self._token: str = ""
        self._ws = None
        self._running = False

    @staticmethod
    def _is_market_open_now() -> bool:
        """국내 주식시장 정규장(09:00~15:30, 평일) 여부."""
        now = datetime.now()
        if now.weekday() >= 5:   # 토(5)/일(6)
            return False
        t = now.time()
        return t >= dtime(9, 0) and t <= dtime(15, 30)

    @staticmethod
    def _is_ws_standby_now() -> bool:
        """08:20부터 WebSocket 접속을 반복 시도해 개장 직후 공백을 줄인다."""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        return dtime(8, 20) <= t <= dtime(15, 40)

    def _retry_delay(self) -> int:
        if self._is_market_open_now():
            return 10
        if self._is_ws_standby_now():
            return 30
        return 300

    async def _wait_until_open_or(self, max_sleep: int = 300) -> None:
        """장 마감 시간엔 길게 잠들고, 정규장 임박 시엔 짧게 깨운다."""
        if self._is_market_open_now():
            await asyncio.sleep(15)
            return
        if self._is_ws_standby_now():
            await asyncio.sleep(min(max_sleep, 30))
            return
        # 시간외(16:00~18:00)도 틱은 거의 없음 → 5분 단위로 폴링
        logger.debug("[WS] 장외 — {}초 대기 후 재시도", max_sleep)
        await asyncio.sleep(max_sleep)

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("pip install websockets 필요")
            return

        self._running = True
        ws_fail_count = 0
        last_token_ts = 0.0   # 토큰 재사용 (1시간 유효)

        while self._running:
            # WebSocket 3회 연속 실패 시 REST 폴링으로 전환
            if ws_fail_count >= 3:
                logger.warning("WebSocket 연결 불가 — REST API 폴링 모드로 전환 후 주기적으로 WS 재시도")
                if self._is_market_open_now():
                    await self._rest_polling_loop(max_cycles=60)
                else:
                    await asyncio.sleep(self._retry_delay())
                ws_fail_count = 0
                continue

            try:
                if not self._is_ws_standby_now():
                    logger.info("[WS] 접속 대기 시간 외 — 5분 후 재확인")
                    await asyncio.sleep(300)
                    continue
                # 토큰 캐시 — 1시간 안 지났으면 재사용
                now_ts = datetime.now().timestamp()
                if not self._token or now_ts - last_token_ts > 3500:
                    logger.info("[WS] 토큰 발급")
                    self._token = get_token()
                    last_token_ts = now_ts
                logger.info("[WS] 연결 시도 (장중={})", self._is_market_open_now())
                # 연결/로그인/등록 단계만 15초 타임아웃 — recv_loop 는 무제한 대기
                async with websockets.connect(
                    WS_URL, ssl=True, open_timeout=10,
                    ping_interval=20, ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    async with asyncio.timeout(15):
                        await self._login(ws)
                        await self._register(ws, self._tickers)
                    ws_fail_count = 0  # 연결 성공 시 리셋
                    await self._recv_loop(ws)   # 서버가 닫을 때까지 대기
                # ── _recv_loop 가 정상 종료 = 서버가 idle 끊음 ───
                if self._is_market_open_now():
                    logger.info("[WS] 서버가 연결 닫음(정규장 idle) — 즉시 재접속")
                    await asyncio.sleep(2)
                else:
                    logger.info("[WS] 서버 연결 닫음(장외) — 5분 후 재접속")
                    await self._wait_until_open_or(300)
            except TimeoutError:
                ws_fail_count += 1
                logger.warning("[WS] 타임아웃 ({}/3) — 30초 후 재시도", ws_fail_count)
                await asyncio.sleep(self._retry_delay())
            except RuntimeError as e:
                ws_fail_count += 1
                logger.error("[WS] 연결 오류 ({}/3): {} — 30초 후 재시도", ws_fail_count, e)
                await asyncio.sleep(self._retry_delay())
            except Exception as e:
                logger.warning("[WS] 끊김: {} — {}",
                               e, "10초 후 재연결" if self._is_market_open_now() else "5분 후 재연결")
                await self._wait_until_open_or(self._retry_delay())

    async def _rest_polling_loop(self, max_cycles: int = 60) -> None:
        """WebSocket 불가 시 키움 REST API로 폴링하고, 일정 시간 후 WS 복구를 재시도한다."""
        from config import API_CONFIG
        appkey    = API_CONFIG["appkey"]
        secretkey = API_CONFIG["secretkey"]
        base_url  = "https://api.kiwoom.com"

        def _get_token_once() -> str:
            r = requests.post(
                f"{base_url}/oauth2/token",
                json={"grant_type": "client_credentials",
                      "appkey": appkey, "secretkey": secretkey},
                timeout=5,
            )
            body = r.json()
            if not _return_code_ok(body):
                raise RuntimeError(body.get("return_msg"))
            token = body.get("token") or body.get("access_token")
            if not token:
                raise RuntimeError("REST 폴링 토큰 응답에 token/access_token 없음")
            return token

        def _fetch_price(code: str, token: str) -> dict | None:
            try:
                r = requests.post(
                    f"{base_url}/api/dostk/stkinfo",
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "authorization": f"Bearer {token}",
                        "cont-yn": "N",
                        "next-key": "",
                        "api-id": "ka10001",
                    },
                    json={"stk_cd": code},
                    timeout=5,
                )
                out = r.json()
                if not _return_code_ok(out):
                    return None
                def _num(v, cast=int):
                    if v is None:
                        return cast(0)
                    try:
                        return cast(str(v).replace("+", "").replace(",", "").strip() or 0)
                    except Exception:
                        return cast(0)
                price = abs(_num(out.get("cur_prc")))
                change_pct = _num(out.get("flu_rt") or out.get("chg_rt"), float)
                return {
                    "ticker":     code,
                    "time":       datetime.now().strftime("%H%M%S"),
                    "price":      price,
                    "change_pct": change_pct,
                    "volume":     _num(out.get("trde_qty")),
                }
            except Exception:
                return None

        token = ""
        token_ts = 0.0
        cycles = 0
        while self._running:
            try:
                # 토큰 1시간마다 갱신
                if datetime.now().timestamp() - token_ts > 3500:
                    token = _get_token_once()
                    token_ts = datetime.now().timestamp()
                    logger.info("[REST폴링] 토큰 갱신 완료")

                for code in self._tickers:
                    tick = _fetch_price(code, token)
                    if tick:
                        self._on_tick(tick)
                    await asyncio.sleep(0.2)  # API 호출 간격

                await asyncio.sleep(5)
                cycles += 1
                if cycles >= max_cycles:
                    logger.info("[REST폴링] WS 복구 재시도를 위해 폴링 일시 종료")
                    return
            except Exception as e:
                logger.warning("[REST폴링] 오류: {} — 10초 후 재시도", e)
                await asyncio.sleep(10)

    async def _login(self, ws) -> None:
        await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if not _return_code_ok(resp):
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
        if not _return_code_ok(resp):
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

    def _on_tick(self, tick: dict) -> None:
        """수신된 틱 처리: price_cache 기록 → 대시보드 전송 → 콘솔 출력 → 사용자 콜백"""
        # 1) price_cache에 기록 (main_v2.py와 실시간 공유)
        try:
            from core.price_cache import get_cache
            get_cache().update(tick)
        except Exception:
            pass

        # 2) 대시보드 전송
        try:
            requests.post("http://127.0.0.1:5001/api/tick", json=tick, timeout=0.5)
        except Exception:
            pass

        # 3) 콘솔 출력
        t = tick
        print(f"[{t['time']}] {t['ticker']} "
              f"현재가:{t['price']:,}  등락:{t['change_pct']}%  "
              f"거래량:{t['volume']:,}")

        # 4) 사용자 콜백
        if self._user_callback:
            try:
                self._user_callback(tick)
            except Exception:
                pass

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
