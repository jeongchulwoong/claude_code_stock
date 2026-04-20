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
    "max_positions":         2,          # 동시 보유 최대 종목 수 (집중 단타)
    "max_invest_per_trade":  300_000,    # 1회 최대 투자금 (원)
    "stop_loss_pct":        -0.02,       # 손절선 -2.0% (노이즈 흡수)
    "take_profit_pct":       0.04,       # 익절선 +4.0% → R/R 2:1
    "trailing_stop_pct":     0.015,      # 트레일링 스탑: 고점 대비 -1.5% (수익 보호)
    "trailing_start_pct":    0.02,       # 트레일링 발동 기준: +2% 도달 시
    "daily_loss_limit":     -100_000,    # 일일 최대 손실 한도 (원)
    "min_confidence":        78,         # AI 신뢰도 최소값 (상향)
    "min_strategies":        2,          # 최소 전략 동의 수 (다중 확인)
    "entry_start":           "09:40",    # 매수 시작 시각 (장 초반 변동성 회피)
    "entry_end":             "14:30",    # 매수 종료 시각 (마감 전 신규 진입 금지)
    "max_retry_order":       3,          # 주문 실패 시 최대 재시도 횟수
    "force_close_time":      "15:20",    # 장 마감 전 강제 청산 시각
    "kospi_min_change":     -0.01,       # KOSPI 하락 -1% 이상이면 당일 매수 중단
}

# ──────────────────────────────────────────────
# 장투 리스크 파라미터
# ──────────────────────────────────────────────
LONG_RISK_CONFIG = {
    "max_positions":         6,          # 동시 보유 최대 종목 수
    "max_invest_per_trade":  1_000_000,  # 1회 최대 투자금 (원)
    "stop_loss_pct":        -0.07,       # 손절선 -7%
    "take_profit_pct":       0.20,       # 익절선 +20%
    "daily_loss_limit":     -500_000,    # 일일 최대 손실 한도 (원)
    "min_confidence":        75,         # AI 신뢰도 최소값
    "max_retry_order":       3,
}

# ──────────────────────────────────────────────
# AI 판단 엔진 설정
# ──────────────────────────────────────────────
AI_CONFIG = {
    "model":       "gemini-2.5-flash-lite",
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
    "scan_interval_minutes": 10,         # AI 판단 주기 (분) - 비용 절감
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
# 대시보드 보안 설정 (외부 접근 비밀번호)
# ──────────────────────────────────────────────
# .env 파일에 DASHBOARD_PASSWORD=your_password 로 설정하면 오버라이드됨
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123")

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
# ⚠️ 단타용: 한국 주식만 포함 (해외 주식은 장투 WATCH_LIST_LONG에만)
_DEFAULT_WATCH_NAMES = [
    # 반도체·IT
    "삼성전자", "SK하이닉스", "삼성전기", "LG이노텍",
    # 인터넷·플랫폼
    "NAVER", "카카오", "카카오뱅크",
    # 2차전지·화학
    "LG화학", "삼성SDI", "LG에너지솔루션", "SK이노베이션", "에코프로비엠",
    # 자동차
    "현대차", "기아", "현대모비스",
    # 바이오·헬스
    "셀트리온", "삼성바이오로직스", "한미약품",
    # 금융
    "KB금융", "신한지주", "하나금융지주", "삼성생명",
    # 건설·중공업
    "현대건설", "삼성물산", "HD현대",
    # 소비재·유통
    "아모레퍼시픽", "LG생활건강", "CJ제일제당",
    # 통신·전력
    "SK텔레콤", "KT", "한국전력",
    # 철강·소재
    "POSCO홀딩스", "LG전자",
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

def get_long_watch_names() -> list[str]:
    _DEFAULT_LONG = [
        # 국내 대형주
        "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스",
        "현대차", "기아", "LG화학", "삼성SDI", "NAVER", "셀트리온",
        "KB금융", "카카오", "LG전자", "SK이노베이션",
        # 국내 유망주
        "에코프로비엠", "에코프로", "알테오젠", "크래프톤", "HYBE",
        # 미국 빅테크 (Mag 7 + AI)
        "Apple", "Microsoft", "NVIDIA", "Alphabet", "Amazon",
        "Meta", "Tesla", "Netflix", "Adobe", "Salesforce",
        # 미국 반도체 (AI 수혜)
        "Broadcom", "AMD", "Qualcomm", "Micron", "ASML", "TSMC ADR",
        # 미국 금융
        "JPMorgan", "Visa", "Mastercard", "Berkshire", "PayPal",
        # 미국 헬스케어
        "Eli Lilly", "UnitedHealth", "Johnson & Johnson", "Thermo Fisher",
        # 미국 소비재
        "Costco", "Walmart", "Nike", "Starbucks", "Coca-Cola",
        # 중국/아시아
        "Alibaba", "Tencent ADR", "Toyota", "Sony",
    ]
    return _load_user_config().get("long_watch_names", _DEFAULT_LONG)

def get_foreign_watch_names() -> list[str]:
    _DEFAULT_FOREIGN_WATCH = [
        "Apple", "Microsoft", "NVIDIA", "Alphabet", "Amazon",
        "Meta", "Tesla", "Netflix", "Broadcom", "AMD",
        "TSMC ADR", "JPMorgan", "Visa", "Eli Lilly", "Costco",
    ]
    return _load_user_config().get("foreign_watch_names", _DEFAULT_FOREIGN_WATCH)

# 하위 호환 — 기존 코드가 WATCH_LIST 를 직접 참조할 경우
WATCH_LIST      = get_watch_names()
WATCH_LIST_LONG = get_long_watch_names()


def fmt_price(ticker: str, price: float) -> str:
    """ticker suffix에 따라 가격+통화 단위 포맷 (KRW·JPY·HKD·USD 지원)"""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"{int(price):,}원"
    if ticker.endswith(".T"):
        return f"¥{int(price):,}"
    if ticker.endswith(".HK"):
        return f"HK${price:.2f}"
    return f"${price:.2f}"
