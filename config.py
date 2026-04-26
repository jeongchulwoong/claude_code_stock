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
# 운영 모드 — 실전투자 전용 (모의/페이퍼 모드 제거됨)
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# 리스크 파라미터 (하드코딩 — 함부로 변경 금지)
# ──────────────────────────────────────────────
RISK_CONFIG = {
    "capital_limit":          500_000,    # 단타 전용 운용 한도
    "max_positions":         2,          # 동시 보유 최대 종목 수
    "max_invest_per_trade":  250_000,    # 1회 절대 상한 (자본 50만 대비 50%)
    # ── ATR 기반 동적 SL/TP (단타) ─────
    "risk_per_trade_pct":    0.005,      # 거래당 리스크 = 자본의 0.5%
    "stop_loss_atr_mult":    1.5,        # 손절선 = 진입가 - 1.5 × ATR(14)
    "take_profit_atr_mult":  3.0,        # 익절선 = 진입가 + 3.0 × ATR → R/R 2:1
    "trailing_start_atr_mult": 2.0,      # 트레일링 시작: +2×ATR 도달 후
    "trailing_stop_atr_mult":  1.2,      # 트레일링 컷: 고점 - 1.2×ATR
    # ── 기존 % 기반 (ATR 없을 때 폴백) ─
    "stop_loss_pct":        -0.025,      # -2.5% (비용 0.4% 차감 후 -2.9%)
    "take_profit_pct":       0.06,       # +6% (비용 차감 후 R:R 1.93 통과)
    "trailing_stop_pct":     0.015,
    "trailing_start_pct":    0.02,
    # ── 일일/연속 손실 가드 ─────────────
    "daily_loss_limit":     -10_000,     # 자본 50만 대비 -2% 절대 한도
    "daily_loss_limit_pct": -0.02,       # 자본 대비 -2% 초과 시 당일 중단
    "consecutive_loss_halt": 3,          # 단타 3연패 시 당일 신규 매수 중단
    # ── 신호 품질 ─────────────────────
    "min_confidence":        75,         # 70 → 75 (비용 감안 상향)
    "min_strategies":        2,
    # ── 뉴스 호재/악재 필터 ─────────────
    "news_block_score":     -30,         # 악재 점수 ≤ -30 이면 매수 차단
    # ── 시간 필터 ─────────────────────
    "entry_start":           "09:40",
    "entry_end":             "14:30",
    "force_close_time":      "15:10",    # 15:20 → 15:10 (종가 변동성 회피)
    # ── 시장 레짐 ─────────────────────
    "kospi_min_change":     -0.01,
    "kospi_above_ma20":      True,       # KOSPI 20일선 위에서만 매수
    # ── 단타 → 장투 전환 ─────────────
    "convert_to_long_enabled":   False,  # 단타 손익 왜곡 방지: 전환은 별도 검증 후 사용
    "convert_min_confidence":    60,     # 전환 허용 최소 AI 신뢰도
    "convert_require_ma120":     True,   # 전환 조건: 현재가 > MA120
    "convert_max_atr_pct":       3.0,    # ATR/가격 > 3% 는 변동성 과대로 전환 거부
    # ── 거래비용 모델 (실효 R:R 보정) ──────────
    "cost_roundtrip_pct":        0.004,  # 0.4% (수수료 0.03% + 세금 0.20% + 슬리피지 0.17%)
    "min_effective_rr":          1.8,    # 비용 보정 후 R/R 최소 1.8 미만이면 컷
    # ── 섹터 중복 차단 ─────────────────
    "sector_overlap_block":      True,   # 같은 섹터 중복 매수 차단
    "max_correlated_positions":  1,      # 같은 섹터 최대 1종목
    # ── 펀더멘탈 게이트 (작전주/적자기업 필터) ──
    "fundamental_gate_enabled":  True,
    "fund_min_op_margin":        0.0,    # 영업이익률 > 0% (흑자 기업)
    "fund_max_debt_to_equity":   200.0,  # 부채비율 < 200%
    "fund_min_roe":              0.05,   # ROE > 5%
    "fund_per_min":              0.0,    # PER > 0 (적자 X)
    "fund_per_max":              50.0,   # PER < 50 (거품 X)
    "fund_allow_missing_data":   False,  # 데이터 없으면 차단 (안전 default)
    "fund_cache_hours":          6,      # 펀더멘탈 캐시 유효시간
    # ── 기타 ───────────────────────
    "max_retry_order":       3,
}

