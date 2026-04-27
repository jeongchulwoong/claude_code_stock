"""
core/kiwoom_api.py — 키움 OpenAPI+ 연결 및 이벤트 핸들러

※ Windows + Python 32bit 전용
※ PyQt5 메인스레드에서만 실행할 것
"""

from __future__ import annotations

import sys
import time
import threading
from typing import Callable, Optional

from loguru import logger

from config import API_CONFIG

# ── Windows 환경 가드 ──────────────────────────
if sys.platform != "win32":
    logger.warning(
        "키움 OpenAPI+ COM은 Windows 전용입니다. (현재 플랫폼: {p}) — REST API만 사용 가능.",
        p=sys.platform,
    )
    _WINDOWS = False
else:
    try:
        from PyQt5.QAxContainer import QAxWidget
        from PyQt5.QtCore import QEventLoop, QTimer
        from PyQt5.QtWidgets import QApplication
        _WINDOWS = True
    except ImportError:
        logger.warning("PyQt5 미설치 — REST API만 사용. (필요 시 pip install PyQt5)")
        _WINDOWS = False


# ── 커스텀 예외 ───────────────────────────────
class KiwoomNotConnectedError(RuntimeError):
    """API 미연결 상태에서 호출 시 발생"""


class KiwoomLoginError(RuntimeError):
    """로그인 실패 시 발생"""


class KiwoomTimeoutError(TimeoutError):
    """API 응답 타임아웃 발생"""


# ─────────────────────────────────────────────
# KiwoomAPI — 실제 Windows 환경용
# ─────────────────────────────────────────────
if _WINDOWS:

    class KiwoomAPI:
        """
        키움 OpenAPI+ COM 연결 클래스.

        사용 예시:
            app = QApplication(sys.argv)
            api = KiwoomAPI()
            api.login()
            accounts = api.get_account_list()
        """

        COM_ID = "KHOPENAPI.KHOpenAPICtrl.1"

        # TR 데이터 콜백 저장소: {rq_name: callback}
        _tr_callbacks: dict[str, Callable] = {}
        # 실시간 데이터 콜백: {(종목코드, fid): callback}
        _real_callbacks: dict[tuple, Callable] = {}

        def __init__(self) -> None:
            self._ocx = QAxWidget(self.COM_ID)
            self._connected = False
            self._login_loop: Optional[QEventLoop] = None

            # ── 이벤트 슬롯 연결 ──────────────────
            self._ocx.OnEventConnect.connect(self._on_event_connect)
            self._ocx.OnReceiveMsg.connect(self._on_receive_msg)
            self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
            self._ocx.OnReceiveRealData.connect(self._on_receive_real_data)
            self._ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)

            logger.info("KiwoomAPI 초기화 완료 (COM 로드 성공)")

        # ── 퍼블릭 API ────────────────────────────

        def login(self) -> bool:
            """
            키움 로그인 창을 열고 OnEventConnect를 대기한다.
            성공 시 True, 실패 시 KiwoomLoginError 발생.
            """
            timeout = API_CONFIG["login_timeout"]
            max_retry = API_CONFIG["max_reconnect"]

            for attempt in range(1, max_retry + 1):
                logger.info("로그인 시도 {}/{}", attempt, max_retry)
                self._login_loop = QEventLoop()

                self._ocx.dynamicCall("CommConnect()")

                # 타임아웃 타이머
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(self._login_loop.quit)
                timer.start(timeout * 1000)

                self._login_loop.exec_()
                timer.stop()

                if self._connected:
                    logger.success("로그인 성공 | 계좌: {}", self.get_account_list())
                    return True

                logger.warning("로그인 실패 (시도 {})", attempt)
                time.sleep(2)

            raise KiwoomLoginError(f"로그인 {max_retry}회 실패 — 프로그램을 종료합니다.")

        def get_connection_state(self) -> bool:
            """연결 상태 반환 (True = 연결됨)"""
            state = self._ocx.dynamicCall("GetConnectState()")
            return state == 1

        def get_account_list(self) -> list[str]:
            """보유 계좌 목록 반환"""
            self._check_connected()
            raw = self._ocx.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            return [a.strip() for a in raw.split(";") if a.strip()]

        def get_login_info(self, tag: str) -> str:
            """
            GetLoginInfo 래퍼.
            tag: ACCOUNT_CNT | ACCNO | USER_ID | USER_NAME | KEY_BSECGB | FIRGB
            """
            self._check_connected()
            return self._ocx.dynamicCall("GetLoginInfo(QString)", tag)

        def set_input_value(self, id_: str, value: str) -> None:
            """TR 요청 전 입력값 세팅"""
            self._check_connected()
            self._ocx.dynamicCall("SetInputValue(QString, QString)", id_, value)

        def comm_rq_data(
            self,
            rq_name: str,
            tr_code: str,
            prev_next: int,
            scr_no: str,
            callback: Callable,
        ) -> None:
            """
            TR 데이터 요청.
            callback(data: dict) 형태로 결과를 전달받는다.
            """
            self._check_connected()
            self._tr_callbacks[rq_name] = callback
            ret = self._ocx.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rq_name,
                tr_code,
                prev_next,
                scr_no,
            )
            if ret != 0:
                logger.error("CommRqData 오류 코드: {}", ret)

        def set_real_reg(
            self,
            scr_no: str,
            code_list: list[str],
            fid_list: list[str],
            opt_type: str = "0",
        ) -> None:
            """실시간 데이터 등록"""
            self._check_connected()
            codes = ";".join(code_list)
            fids = ";".join(fid_list)
            self._ocx.dynamicCall(
                "SetRealReg(QString, QString, QString, QString)",
                scr_no,
                codes,
                fids,
                opt_type,
            )
            logger.debug("실시간 등록: {} | FIDs: {}", codes, fids)

        def send_order(
            self,
            rq_name: str,
            scr_no: str,
            acc_no: str,
            order_type: int,
            code: str,
            qty: int,
            price: int,
            hoga_gb: str,
            org_order_no: str = "",
        ) -> int:
            """
            주문 전송.
            order_type: 1=신규매수, 2=신규매도, 3=매수취소, 4=매도취소
            hoga_gb: "00"=지정가, "03"=시장가
            반환: 0=성공, 음수=오류코드
            """
            self._check_connected()
            ret = self._ocx.dynamicCall(
                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                rq_name,
                scr_no,
                acc_no,
                order_type,
                code,
                qty,
                price,
                hoga_gb,
                org_order_no,
            )
            if ret != 0:
                logger.error("SendOrder 실패 | 코드: {} | 오류: {}", code, ret)
            return ret

        def get_comm_data(self, tr_code: str, rq_name: str, index: int, item: str) -> str:
            """TR 수신 데이터에서 값 추출"""
            return self._ocx.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                tr_code,
                rq_name,
                index,
                item,
            ).strip()

        def get_chejan_data(self, fid: int) -> str:
            """체결/잔고 데이터 추출"""
            return self._ocx.dynamicCall("GetChejanData(int)", fid).strip()

        def disconnect(self) -> None:
            """연결 해제"""
            if self._connected:
                self._ocx.dynamicCall("CommTerminate()")
                self._connected = False
                logger.info("키움 API 연결 해제")

        # ── 이벤트 핸들러 (슬롯) ─────────────────

        def _on_event_connect(self, err_code: int) -> None:
            if err_code == 0:
                self._connected = True
                logger.success("OnEventConnect: 연결 성공")
            else:
                self._connected = False
                logger.error("OnEventConnect: 연결 실패 (err={})", err_code)

            if self._login_loop and self._login_loop.isRunning():
                self._login_loop.quit()

        def _on_receive_msg(
            self, scr_no: str, rq_name: str, tr_code: str, msg: str
        ) -> None:
            logger.debug("MSG | scr={} rq={} tr={} msg={}", scr_no, rq_name, tr_code, msg)

        def _on_receive_tr_data(
            self,
            scr_no: str,
            rq_name: str,
            tr_code: str,
            record_name: str,
            prev_next: str,
            data_len: int,
            err_code: str,
            msg: str,
            spl_msg: str,
        ) -> None:
            logger.debug("TR 수신: rq={} tr={} prev_next={}", rq_name, tr_code, prev_next)
            cb = self._tr_callbacks.get(rq_name)
            if cb:
                cb(
                    scr_no=scr_no,
                    rq_name=rq_name,
                    tr_code=tr_code,
                    prev_next=prev_next,
                )

        def _on_receive_real_data(
            self, code: str, real_type: str, real_data: str
        ) -> None:
            logger.debug("실시간: code={} type={}", code, real_type)

        def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
            """
            체결/잔고 이벤트.
            gubun: "0"=주문체결, "1"=잔고, "4"=파생잔고
            """
            logger.info("체결 이벤트 | gubun={} fids={}", gubun, fid_list)

        # ── 내부 헬퍼 ────────────────────────────

        def _check_connected(self) -> None:
            if not self._connected:
                raise KiwoomNotConnectedError(
                    "키움 API가 연결되어 있지 않습니다. login()을 먼저 호출하세요."
                )


