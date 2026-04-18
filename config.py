"""
config.py — 전역 설정 파일
※ 리스크 파라미터는 이 파일에서만 수정한다.
※ API 키·계좌번호는 .env에서만 관리한다.
※ 런타임 설정(감시 종목 등)은 user_config.json 으로 오버라이드 가능하다.
"""

import json
import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 운영 모드 (반드시 paper로 시작, 실거래 시 live)
# ──────────────────────────────────────────────
TRADING_MODE = os.getenv("TRADING_MODE", "paper")   # "paper" | "live"
PAPER_TRADING = (TRADING_MODE != "live")             # True = 페이퍼 트레이딩

# ──────────────────────────────────────────────
# 리스크 파라미터 (하드코딩 — 함부로 변경 금지)
# ──────────────────────────────────────────────
RISK_CONFIG = {
    "max_positions":         5,          # 동시 보유 최대 종목 수
    "max_invest_per_trade":  500_000,    # 1회 최대 투자금 (원)
    "stop_loss_pct":        -0.03,       # 손절선 -3%
    "take_profit_pct":       0.06,       # 익절선 +6%
    "daily_loss_limit":     -200_000,    # 일일 최대 손실 한도 (원)
    "min_confidence":        70,         # AI 신뢰도 최소값 (0~100)
    "max_retry_order":       3,          # 주문 실패 시 최대 재시도 횟수
}

# ──────────────────────────────────────────────
# AI 판단 엔진 설정
# ──────────────────────────────────────────────
AI_CONFIG = {
    "model":       "gemini-2.5-flash",
    "max_tokens":  2048,
    "temperature": 0,
}
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ──────────────────────────────────────────────
# 키움 API 설정
# ──────────────────────────────────────────────
API_CONFIG = {
    "account_no":  os.getenv("KIWOOM_ACCOUNT_NO", ""),
    "appkey":      os.getenv("KIWOOM_APPKEY", ""),
    "secretkey":   os.getenv("KIWOOM_SECRETKEY", ""),
    "login_timeout": 30,
    "tr_timeout":    10,
    "max_reconnect": 3,
}

# ──────────────────────────────────────────────
# 스케줄 설정
# ──────────────────────────────────────────────
SCHEDULE_CONFIG = {
    "market_open":           "09:00",
    "market_close":          "15:30",
    "scan_interval_minutes": 5,          # AI 판단 주기 (분)
    "pre_market_minutes":    10,         # 장 시작 전 준비 시간
}

# ──────────────────────────────────────────────
# 텔레그램 설정
# ──────────────────────────────────────────────
TELEGRAM_CONFIG = {
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id":   os.getenv("TELEGRAM_CHAT_ID", ""),
}

# ──────────────────────────────────────────────
# DB / 로그 경로
# ──────────────────────────────────────────────
BASE_DIR = pathlib.Path(__file__).parent

DB_PATH  = BASE_DIR / "db"  / "trade_log.db"
LOG_DIR  = BASE_DIR / "logs"
USER_CONFIG_PATH = BASE_DIR / "user_config.json"

DB_PATH.parent.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# 감시 종목 — 이름으로 관리 (티커는 stock_universe 에서 자동 조회)
# ──────────────────────────────────────────────
_DEFAULT_WATCH_NAMES = [
    "삼성전자", "SK하이닉스", "NAVER", "LG화학", "삼성SDI",
    "현대차", "카카오", "셀트리온", "Apple", "NVIDIA",
]

def _load_user_config() -> dict:
    if USER_CONFIG_PATH.exists():
        try:
            return json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_user_config(data: dict) -> None:
    USER_CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def get_watch_names() -> list[str]:
    return _load_user_config().get("watch_names", _DEFAULT_WATCH_NAMES)

def get_risk_config() -> dict:
    overrides = _load_user_config().get("risk_config", {})
    return {**RISK_CONFIG, **overrides}

def get_scan_interval() -> int:
    return _load_user_config().get("scan_interval_minutes", SCHEDULE_CONFIG["scan_interval_minutes"])

# 하위 호환 — 기존 코드가 WATCH_LIST 를 직접 참조할 경우
WATCH_LIST = get_watch_names()
