"""
config.py — 전역 설정 파일
※ 리스크 파라미터는 이 파일에서만 수정한다.
※ API 키·계좌번호는 .env에서만 관리한다.
"""

import os
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
    "model":       "claude-sonnet-4-20250514",
    "max_tokens":  1024,
    "temperature": 0,           # 재현성을 위해 0 고정
}
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ──────────────────────────────────────────────
# 키움 API 설정
# ──────────────────────────────────────────────
API_CONFIG = {
    "account_no":    os.getenv("KIWOOM_ACCOUNT_NO", ""),
    "login_timeout": 30,        # 로그인 대기 최대 시간 (초)
    "tr_timeout":    10,        # TR 요청 타임아웃 (초)
    "max_reconnect": 3,         # 연결 실패 시 최대 재연결 횟수
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
import pathlib
BASE_DIR = pathlib.Path(__file__).parent

DB_PATH  = BASE_DIR / "db"  / "trade_log.db"
LOG_DIR  = BASE_DIR / "logs"

DB_PATH.parent.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# 감시 종목 리스트 (Phase 1 테스트용)
# ──────────────────────────────────────────────
WATCH_LIST = [
    "005930",   # 삼성전자
    "000660",   # SK하이닉스
    "035420",   # NAVER
    "051910",   # LG화학
    "006400",   # 삼성SDI
]