# ─────────────────────────────────────────────
# KiwoomRestAPI — REST API 기반 (모의/실전 공통)
# ─────────────────────────────────────────────
class KiwoomRestAPI:
    """
    키움 REST API 클라이언트 (실전투자 전용).
    base URL: api.kiwoom.com
    """

    # 토큰 TTL — 키움 OAuth2 는 24시간. 보수적으로 23시간 후 선제 갱신.
    _TOKEN_TTL_SEC: int = 23 * 3600

    def __init__(self) -> None:
        from config import API_CONFIG
        self._appkey    = API_CONFIG["appkey"]
        self._secretkey = API_CONFIG["secretkey"]
        self._account   = API_CONFIG["account_no"]
        self._base      = "https://api.kiwoom.com"
        self._token: str = ""
        self._token_ts: float = 0.0    # 토큰 발급 시각 (epoch sec)
        self._token_expiry_ts: float = 0.0
        self._token_lock = threading.Lock()
        self._connected = False
        # 직전 send_order 가 broker 로부터 받은 ord_no — cancel_order 등에 사용.
        # 호출 직후 OrderManager 가 즉시 읽어가는 동기 흐름을 가정 (self._sending 가 직렬화).
        self.last_ord_no: str = ""
        # 직전 send_order 의 broker 거절 메시지 (성공 시 빈 문자열).
        # OrderManager 가 ret!=0 직후 읽어 orders.reject_msg 컬럼에 저장한다.
        self.last_reject_msg: str = ""
        logger.info("KiwoomRestAPI 초기화 | 실전투자")

    @staticmethod
    def _return_code_ok(body: dict) -> bool:
        return str(body.get("return_code", "")).strip() in ("0", "0000")

    def _issue_token(self) -> bool:
        """토큰 발급 또는 재발급. 성공 시 True 반환."""
        import requests
        if not self._appkey or not self._secretkey:
            logger.error("토큰 발급 불가: KIWOOM_APPKEY/KIWOOM_SECRETKEY 누락")
            self._connected = False
            return False

        try:
            r = requests.post(
                f"{self._base}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self._appkey,
                    "secretkey":  self._secretkey,
                },
                timeout=10,
            )
            try:
                body = r.json()
            except Exception:
                logger.error("토큰 발급 실패: http={} text={}", r.status_code, r.text[:200])
                self._connected = False
                return False

            if r.status_code in (401, 403) or not self._return_code_ok(body):
                logger.error("토큰 발급 실패: http={} rc={} msg={}",
                             r.status_code, body.get("return_code"), body.get("return_msg"))
                self._connected = False
                return False

            token = body.get("token") or body.get("access_token")
            if not token:
                logger.error("토큰 발급 실패: 응답에 token/access_token 없음 | keys={}", list(body.keys()))
                self._connected = False
                return False

            expires_in = int(body.get("expires_in") or body.get("expires") or self._TOKEN_TTL_SEC)
            ttl = max(60, min(expires_in, self._TOKEN_TTL_SEC))
            self._token     = token
            self._token_ts  = time.time()
            self._token_expiry_ts = self._token_ts + ttl
            self._connected = True
            logger.debug("키움 REST 토큰 발급 완료 | ttl={}초", ttl)
            return True
        except Exception as e:
            logger.error("토큰 발급 오류: {}", e)
            self._connected = False
            return False

    def login(self) -> bool:
        ok = self._issue_token()
        if ok:
            logger.info("키움 REST 로그인 성공 | 실전 (토큰 TTL {}h)",
                        self._TOKEN_TTL_SEC // 3600)
        return ok

    def _ensure_token(self) -> bool:
        """헤더 발급 직전 호출 — 토큰 없거나 만료 임박 시 자동 재발급."""
        now = time.time()
        refresh_margin = 10 * 60
        if self._token and self._token_expiry_ts and now < self._token_expiry_ts - refresh_margin:
            return True

        with self._token_lock:
            now = time.time()
            if self._token and self._token_expiry_ts and now < self._token_expiry_ts - refresh_margin:
                return True

            if not self._token:
                logger.info("[키움] 토큰 없음 — 즉시 발급")
            else:
                age = now - self._token_ts
                remain = self._token_expiry_ts - now if self._token_expiry_ts else 0
                logger.info("[키움] 토큰 갱신 필요 | age={:.1f}h remain={:.0f}s",
                            age / 3600, remain)
            return self._issue_token()

    @staticmethod
    def _is_token_invalid(body: dict) -> bool:
        """응답이 8005(토큰 무효) 에러인지 판정."""
        if not isinstance(body, dict):
            return False
        msg = str(body.get("return_msg", ""))
        rc = str(body.get("return_code", "")).strip()
        # 키움은 토큰 만료 시 return_code=3 또는 메시지에 8005/토큰/인증 문구를 내려준다.
        return rc in ("3", "8005", "-8005") or any(
            token_word in msg for token_word in ("8005", "토큰", "Token", "token", "인증", "auth")
        )

    def _headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict:
        """키움 REST 공통 헤더 — api-id / cont-yn / next-key 사용"""
        if not self._ensure_token():
            raise KiwoomLoginError(f"[{api_id}] 키움 REST 토큰 발급/갱신 실패")
        return {
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {self._token}",
            "cont-yn":       cont_yn,
            "next-key":      next_key,
            "api-id":        api_id,
        }

    def _post_tr(self, api_id: str, url_path: str, json_body: dict,
                 timeout: int = 10, cont_yn: str = "N", next_key: str = "") -> dict:
        """
        공통 TR POST 헬퍼 — 토큰 만료(8005) 감지 시 자동 재발급 후 1회 재시도.
        실패 시 빈 dict 반환 (호출자가 체크).
        """
        import requests
        url = f"{self._base}{url_path}"
        for attempt in range(2):
            try:
                r = requests.post(
                    url,
                    headers=self._headers(api_id, cont_yn, next_key),
                    json=json_body,
                    timeout=timeout,
                )
                if r.status_code in (401, 403) and attempt == 0:
                    logger.warning("[{}] HTTP {} 인증 실패 — 토큰 재발급 후 재시도",
                                   api_id, r.status_code)
                    with self._token_lock:
                        self._token = ""
                        self._token_expiry_ts = 0.0
                    if not self._issue_token():
                        return {}
                    continue
                try:
                    body = r.json()
                except Exception:
                    logger.error("[{}] JSON 파싱 실패 | http={} text={}",
                                 api_id, r.status_code, r.text[:200])
                    return {}
                if self._is_token_invalid(body) and attempt == 0:
                    logger.warning("[{}] 토큰 무효 응답 — 재발급 후 재시도", api_id)
                    with self._token_lock:
                        self._token = ""
                        self._token_expiry_ts = 0.0
                    if not self._issue_token():
                        return body
                    continue
                return body
            except Exception as e:
                if attempt == 0:
                    logger.warning("[{}] HTTP 오류 — 재시도: {}", api_id, e)
                    continue
                logger.error("[{}] HTTP 재시도 실패: {}", api_id, e)
                return {}
        return {}

    def get_connection_state(self) -> bool:
        return self._connected

    def get_account_list(self) -> list[str]:
        return [self._account]

    def get_login_info(self, tag: str) -> str:
        return self._account if tag == "ACCNO" else ""

    # ── REST 데이터 조회 (DataCollector용) ───────────
    # KiwoomRestAPI는 TR 콜백 대신 이 동기 메서드를 사용한다.

    # 해외주식 endpoint 한 번 실패하면 세션 동안 스킵 (시간 낭비 방지)
    _overseas_supported: bool = True

    def get_market_microstructure(self, ticker: str) -> dict:
        """
        한국 시장 마이크로구조 데이터 — 외국인·기관 순매수 + 호가 잔량.
        키움 REST: ka10009 (주식거래원요청) / ka10004 (주식호가요청).
        반환: {foreign_net, inst_net, bid_strength, ask_strength}
        실패 시 빈 dict (스크리너가 무시).
        """
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            return {}
        if not getattr(self, "_micro_supported", True):
            return {}
        import requests
        code = ticker.replace(".KS", "").replace(".KQ", "")
        out = {}
        # 1) 호가 잔량 (ka10004)
        try:
            r = requests.post(
                f"{self._base}/api/dostk/mrkcond",
                headers=self._headers("ka10004"),
                json={"stk_cd": code},
                timeout=3,
            )
            body = r.json()
            if self._return_code_ok(body):
                # 매수 1~5호가 잔량 합 vs 매도 1~5호가 잔량 합
                def _num(v):
                    if v is None: return 0
                    try: return int(str(v).replace("+","").replace("-","").replace(",","").strip() or 0)
                    except: return 0
                bid = sum(_num(body.get(f"bid_req_base_pric{i}")) for i in range(1, 6))
                ask = sum(_num(body.get(f"sel_req_base_pric{i}")) for i in range(1, 6))
                # alternate field names if 위 안 잡힐 때
                if bid == 0:
                    bid = sum(_num(body.get(f"bid_qty{i}_amt") or body.get(f"buy_req{i}")) for i in range(1, 6))
                if ask == 0:
                    ask = sum(_num(body.get(f"ask_qty{i}_amt") or body.get(f"sel_req{i}")) for i in range(1, 6))
                out["bid_qty"] = bid
                out["ask_qty"] = ask
                out["bid_ask_ratio"] = round(bid / ask, 3) if ask > 0 else 0.0
        except Exception as e:
            logger.debug("호가 조회 실패 [{}]: {}", code, e)

        # 2) 외국인·기관 순매수 (ka10009 - 주식거래원요청)
        try:
            r = requests.post(
                f"{self._base}/api/dostk/stkinfo",
                headers=self._headers("ka10059"),   # 종목별투자자기관별요청
                json={"stk_cd": code, "dt": ""},
                timeout=3,
            )
            body = r.json()
            if self._return_code_ok(body):
                def _num(v):
                    if v is None: return 0
                    try: return int(str(v).replace("+","").replace(",","").strip() or 0)
                    except: return 0
                # 가능한 필드명 후보 (키움 명세 변형 대응)
                out["foreign_net"]   = _num(body.get("frgn_net_buy") or body.get("frgnr_buyamt"))
                out["inst_net"]      = _num(body.get("inst_net_buy")  or body.get("orgn_buyamt"))
        except Exception as e:
            logger.debug("투자자별 조회 실패 [{}]: {}", code, e)

        # 한 번도 데이터 못 받으면 세션 스킵
        if not out:
            self._micro_supported = False
            logger.info("키움 마이크로구조 endpoint 미지원 — 세션 스킵")
        return out

    def get_overseas_basic_info(self, ticker: str) -> dict:
        """
        해외주식 기본정보 조회 — 키움 REST 해외주식 endpoint (정확한 명세 확인 필요).
        시도 → 실패 시 self._overseas_supported = False 로 세션 스킵.
        반환: 성공 시 {name, current_price, ...} / 실패 시 {} (DataCollector 가 다음 폴백)
        """
        if not KiwoomRestAPI._overseas_supported:
            return {}
        import requests
        # ⚠️ 해외주식 endpoint 추정 — 키움 OpenAPI docs 확인 후 정확한 path 로 교체할 것
        # 후보 1: /api/ovsstk/quotation  (api-id: OPTOS001)
        # 후보 2: /api/dostk/ovrs        (api-id: ovs10001)
        try:
            r = requests.post(
                f"{self._base}/api/ovsstk/quotation",   # ← 추정 path
                headers=self._headers("OPTOS001"),       # ← 추정 api-id
                json={"stk_cd": ticker, "exch_cd": "NAS"},   # NAS, NYS, AMS
                timeout=3,
            )
            body = r.json()
            if self._return_code_ok(body):
                # 응답 파싱은 endpoint 정확히 확정 후 채울 것
                logger.info("✅ 키움 해외 시세 응답 받음 — 첫 호출 사양 확인 필요: keys={}",
                            list(body.keys())[:8])
                return {}   # 일단 빈 dict 반환 (Finnhub 폴백 사용)
            else:
                # 명시적 실패 응답 — endpoint 자체는 응답
                logger.warning("키움 해외 endpoint 실패 (rc={}) — 세션 스킵",
                               body.get("return_code"))
                KiwoomRestAPI._overseas_supported = False
        except Exception as e:
            logger.warning("키움 해외 endpoint 미지원 ({}) — 세션 스킵", type(e).__name__)
            KiwoomRestAPI._overseas_supported = False
        return {}

    def get_overseas_daily_chart(self, ticker: str, count: int = 60) -> dict:
        """해외주식 일봉 차트 — 동일하게 placeholder. 실패 시 세션 스킵."""
        if not KiwoomRestAPI._overseas_supported:
            return {}
        # placeholder: 정확한 endpoint 확정 후 구현
        return {}

    def get_basic_info(self, ticker: str) -> dict:
        """
        주식기본정보 조회 — 키움 REST ka10001 (주식기본정보요청).
        DataCollector가 실패 시 폴백한다.
        """
        # 해외 티커는 본 메서드(국내용)에선 빈 dict — DataCollector 가 폴백 체인 호출
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            return {}
        code = ticker.replace(".KS", "").replace(".KQ", "")
        try:
            body = self._post_tr("ka10001", "/api/dostk/stkinfo",
                                 {"stk_cd": code}, timeout=5)
            if not body or not self._return_code_ok(body):
                logger.warning(
                    "ka10001 실패 [{}] | rc={} msg={}",
                    code, body.get("return_code"), body.get("return_msg"),
                )
                return {}
            # 키움 응답 — 가격 필드는 부호 포함 문자열일 수 있음 ("+72500", "-50")
            def _num(v, cast=int):
                if v is None: return cast(0)
                try: return cast(str(v).replace("+", "").replace(",", "").strip() or 0)
                except Exception: return cast(0)
            return {
                "name":          body.get("stk_nm", ""),
                "current_price": abs(_num(body.get("cur_prc"))),
                "open_price":    abs(_num(body.get("open_pric"))),
                "high_price":    abs(_num(body.get("high_pric"))),
                "low_price":     abs(_num(body.get("low_pric"))),
                "volume":        _num(body.get("trde_qty")),
                "volume_ratio":  _num(body.get("trde_qty_rt"), float) or 1.0,
                "per":           _num(body.get("per"), float),
                "foreigner_pct": _num(body.get("for_hold_qty_rt"), float),
            }
        except Exception as e:
            logger.error("get_basic_info 오류 [{}]: {}", ticker, e)
            return {}

    def get_daily_chart(self, ticker: str, count: int = 60) -> dict:
        """
        주식 일봉차트 조회 — 키움 REST ka10081.
        URL : POST /api/dostk/chart
        body: { stk_cd, base_dt(YYYYMMDD), upd_stkpc_tp("1"=수정주가) }
        반환: {"df": pandas.DataFrame[date,open,high,low,close,volume]}
        """
        import requests
        import pandas as pd
        from datetime import datetime as _dt

        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            return {}
        code = ticker.replace(".KS", "").replace(".KQ", "")
        try:
            body = self._post_tr(
                "ka10081", "/api/dostk/chart",
                {"stk_cd": code,
                 "base_dt": _dt.now().strftime("%Y%m%d"),
                 "upd_stkpc_tp": "1"},
                timeout=10,
            )
            if not body or not self._return_code_ok(body):
                logger.warning("ka10081 일봉 실패 [{}] | rc={} msg={}",
                               code, body.get("return_code"), body.get("return_msg"))
                return {}

            # 응답 리스트 키 — 명세에 따라 후보 다수, 실제 채워진 키 사용
            rows_raw = (body.get("stk_dt_pole_chart_qry")
                        or body.get("output") or body.get("output1") or [])
            if not rows_raw:
                logger.debug("ka10081 응답 비어있음 [{}] | keys={}", code, list(body.keys()))
                return {}

            def _num(v, cast=float):
                if v is None: return cast(0)
                try: return cast(str(v).replace("+", "").replace("-", "").replace(",", "").strip() or 0)
                except Exception: return cast(0)

            rows = []
            for it in rows_raw[:count]:
                d = (it.get("dt") or it.get("base_dt") or it.get("stck_bsop_date") or "").strip()
                if not d: continue
                rows.append({
                    "date":   d,
                    "open":   _num(it.get("open_pric") or it.get("stck_oprc") or it.get("opn_prc")),
                    "high":   _num(it.get("high_pric") or it.get("stck_hgpr") or it.get("hgh_prc")),
                    "low":    _num(it.get("low_pric")  or it.get("stck_lwpr") or it.get("lw_prc")),
                    "close":  _num(it.get("cur_prc")   or it.get("stck_clpr") or it.get("close_pric")),
                    "volume": _num(it.get("trde_qty")  or it.get("acml_vol"), int),
                })
            if not rows:
                logger.debug("ka10081 row 변환 실패 [{}] | sample={}", code, rows_raw[0] if rows_raw else None)
                return {}

            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
            return {"df": df}
        except Exception as e:
            logger.error("get_daily_chart 오류 [{}]: {}", ticker, e)
            return {}

    def get_minute_chart(self, ticker: str, count: int = 120, tic_scope: str = "1") -> dict:
        """
        주식 분봉차트 조회 — 키움 REST ka10080.
        tic_scope: "1"|"3"|"5"|"10"|"15"|"30"|"45"|"60" (분 단위)
        반환: {"df": pandas.DataFrame[time,open,high,low,close,volume]}
        """
        import requests
        import pandas as pd
        if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
            return {}
        code = ticker.replace(".KS", "").replace(".KQ", "")
        try:
            body = self._post_tr(
                "ka10080", "/api/dostk/chart",
                {"stk_cd": code,
                 "tic_scope": tic_scope,
                 "upd_stkpc_tp": "1"},
                timeout=10,
            )
            if not body or not self._return_code_ok(body):
                logger.warning("ka10080 분봉 실패 [{}] | rc={} msg={}",
                               code, body.get("return_code"), body.get("return_msg"))
                return {}

            rows_raw = (body.get("stk_min_pole_chart_qry")
                        or body.get("output") or body.get("output1") or [])
            if not rows_raw:
                return {}

            def _num(v, cast=float):
                if v is None: return cast(0)
                try: return cast(str(v).replace("+", "").replace("-", "").replace(",", "").strip() or 0)
                except Exception: return cast(0)

            rows = []
            for it in rows_raw[:count]:
                t = (it.get("cntr_tm") or it.get("dt") or it.get("stck_cntg_hour") or "").strip()
                if not t: continue
                rows.append({
                    "time":   t,
                    "open":   _num(it.get("open_pric") or it.get("stck_oprc")),
                    "high":   _num(it.get("high_pric") or it.get("stck_hgpr")),
                    "low":    _num(it.get("low_pric")  or it.get("stck_lwpr")),
                    "close":  _num(it.get("cur_prc")   or it.get("stck_prpr")),
                    "volume": _num(it.get("trde_qty")  or it.get("acml_vol"), int),
                })
            if not rows:
                return {}
            df = pd.DataFrame(rows)
            # 키움은 보통 최신순 → 오름차순으로 정렬
            df = df.iloc[::-1].reset_index(drop=True)
            return {"df": df}
        except Exception as e:
            logger.error("get_minute_chart 오류 [{}]: {}", ticker, e)
            return {}

    def get_current_price(self, ticker: str) -> dict:
        """현재가만 필요한 경우 ka10001 결과를 그대로 반환."""
        return self.get_basic_info(ticker)

    def send_order(
        self, rq_name, scr_no, acc_no, order_type: int,
        code: str, qty: int, price: int, hoga_gb: str, org_order_no: str = ""
    ) -> int:
        """
        주식 주문 전송 — 키움 REST kt10000(매수)/kt10001(매도).
        order_type: 1=매수, 2=매도
        hoga_gb: "00"=지정가, "03"=시장가, "81"=시간외종가(15:40~16:00)
        """
        import requests
        # ── 해외주식 가드 ─────────────────────────
        if not (code.endswith(".KS") or code.endswith(".KQ")):
            logger.error("해외주식 주문 거부 [{}] — 키움 OpenAPI는 국내주식만 지원", code)
            return -1

        ticker = code.replace(".KS", "").replace(".KQ", "")
        # 키움 매매구분(trde_tp) 매핑
        if hoga_gb == "81":
            trde_tp = "81"   # 시간외종가 (15:40~16:00)
            ord_uv  = ""
        elif hoga_gb == "62":
            trde_tp = "62"   # 시간외단일가 (16:00~18:00)
            ord_uv  = str(int(price)) if price > 0 else ""
        elif hoga_gb == "03":
            trde_tp = "3"    # 시장가
            ord_uv  = ""
        else:
            trde_tp = "0"    # 보통 지정가
            ord_uv  = str(int(price))

        api_id = "kt10000" if order_type == 1 else "kt10001"
        body = {
            "dmst_stex_tp": "KRX",
            "stk_cd":       ticker,
            "ord_qty":      str(int(qty)),
            "ord_uv":       ord_uv,
            "trde_tp":      trde_tp,
            "cond_uv":      "",
        }
        action = "매수" if order_type == 1 else "매도"
        try:
            resp = self._post_tr(api_id, "/api/dostk/ordr", body, timeout=10)
            if not resp:
                logger.error("주문 응답 없음 | {} {} {}주", action, ticker, qty)
                self.last_ord_no = ""
                self.last_reject_msg = "broker 응답 없음"
                return -1

            if self._return_code_ok(resp):
                ord_no = resp.get("ord_no", "")
                self.last_ord_no = ord_no   # OrderManager 가 cancel_order 시 사용
                self.last_reject_msg = ""
                logger.success(
                    "주문 성공 | {} {} {}주 @{} | ord_no={}",
                    action, ticker, qty, ord_uv or "시장가", ord_no,
                )
                return 0

            self.last_ord_no = ""
            rc  = str(resp.get("return_code", "")).strip()
            msg = str(resp.get("return_msg", "")).strip()
            self.last_reject_msg = (msg or f"rc={rc}")[:200]
            logger.error(
                "주문 실패 | {} {} {}주 trde_tp={} ord_uv={} | rc={} msg={}",
                action, ticker, qty, trde_tp, ord_uv, rc, msg,
            )
            return -1
        except Exception as e:
            self.last_ord_no = ""
            self.last_reject_msg = f"{type(e).__name__}: {e}"[:200]
            logger.error("주문 오류 [{}]: {}", ticker, e)
            return -1

    def cancel_order(self, orig_ord_no: str, ticker: str, side: str, qty: int = 0) -> int:
        """
        미체결 주문 취소 — 키움 REST kt10003(매수취소) / kt10004(매도취소).

        Args:
            orig_ord_no: 원주문번호 (send_order 응답의 ord_no, last_ord_no 캐시 가능)
            ticker:       .KS / .KQ 가능
            side:         'BUY' | 'SELL'
            qty:          취소 수량 (0 → 전량 취소)

        Returns:
            0=성공, -1=실패
        """
        if not orig_ord_no:
            logger.warning("cancel_order: orig_ord_no 없음 — broker 측 취소 skip ({} {})", side, ticker)
            return -1
        code = ticker.replace(".KS", "").replace(".KQ", "")
        api_id = "kt10003" if side.upper() == "BUY" else "kt10004"
        body = {
            "dmst_stex_tp": "KRX",
            "orig_ord_no":  str(orig_ord_no),
            "stk_cd":       code,
            "ord_qty":      str(int(qty or 0)),     # 0 = 전량 취소
            "ord_uv":       "",
            "trde_tp":      "0",                     # 취소는 보통주문(0) 으로
        }
        try:
            resp = self._post_tr(api_id, "/api/dostk/ordr", body, timeout=10)
            if resp and self._return_code_ok(resp):
                logger.success("주문 취소 성공 | {} {} {}주 | orig={}",
                               side, code, qty, orig_ord_no)
                return 0
            rc  = resp.get("return_code") if resp else "-"
            msg = resp.get("return_msg")  if resp else "-"
            logger.error("주문 취소 실패 | {} {} | rc={} msg={}", side, code, rc, msg)
            return -1
        except Exception as e:
            logger.error("주문 취소 오류 [{}]: {}", code, e)
            return -1

    def get_open_orders(self) -> list[dict]:
        """
        미체결 주문 목록 — 키움 REST kt00007 (계좌별주문체결내역상세요청, qry_tp='1' 미체결만).
        반환: [{ord_no, ticker, code, side, ord_qty, filled_qty, remaining}, ...]
        endpoint 미지원 / 응답 없음 시 빈 리스트.
        """
        try:
            body = self._post_tr(
                "kt00007", "/api/dostk/acnt",
                {
                    "qry_tp":       "1",      # 1=미체결만
                    "stk_bond_tp":  "0",      # 0=주식
                    "sell_tp":      "0",      # 0=전체
                    "stk_cd":       "",
                    "fr_ord_no":    "",
                    "dmst_stex_tp": "%",
                },
                timeout=10,
            )
            if not body or not self._return_code_ok(body):
                if body:
                    logger.info(
                        "kt00007 미체결 조회 실패/미지원 | rc={} msg={}",
                        body.get("return_code"), body.get("return_msg"),
                    )
                return []

            rows = (body.get("acnt_ord_cntr_prps_dtl")
                    or body.get("acnt_ord_cntr_prst_array")
                    or body.get("output") or body.get("output1") or [])
            if not rows:
                return []

            def _num(v, cast=int):
                if v is None: return cast(0)
                try:
                    s = str(v).replace("+", "").replace("-", "").replace(",", "").strip()
                    return cast(s or 0)
                except Exception:
                    return cast(0)

            out: list[dict] = []
            for it in rows:
                ord_no = str(it.get("ord_no") or it.get("odno") or "").strip()
                if not ord_no:
                    continue
                code = str(it.get("stk_cd") or it.get("pdno") or "").strip()
                if not code:
                    continue
                code_short = code.replace(".KS", "").replace(".KQ", "")
                ticker = code if (code.endswith(".KS") or code.endswith(".KQ")) else f"{code_short}.KS"
                ord_qty   = _num(it.get("ord_qty") or it.get("oord_qty"))
                filled    = _num(it.get("cntr_qty") or it.get("tot_cntr_qty") or it.get("exec_qty"))
                remaining = max(0, _num(it.get("rmnd_qty") or it.get("rmn_qty")) or (ord_qty - filled))
                io_tp = str(it.get("io_tp_nm") or it.get("ord_dvsn_nm")
                            or it.get("io_tp")  or it.get("sll_buy_dvsn_cd_name")
                            or "").strip()
                side = "SELL" if ("매도" in io_tp or io_tp.upper() in ("2", "S", "SELL")) else "BUY"
                out.append({
                    "ord_no":     ord_no,
                    "ticker":     ticker,
                    "code":       code_short,
                    "side":       side,
                    "ord_qty":    ord_qty,
                    "filled_qty": filled,
                    "remaining":  remaining,
                })
            logger.debug("kt00007 미체결 {}건", len(out))
            return out
        except Exception as e:
            logger.warning("kt00007 미체결 조회 오류: {}", e)
            return []

    def get_deposit_detail(self) -> dict:
        """
        예수금상세현황 — 키움 REST kt00001 (예수금상세현황요청).
        주문가능금액/인출가능금액을 정확히 가져오기 위해 별도 조회.
        """
        try:
            body = self._post_tr("kt00001", "/api/dostk/acnt",
                                 {"qry_tp": "3"}, timeout=10)
            if not body or not self._return_code_ok(body):
                logger.warning(
                    "kt00001 예수금상세 실패 | rc={} msg={}",
                    body.get("return_code"), body.get("return_msg"),
                )
                return {}

            # 첫 호출 시 전체 필드 dump
            if not getattr(KiwoomRestAPI, "_kt1_dumped", False):
                full = {k: v for k, v in body.items()
                        if k not in ("return_code", "return_msg")
                        and str(v).strip() not in ("", "0", "000000000000")}
                logger.info("kt00001 전체 응답 (최초 1회): {}", full)
                KiwoomRestAPI._kt1_dumped = True

            def _num(v):
                if v is None: return 0
                try: return int(str(v).replace("+", "").replace("-", "").replace(",", "").strip() or 0)
                except Exception: return 0

            # 주요 후보 필드를 모두 추출 (실제 응답에서 어떤 키가 매수가능금액인지는 dump 확인 후 확정)
            return {
                "entr":                  _num(body.get("entr")),
                "d2_entra":              _num(body.get("d2_entra")),
                "ord_alow_amt":          _num(body.get("ord_alow_amt")),        # 주문가능금액
                "wthd_alow_amt":         _num(body.get("wthd_alow_amt")),       # 출금가능금액
                "d2_ord_psbl_amt":       _num(body.get("d2_ord_psbl_amt")),     # D+2 주문가능금액
                "mgn_money":             _num(body.get("mgn_money")),           # 증거금액
                "mgn_stk":               _num(body.get("mgn_stk")),             # 증거금주식
                "uncla_amt":             _num(body.get("uncla_amt")),           # 미수금
                "raw":                   body,
            }
        except Exception as e:
            logger.error("kt00001 예수금상세 오류: {}", e)
            return {}

    def get_holdings(self) -> list[dict]:
        """
        보유 종목 명세 — 키움 REST kt00018 (계좌평가잔고내역요청).
        반환: [{ticker, name, qty, avg_price, cur_price, eval_amt, pnl, pnl_rate}, ...]
        """
        try:
            body = self._post_tr("kt00018", "/api/dostk/acnt",
                                 {"qry_tp": "1", "dmst_stex_tp": "KRX"},
                                 timeout=10)
            if not body or not self._return_code_ok(body):
                logger.warning(
                    "kt00018 보유종목 조회 실패 | rc={} msg={}",
                    body.get("return_code"), body.get("return_msg"),
                )
                return []

            rows = (body.get("acnt_evlt_remn_indv_tot")
                    or body.get("output") or body.get("output1") or [])
            if not rows:
                logger.info("보유 종목 없음 (kt00018 응답 비어있음)")
                return []

            def _num(v, cast=float):
                if v is None: return cast(0)
                try: return cast(str(v).replace("+", "").replace("-", "").replace(",", "").strip() or 0)
                except Exception: return cast(0)

            holdings = []
            for it in rows:
                code = (it.get("stk_cd") or it.get("pdno") or "").strip()
                if not code: continue
                # 키움 종목코드는 보통 6자리 숫자, suffix 없음 → KOSPI/KOSDAQ 추론 필요.
                # 일단 .KS 로 가정. 정확한 거래소 정보는 별도 ka10001 으로 확인 가능.
                ticker = code if (code.endswith(".KS") or code.endswith(".KQ")) else f"{code}.KS"
                qty = _num(it.get("rmnd_qty") or it.get("hold_qty") or it.get("hldg_qty"), int)
                if qty <= 0: continue
                holdings.append({
                    "ticker":    ticker,
                    "code":      code.replace(".KS", "").replace(".KQ", ""),
                    "name":      (it.get("stk_nm") or it.get("prdt_name") or "").strip(),
                    "qty":       qty,
                    "avg_price": _num(it.get("pur_pric") or it.get("avg_pur_pric") or it.get("pchs_avg_pric")),
                    "cur_price": _num(it.get("cur_prc")  or it.get("prpr") or it.get("now_prc")),
                    "eval_amt":  _num(it.get("evlt_amt") or it.get("evlu_amt"), int),
                    "pnl":       _num(it.get("evltv_prft") or it.get("evlu_pfls_amt"), int),
                    "pnl_rate":  _num(it.get("prft_rt")  or it.get("evlu_pfls_rt")),
                })
            logger.info("kt00018 보유 {}종목 조회됨", len(holdings))
            return holdings
        except Exception as e:
            logger.error("kt00018 보유종목 조회 오류: {}", e)
            return []

    def get_balance(self) -> dict:
        """
        계좌평가현황 — 키움 REST kt00004 (계좌평가잔고내역요청).
        반환 형태는 KIS 호환 ({"output2": [{...}]})로 정규화한다.

        키움 kt00004 주요 응답 필드 (확인된 명세):
          entr               : 예수금
          d2_entra           : D+2 추정예수금
          tot_est_amt / aset_evlt_amt : 유가/예탁자산평가액
          prsm_dpst_aset_amt : 추정예탁자산
          tot_pur_amt        : 총매입금액
          tot_evlt_amt       : 총평가금액 (보유 종목 평가)
          tot_evlt_pl        : 총평가손익
        """
        try:
            body = self._post_tr("kt00004", "/api/dostk/acnt",
                                 {"qry_tp": "0", "dmst_stex_tp": "KRX"},
                                 timeout=10)
            if not body or not self._return_code_ok(body):
                logger.error(
                    "kt00004 잔고 조회 실패 | rc={} msg={}",
                    body.get("return_code"), body.get("return_msg"),
                )
                return {}

            def _num(v):
                if v is None: return 0
                try: return int(str(v).replace("+", "").replace("-", "").replace(",", "").strip() or 0)
                except Exception: return 0

            # 응답 필드 진단 — 첫 호출 때 raw body 전체를 한번 찍어 누락 필드 찾기
            if not getattr(KiwoomRestAPI, "_kt4_dumped", False):
                # return_code/return_msg 는 제외하고 실값 있는 모든 키 나열
                full_fields = {k: v for k, v in body.items()
                               if k not in ("return_code", "return_msg")
                               and str(v).strip() not in ("", "0", "000000000000")}
                logger.info("kt00004 전체 응답 필드 (최초 1회): {}", full_fields)
                KiwoomRestAPI._kt4_dumped = True

            keys_with_value = {
                k: body[k] for k in (
                    "entr", "d2_entra", "tot_est_amt", "aset_evlt_amt",
                    "prsm_dpst_aset_amt", "tot_evlt_amt", "tot_pur_amt", "tot_evlt_pl",
                ) if k in body and str(body.get(k, "")).strip() not in ("", "0")
            }
            logger.debug("kt00004 잔고 응답 | 활성필드={}", keys_with_value)

            entr      = _num(body.get("entr"))
            d2_entra  = _num(body.get("d2_entra"))
            prsm_dpst = _num(body.get("prsm_dpst_aset_amt"))
            tot_evlt  = _num(body.get("tot_evlt_amt") or body.get("tot_est_amt") or body.get("aset_evlt_amt"))

            # 매수가능 금액 우선순위: D+2예수금 > 예수금 > 추정예탁자산
            buying_power = d2_entra or entr or prsm_dpst

            return {
                "output2": [{
                    "tot_evlu_amt":          tot_evlt,           # 보유 종목 평가
                    "tot_stln_sl_empt_amt":  0,                  # 키움 미제공
                    "buying_power":          buying_power,       # ★ 실제 매수가능 금액
                    "entr":                  entr,
                    "d2_entra":              d2_entra,
                    "prsm_dpst_aset_amt":    prsm_dpst,
                    "tot_pur_amt":           _num(body.get("tot_pur_amt")),
                    "tot_evlt_pl":           _num(body.get("tot_evlt_pl")),
                }],
                "raw": body,
            }
        except Exception as e:
            logger.error("잔고 조회 오류: {}", e)
            return {}

    # 하위 호환용 stub
    def set_input_value(self, id_: str, value: str) -> None:
        pass

    def comm_rq_data(self, rq_name, tr_code, prev_next, scr_no, callback) -> None:
        pass

    def set_real_reg(self, scr_no, code_list, fid_list, opt_type="0") -> None:
        pass

    def get_comm_data(self, tr_code, rq_name, index, item) -> str:
        return "0"

    def get_chejan_data(self, fid) -> str:
        return "0"

    def disconnect(self) -> None:
        self._connected = False
        logger.info("키움 REST 연결 해제")

    def _check_connected(self) -> None:
        if not self._connected:
            raise KiwoomNotConnectedError("KiwoomRestAPI 미연결 — login() 먼저")


# ── 팩토리 함수 ──────────────────────────────
def get_kiwoom_api():
    """
    실전투자 전용 키움 API 인스턴스 반환.
    1. appkey/secretkey 있으면 → KiwoomRestAPI
    2. Windows + PyQt5 있으면  → KiwoomAPI (OpenAPI+ COM)
    3. 둘 다 없으면 → 명시적 오류
    """
    from config import API_CONFIG
    if API_CONFIG.get("appkey"):
        return KiwoomRestAPI()
    if _WINDOWS:
        return KiwoomAPI()
    raise RuntimeError(
        "키움 API 초기화 불가 — KIWOOM_APPKEY/SECRETKEY (.env) 또는 "
        "Windows + PyQt5 환경이 필요합니다."
    )
