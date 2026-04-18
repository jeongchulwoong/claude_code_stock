"""
core/kiwoom_api.py — 키움 OpenAPI+ 연결 및 이벤트 핸들러

※ Windows + Python 32bit 전용
※ PyQt5 메인스레드에서만 실행할 것
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from loguru import logger

from config import API_CONFIG, LOG_DIR

# ── Windows 환경 가드 ──────────────────────────
if sys.platform != "win32":
    logger.warning(
        "키움 OpenAPI+는 Windows 전용입니다. "
        "현재 플랫폼: {p} — MockKiwoomAPI로 대체합니다.",
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
        logger.warning("PyQt5 미설치 — MockKiwoomAPI로 대체합니다. (pip install PyQt5)")
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
    키움 REST API (모의투자 / 실전투자) 클라이언트.
    TRADING_MODE=paper  → mockapi.kiwoom.com  (모의투자)
    TRADING_MODE=live   → api.kiwoom.com      (실전투자)
    """

    def __init__(self, paper: bool = True) -> None:
        from config import API_CONFIG, PAPER_TRADING
        self._appkey    = API_CONFIG["appkey"]
        self._secretkey = API_CONFIG["secretkey"]
        self._account   = API_CONFIG["account_no"]
        self._paper     = paper
        self._base      = "https://mockapi.kiwoom.com" if paper else "https://api.kiwoom.com"
        self._token: str = ""
        self._connected = False
        logger.info("KiwoomRestAPI 초기화 | {}", "모의투자" if paper else "실전투자")

    def login(self) -> bool:
        import requests
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
            body = r.json()
            if body.get("return_code") != 0:
                logger.error("토큰 발급 실패: {}", body.get("return_msg"))
                return False
            self._token     = body["token"]
            self._connected = True
            logger.info("키움 REST 로그인 성공 | {}", "모의" if self._paper else "실전")
            return True
        except Exception as e:
            logger.error("키움 REST 로그인 오류: {}", e)
            return False

    def _headers(self, tr_id: str) -> dict:
        return {
            "Content-Type":  "application/json; charset=UTF-8",
            "authorization": f"Bearer {self._token}",
            "appkey":        self._appkey,
            "appsecret":     self._secretkey,
            "tr_id":         tr_id,
        }

    def get_connection_state(self) -> bool:
        return self._connected

    def get_account_list(self) -> list[str]:
        return [self._account]

    def get_login_info(self, tag: str) -> str:
        return self._account if tag == "ACCNO" else ""

    def get_current_price(self, ticker: str) -> dict:
        """종목 현재가 조회"""
        import requests
        # ticker가 .KS/.KQ 포함이면 제거
        code = ticker.replace(".KS", "").replace(".KQ", "")
        try:
            r = requests.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers("FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=5,
            )
            return r.json().get("output", {})
        except Exception as e:
            logger.error("현재가 조회 오류 [{}]: {}", ticker, e)
            return {}

    def send_order(
        self, rq_name, scr_no, acc_no, order_type: int,
        code: str, qty: int, price: int, hoga_gb: str, org_order_no: str = ""
    ) -> int:
        """
        주식 주문 전송
        order_type: 1=매수, 2=매도
        hoga_gb: "00"=지정가, "03"=시장가
        """
        import requests
        ticker = code.replace(".KS", "").replace(".KQ", "")
        # TR ID: 모의(V), 실전(T) / 매수(0802U), 매도(0801U)
        prefix = "VT" if self._paper else "TT"
        tr_id  = f"{prefix}TC0802U" if order_type == 1 else f"{prefix}TC0801U"
        # 호가 구분: 00→지정가(00), 03→시장가(01)
        ord_dvsn = "01" if hoga_gb == "03" else "00"
        body = {
            "CANO":         self._account[:8],
            "ACNT_PRDT_CD": self._account[8:] if len(self._account) > 8 else "01",
            "PDNO":         ticker,
            "ORD_DVSN":     ord_dvsn,
            "ORD_QTY":      str(qty),
            "ORD_UNPR":     str(price),
        }
        action = "매수" if order_type == 1 else "매도"
        try:
            r = requests.post(
                f"{self._base}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id),
                json=body,
                timeout=10,
            )
            resp = r.json()
            if resp.get("rt_cd") == "0":
                logger.info("주문 성공 | {} {} {}주 {}원", action, ticker, qty, price)
                return 0
            else:
                logger.error("주문 실패 | {} {} | {}", action, ticker, resp.get("msg1", ""))
                return -1
        except Exception as e:
            logger.error("주문 오류 [{}]: {}", ticker, e)
            return -1

    def get_balance(self) -> dict:
        """잔고 조회"""
        import requests
        tr_id = "VTTC8434R" if self._paper else "TTTC8434R"
        try:
            r = requests.get(
                f"{self._base}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=self._headers(tr_id),
                params={
                    "CANO":         self._account[:8],
                    "ACNT_PRDT_CD": self._account[8:] if len(self._account) > 8 else "01",
                    "AFHR_FLPR_YN": "N", "OFL_YN": "N", "INQR_DVSN": "02",
                    "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                timeout=10,
            )
            return r.json()
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


# ─────────────────────────────────────────────
# MockKiwoomAPI — 비-Windows / 테스트 / 페이퍼 트레이딩용
# ─────────────────────────────────────────────
class MockKiwoomAPI:
    """
    Windows가 아닌 환경 또는 페이퍼 트레이딩에서 사용하는
    키움 API 목(Mock) 클래스.
    실제 주문을 전송하지 않고 로그만 기록한다.
    """

    def __init__(self) -> None:
        self._connected = False
        logger.info("MockKiwoomAPI 초기화 (페이퍼 트레이딩 모드)")

    def login(self) -> bool:
        self._connected = True
        logger.info("[MOCK] 로그인 성공 (가상)")
        return True

    def get_connection_state(self) -> bool:
        return self._connected

    def get_account_list(self) -> list[str]:
        return ["MOCK_ACCOUNT_0000000000"]

    def get_login_info(self, tag: str) -> str:
        return f"MOCK_{tag}"

    def set_input_value(self, id_: str, value: str) -> None:
        logger.debug("[MOCK] SetInputValue: {}={}", id_, value)

    def comm_rq_data(self, rq_name, tr_code, prev_next, scr_no, callback) -> None:
        logger.debug("[MOCK] CommRqData: rq={} tr={}", rq_name, tr_code)

    def set_real_reg(self, scr_no, code_list, fid_list, opt_type="0") -> None:
        logger.debug("[MOCK] SetRealReg: codes={}", code_list)

    def send_order(
        self, rq_name, scr_no, acc_no, order_type, code, qty, price, hoga_gb, org_order_no=""
    ) -> int:
        action = {1: "매수", 2: "매도", 3: "매수취소", 4: "매도취소"}.get(order_type, "?")
        logger.info(
            "[MOCK 주문] {} | 종목:{} 수량:{} 가격:{}", action, code, qty, price
        )
        return 0  # 성공 코드

    def get_comm_data(self, tr_code, rq_name, index, item) -> str:
        return "0"

    def get_chejan_data(self, fid) -> str:
        return "0"

    def disconnect(self) -> None:
        self._connected = False
        logger.info("[MOCK] 연결 해제")

    def _check_connected(self) -> None:
        if not self._connected:
            raise KiwoomNotConnectedError("MockAPI 미연결 — login() 먼저 호출")


# ── 팩토리 함수 ──────────────────────────────
def get_kiwoom_api(paper_trading: bool = True):
    """
    우선순위:
    1. appkey/secretkey 있으면 → KiwoomRestAPI (모의 or 실전)
    2. Windows + PyQt5 있으면  → KiwoomAPI (OpenAPI+)
    3. 나머지                  → MockKiwoomAPI
    """
    from config import API_CONFIG
    if API_CONFIG.get("appkey"):
        return KiwoomRestAPI(paper=paper_trading)
    if not paper_trading and _WINDOWS:
        return KiwoomAPI()
    return MockKiwoomAPI()