# ──────────────────────────────────────────────
# 장투 리스크 파라미터
# ──────────────────────────────────────────────
LONG_RISK_CONFIG = {
    "capital_limit":          2_000_000,  # 장투 전용 운용 한도
    "max_positions":         3,          # 동시 보유 최대 종목 수
    "max_invest_per_trade":  10_000_000, # 1회 절대 상한 (실제 사이징은 가용현금/잔여슬롯)
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

# 해외주식 시세 폴백용 — finnhub.io 무료 발급 (분당 60호출)
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

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
    "scan_interval_minutes": 1,          # 단타 1분봉 스캔 주기
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
# 대시보드 보안 설정 (외부 접근 비밀번호) — 역할 분리
# ──────────────────────────────────────────────
# admin: 모든 기능 (설정 변경, 주문 내역, 잔고)
# client: 읽기 전용 (스크리너, 차트, 종목만)
#
# .env 에 다음 두 값 설정 권장:
#   DASHBOARD_ADMIN_PASSWORD=긴_랜덤_관리자비번
#   DASHBOARD_CLIENT_PASSWORD=친구공유용_간단비번
DASHBOARD_ADMIN_PASSWORD  = os.getenv("DASHBOARD_ADMIN_PASSWORD",
                                       os.getenv("DASHBOARD_PASSWORD", "wjd..dk33?"))
DASHBOARD_CLIENT_PASSWORD = os.getenv("DASHBOARD_CLIENT_PASSWORD", "")  # 비어있으면 client 비활성

# 하위 호환 — 기존 코드가 DASHBOARD_PASSWORD 만 import 하는 경우
DASHBOARD_PASSWORD = DASHBOARD_ADMIN_PASSWORD

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


def get_priority_watch_names() -> list[str]:
    """
    단타 진입 스캔용 우선순위 종목 (변동성·거래대금 큰 핵심).
    매분 1회 스캔 — 단타 신호 빠르게 잡기 위함.
    설정 안 하면 watch_names 의 첫 30개로 폴백.
    """
    cfg = _load_user_config()
    if "priority_watch_names" in cfg and cfg["priority_watch_names"]:
        return cfg["priority_watch_names"]
    return cfg.get("watch_names", _DEFAULT_WATCH_NAMES)[:30]

def get_risk_config() -> dict:
    overrides = _load_user_config().get("risk_config", {})
    return {**RISK_CONFIG, **overrides}

def get_scan_interval() -> int:
    return _load_user_config().get("scan_interval_minutes", SCHEDULE_CONFIG["scan_interval_minutes"])


def _apply_user_runtime_overrides() -> None:
    """Apply user_config overrides with hard safety floors for live trading."""
    cfg = _load_user_config()

    risk_overrides = dict(cfg.get("risk_config", {}))
    if risk_overrides:
        RISK_CONFIG.update(risk_overrides)

    long_overrides = dict(cfg.get("long_risk_config", {}))
    if long_overrides:
        LONG_RISK_CONFIG.update(long_overrides)

    # Safety clamps: user_config may be stale or too aggressive.
    RISK_CONFIG["min_confidence"] = max(65, min(90, int(RISK_CONFIG.get("min_confidence", 75))))
    RISK_CONFIG["min_effective_rr"] = max(1.6, min(2.8, float(RISK_CONFIG.get("min_effective_rr", 1.8))))
    RISK_CONFIG["stop_loss_atr_mult"] = max(1.0, min(2.5, float(RISK_CONFIG.get("stop_loss_atr_mult", 1.5))))
    RISK_CONFIG["take_profit_atr_mult"] = max(2.0, min(4.5, float(RISK_CONFIG.get("take_profit_atr_mult", 3.0))))
    RISK_CONFIG["max_positions"] = max(1, min(3, int(RISK_CONFIG.get("max_positions", 2))))
    RISK_CONFIG["capital_limit"] = max(0, int(RISK_CONFIG.get("capital_limit", 0) or 0))
    LONG_RISK_CONFIG["capital_limit"] = max(0, int(LONG_RISK_CONFIG.get("capital_limit", 0) or 0))

    if "scan_interval_minutes" in cfg:
        SCHEDULE_CONFIG["scan_interval_minutes"] = max(1, min(30, int(cfg.get("scan_interval_minutes", 1))))

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
_apply_user_runtime_overrides()

WATCH_LIST          = get_watch_names()
WATCH_LIST_LONG     = get_long_watch_names()
WATCH_LIST_PRIORITY = get_priority_watch_names()    # 단타 분당 스캔 대상


def fmt_price(ticker: str, price: float) -> str:
    """ticker suffix에 따라 가격+통화 단위 포맷 (KRW·JPY·HKD·USD 지원)"""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"{int(price):,}원"
    if ticker.endswith(".T"):
        return f"¥{int(price):,}"
    if ticker.endswith(".HK"):
        return f"HK${price:.2f}"
    return f"${price:.2f}"
