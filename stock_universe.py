"""
Central stock universe — name → ticker mapping.
All modules import from here instead of hardcoding tickers.
"""

# ── 국내 (KOSPI / KOSDAQ) ───────────────────────────────────
DOMESTIC: dict[str, str] = {
    # KOSPI 시가총액 상위
    "삼성전자":          "005930.KS",
    "SK하이닉스":        "000660.KS",
    "삼성바이오로직스":  "207940.KS",
    "현대차":            "005380.KS",
    "기아":              "000270.KS",
    "LG화학":            "051910.KS",
    "삼성SDI":           "006400.KS",
    "NAVER":             "035420.KS",
    "셀트리온":          "068270.KS",
    "KB금융":            "105560.KS",
    "신한지주":          "055550.KS",
    "카카오":            "035720.KS",
    "SK이노베이션":      "096770.KS",
    "삼성물산":          "028260.KS",
    "LG전자":            "066570.KS",
    "LG":                "003550.KS",
    "SK텔레콤":          "017670.KS",
    "현대모비스":        "012330.KS",
    "하나금융지주":      "086790.KS",
    "삼성생명":          "032830.KS",
    "에쓰오일":          "010950.KS",
    "대한항공":          "003490.KS",
    "SK":                "034730.KS",
    "삼성SDS":           "018260.KS",
    "HMM":               "011200.KS",
    "삼성전기":          "009150.KS",
    "고려아연":          "010130.KS",
    "한국전력":          "015760.KS",
    "한화솔루션":        "009830.KS",
    "LG이노텍":          "011070.KS",
    "KT":                "030200.KS",
    "POSCO홀딩스":       "003670.KS",
    "산업은행":          "024110.KS",
    "이마트":            "139480.KS",
    "현대제철":          "004020.KS",
    "아모레퍼시픽":      "090430.KS",
    "호텔신라":          "008770.KS",
    "오리온":            "271560.KS",
    "한국타이어":        "161390.KS",
    # KOSDAQ
    "에코프로비엠":      "247540.KQ",
    "에코프로":          "086520.KQ",
    "알테오젠":          "196170.KQ",
    "솔브레인":          "357780.KQ",
    "휴젤":              "145020.KQ",
    "위메이드":          "112040.KQ",
    "엔씨소프트":        "036570.KQ",
    "펄어비스":          "263750.KQ",
    "카카오게임즈":      "293490.KQ",
    "JYP엔터테인먼트":   "035900.KQ",
    "SM엔터테인먼트":    "041510.KQ",
    "HYBE":              "352820.KQ",
}

# ── 해외 ───────────────────────────────────────────────────
FOREIGN: dict[str, str] = {
    # 미국 빅테크
    "Apple":             "AAPL",
    "Microsoft":         "MSFT",
    "NVIDIA":            "NVDA",
    "Alphabet":          "GOOGL",
    "Amazon":            "AMZN",
    "Meta":              "META",
    "Tesla":             "TSLA",
    "Broadcom":          "AVGO",
    "AMD":               "AMD",
    "Intel":             "INTC",
    "Qualcomm":          "QCOM",
    "Texas Instruments": "TXN",
    "Micron":            "MU",
    "Applied Materials": "AMAT",
    "Lam Research":      "LRCX",
    # 미국 금융
    "JPMorgan":          "JPM",
    "Bank of America":   "BAC",
    "Goldman Sachs":     "GS",
    "Morgan Stanley":    "MS",
    "Visa":              "V",
    "Mastercard":        "MA",
    "Berkshire":         "BRK-B",
    "BlackRock":         "BLK",
    # 미국 헬스케어
    "Johnson & Johnson": "JNJ",
    "UnitedHealth":      "UNH",
    "Eli Lilly":         "LLY",
    "Pfizer":            "PFE",
    "AbbVie":            "ABBV",
    "Merck":             "MRK",
    "Thermo Fisher":     "TMO",
    # 미국 소비재/산업
    "Walmart":           "WMT",
    "Costco":            "COST",
    "Home Depot":        "HD",
    "McDonald's":        "MCD",
    "Nike":              "NKE",
    "Starbucks":         "SBUX",
    "Amgen":             "AMGN",
    # 미국 에너지/기타
    "ExxonMobil":        "XOM",
    "Chevron":           "CVX",
    "ConocoPhillips":    "COP",
    "NextEra Energy":    "NEE",
    "Caterpillar":       "CAT",
    "Boeing":            "BA",
    "GE Aerospace":      "GE",
    "RTX":               "RTX",
    # 반도체/장비
    "TSMC ADR":          "TSM",
    "ASML":              "ASML",
    "KLA Corp":          "KLAC",
    # 아시아
    "SoftBank":          "9984.T",
    "Toyota":            "7203.T",
    "Sony":              "6758.T",
    "Keyence":           "6861.T",
    "Nintendo":          "7974.T",
    "NTT":               "9432.T",
    "TSMC TW":           "2330.TW",
    "MediaTek":          "2454.TW",
    "Tencent":           "0700.HK",
    "Alibaba HK":        "9988.HK",
    "Meituan":           "3690.HK",
    # 유럽
    "SAP":               "SAP",
    "Nestle":            "NESN.SW",
    "Novartis":          "NOVN.SW",
    "Roche":             "ROG.SW",
}

ALL: dict[str, str] = {**DOMESTIC, **FOREIGN}

# reverse lookup: ticker → name
TICKER_TO_NAME: dict[str, str] = {v: k for k, v in ALL.items()}


def get_ticker(name: str) -> str | None:
    """이름으로 티커 반환. 없으면 None."""
    return ALL.get(name)


def get_name(ticker: str) -> str:
    """티커로 이름 반환. 없으면 ticker 그대로."""
    return TICKER_TO_NAME.get(ticker, ticker)


def resolve(name_or_ticker: str) -> tuple[str, str]:
    """
    이름 또는 티커를 받아 (ticker, name) 반환.
    예) "삼성전자" → ("005930.KS", "삼성전자")
        "005930.KS" → ("005930.KS", "삼성전자")
    """
    if name_or_ticker in ALL:
        return ALL[name_or_ticker], name_or_ticker
    if name_or_ticker in TICKER_TO_NAME:
        return name_or_ticker, TICKER_TO_NAME[name_or_ticker]
    return name_or_ticker, name_or_ticker
