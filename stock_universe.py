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
    # 추가 대형주
    "LG에너지솔루션":    "373220.KS",
    "삼성화재":          "000810.KS",
    "현대건설":          "000720.KS",
    "SK스퀘어":          "402340.KS",
    "두산에너빌리티":    "034020.KS",
    "한국가스공사":      "036460.KS",
    "CJ제일제당":        "097950.KS",
    "LG생활건강":        "051900.KS",
    "삼성엔지니어링":    "028050.KS",
    "한화에어로스페이스":"012450.KS",
    "SK바이오팜":        "326030.KS",
    "포스코퓨처엠":      "003670.KS",
    "HD현대중공업":      "329180.KS",
    "HD현대":            "267250.KS",
    "카카오뱅크":        "323410.KS",
    "한미약품":          "128940.KS",
    # KOSDAQ 유망주
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
    "크래프톤":          "259960.KQ",
    "셀트리온헬스케어":  "091990.KQ",
    "셀트리온제약":      "068760.KQ",
    "씨젠":              "096530.KQ",
    "파마리서치":        "214450.KQ",
    "에이치엘비":        "028300.KQ",
    "레고켐바이오":      "141080.KQ",
    "메디톡스":          "086900.KQ",
    "클래시스":          "214150.KQ",
    "리노공업":          "058470.KQ",
    "원익IPS":           "240810.KQ",
    "피에스케이":        "319660.KQ",
    "테크윙":            "089030.KQ",
    "에스에프에이":      "056190.KQ",
}

# ── 해외 ───────────────────────────────────────────────────
FOREIGN: dict[str, str] = {
    # 미국 빅테크 (Magnificent 7 + AI)
    "Apple":             "AAPL",
    "Microsoft":         "MSFT",
    "NVIDIA":            "NVDA",
    "Alphabet":          "GOOGL",
    "Amazon":            "AMZN",
    "Meta":              "META",
    "Tesla":             "TSLA",
    "Netflix":           "NFLX",
    "Adobe":             "ADBE",
    "Salesforce":        "CRM",
    "Oracle":            "ORCL",
    "ServiceNow":        "NOW",
    "Snowflake":         "SNOW",
    "Palantir":          "PLTR",
    "CrowdStrike":       "CRWD",
    "Datadog":           "DDOG",
    # 반도체 (AI 수혜주)
    "Broadcom":          "AVGO",
    "AMD":               "AMD",
    "Intel":             "INTC",
    "Qualcomm":          "QCOM",
    "Texas Instruments": "TXN",
    "Micron":            "MU",
    "Applied Materials": "AMAT",
    "Lam Research":      "LRCX",
    "ASML":              "ASML",
    "KLA Corp":          "KLAC",
    "Marvell":           "MRVL",
    "Analog Devices":    "ADI",
    "NXP":               "NXPI",
    "ON Semiconductor":  "ON",
    "Microchip":         "MCHP",
    "TSMC ADR":          "TSM",
    # 금융 (대형 은행/핀테크)
    "JPMorgan":          "JPM",
    "Bank of America":   "BAC",
    "Wells Fargo":       "WFC",
    "Citigroup":         "C",
    "Goldman Sachs":     "GS",
    "Morgan Stanley":    "MS",
    "Visa":              "V",
    "Mastercard":        "MA",
    "American Express":  "AXP",
    "PayPal":            "PYPL",
    "Block":             "SQ",
    "Berkshire":         "BRK-B",
    "BlackRock":         "BLK",
    "Charles Schwab":    "SCHW",
    # 헬스케어/바이오
    "Johnson & Johnson": "JNJ",
    "UnitedHealth":      "UNH",
    "Eli Lilly":         "LLY",
    "Pfizer":            "PFE",
    "AbbVie":            "ABBV",
    "Merck":             "MRK",
    "Thermo Fisher":     "TMO",
    "Abbott":            "ABT",
    "Danaher":           "DHR",
    "Bristol Myers":     "BMY",
    "Amgen":             "AMGN",
    "Gilead":            "GILD",
    "Regeneron":         "REGN",
    "Vertex":            "VRTX",
    "Moderna":           "MRNA",
    "Intuitive Surgical":"ISRG",
    # 소비재/리테일
    "Walmart":           "WMT",
    "Costco":            "COST",
    "Target":            "TGT",
    "Home Depot":        "HD",
    "Lowe's":            "LOW",
    "McDonald's":        "MCD",
    "Starbucks":         "SBUX",
    "Chipotle":          "CMG",
    "Nike":              "NKE",
    "Lululemon":         "LULU",
    "Coca-Cola":         "KO",
    "PepsiCo":           "PEP",
    "Procter & Gamble":  "PG",
    "Colgate":           "CL",
    "Estee Lauder":      "EL",
    # 에너지
    "ExxonMobil":        "XOM",
    "Chevron":           "CVX",
    "ConocoPhillips":    "COP",
    "Schlumberger":      "SLB",
    "Marathon":          "MPC",
    "NextEra Energy":    "NEE",
    # 산업/항공우주
    "Caterpillar":       "CAT",
    "Boeing":            "BA",
    "GE Aerospace":      "GE",
    "RTX":               "RTX",
    "Lockheed Martin":   "LMT",
    "Honeywell":         "HON",
    "3M":                "MMM",
    "Deere":             "DE",
    "Union Pacific":     "UNP",
    # 통신/미디어
    "Verizon":           "VZ",
    "AT&T":              "T",
    "T-Mobile":          "TMUS",
    "Comcast":           "CMCSA",
    "Disney":            "DIS",
    "Warner Bros":       "WBD",
    "Spotify":           "SPOT",
    # 전기차/배터리
    "Rivian":            "RIVN",
    "Lucid":             "LCID",
    "NIO":               "NIO",
    "XPeng":             "XPEV",
    "Li Auto":           "LI",
    "BYD ADR":           "BYDDY",
    # 아시아 (일본)
    "Toyota":            "TM",
    "Sony":              "SONY",
    "Nintendo ADR":      "NTDOY",
    "SoftBank ADR":      "SFTBY",
    "Keyence ADR":       "KYCCF",
    # 아시아 (중국/홍콩)
    "Alibaba":           "BABA",
    "Tencent ADR":       "TCEHY",
    "JD.com":            "JD",
    "Baidu":             "BIDU",
    "Pinduoduo":         "PDD",
    "NetEase":           "NTES",
    "Meituan ADR":       "MPNGF",
    # 유럽
    "SAP":               "SAP",
    "LVMH ADR":          "LVMUY",
    "Novo Nordisk":      "NVO",
    "AstraZeneca":       "AZN",
    "Shell":             "SHEL",
    "BP":                "BP",
    "Siemens ADR":       "SIEGY",
    "Airbus ADR":        "EADSY",
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


def is_domestic(ticker: str) -> bool:
    """
    한국 주식 여부 판단 (티커 기준).
    단타는 한국 주식만 거래 가능.

    Returns:
        True: 한국 주식 (.KS, .KQ 종목)
        False: 해외 주식
    """
    return ticker.endswith(".KS") or ticker.endswith(".KQ")
