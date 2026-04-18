"""
core/news_analyzer.py — 종목별 뉴스 수집 + Claude 호재/악재 분석

뉴스 수집 소스 (우선순위 순, 막히면 다음으로 자동 전환):
  1. 네이버 금융 뉴스    (국내주식 — 한국어)
  2. Finnhub News       (해외주식 — 영어)
  3. Yahoo Finance RSS  (국내·해외 — 영어/한국어)
  4. Google News RSS    (범용 — 다국어)

분석:
  - Claude API가 뉴스 헤드라인+요약을 읽고
    해당 종목에 대한 호재/악재 여부를 판단
  - 단순 감성이 아니라 "왜 호재/악재인지 근거"까지 출력
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from loguru import logger

from config import AI_CONFIG, GEMINI_API_KEY


# ── 데이터 구조 ───────────────────────────────

@dataclass
class NewsItem:
    title:    str
    summary:  str
    source:   str
    url:      str
    pub_date: str


@dataclass
class NewsVerdict:
    """Claude가 내린 종목별 뉴스 호재/악재 판단"""
    ticker:      str
    ticker_name: str
    judgment:    Literal["호재", "악재", "중립", "분석불가"]
    score:       int          # -100 ~ +100 (양수=호재, 음수=악재)
    reason:      str          # 판단 근거 2~3줄
    key_points:  list[str]    # 핵심 포인트 (최대 3개)
    news_count:  int
    news_titles: list[str]
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_positive(self) -> bool:
        return self.judgment == "호재"

    @property
    def is_negative(self) -> bool:
        return self.judgment == "악재"

    @property
    def emoji(self) -> str:
        return {"호재": "🟢", "악재": "🔴", "중립": "⚪", "분석불가": "❓"}[self.judgment]

    def to_telegram(self) -> str:
        """텔레그램 알림 형식"""
        points = "\n".join(f"  • {p}" for p in self.key_points)
        return (
            f"{self.emoji} [{self.judgment}] {self.ticker_name}({self.ticker})\n"
            f"{'━'*24}\n"
            f"스코어: {self.score:+d}점\n\n"
            f"판단 근거:\n  {self.reason}\n\n"
            f"핵심 포인트:\n{points}\n"
            f"{'━'*24}\n"
            f"수집 뉴스: {self.news_count}건\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )


# ── 뉴스 수집기 ───────────────────────────────

class NewsCollector:
    """
    여러 소스에서 종목 관련 뉴스를 수집한다.
    소스가 막히면 자동으로 다음 소스를 시도한다.
    """

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    _TIMEOUT = 8

    def collect(
        self,
        ticker:      str,
        ticker_name: str = "",
        max_items:   int = 10,
    ) -> list[NewsItem]:
        """
        종목 관련 뉴스를 수집하여 반환한다.
        국내주식이면 네이버 금융 우선, 해외주식이면 Finnhub/Yahoo 우선.
        """
        is_kr = ticker.isdigit()
        sources = (
            [self._naver, self._google_news, self._yahoo_rss, self._finnhub]
            if is_kr else
            [self._finnhub, self._yahoo_rss, self._google_news]
        )

        query = ticker_name or ticker
        all_items: list[NewsItem] = []

        for fn in sources:
            try:
                items = fn(ticker, query, max_items)
                if items:
                    all_items.extend(items)
                    logger.info(
                        "[뉴스] {}: {} ({} 건)",
                        fn.__name__.replace("_","").upper(), ticker, len(items)
                    )
                    if len(all_items) >= max_items:
                        break
            except Exception as e:
                logger.debug("[뉴스] {} 실패 [{}]: {}", fn.__name__, ticker, e)

        # 중복 제거 (제목 기준)
        seen   = set()
        unique = []
        for item in all_items:
            key = item.title[:40]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        # 모든 소스 실패 시 → Mock 뉴스 (개발/테스트 환경)
        if not unique:
            logger.warning("모든 뉴스 소스 실패 — Mock 뉴스 사용: {}", ticker)
            unique = self._mock_news(ticker, query)

        logger.info("뉴스 수집 완료: {} | {}건", ticker, len(unique))
        return unique[:max_items]

    def _mock_news(self, ticker: str, query: str) -> list[NewsItem]:
        """모든 외부 소스 실패 시 사용하는 데모 뉴스"""
        import random
        random.seed(sum(ord(c) for c in ticker))
        is_kr = ticker.isdigit()
        if is_kr:
            templates = [
                (f"{query} 3분기 영업이익 전년比 23% 증가 '어닝 서프라이즈'", "한국경제"),
                (f"{query}, 차세대 반도체 양산 돌입…글로벌 점유율 확대 기대", "매일경제"),
                (f"{query} 외국인 5거래일 연속 순매수…기관도 동반 매수세", "연합뉴스"),
                (f"{query} 노조 임금협상 타결…생산 정상화 기대", "뉴시스"),
                (f"{query}, 신사업 부문 흑자 전환 성공", "머니투데이"),
            ]
        else:
            templates = [
                (f"{query} beats Q3 earnings estimates, raises full-year guidance", "Reuters"),
                (f"{query} announces B share buyback program", "Bloomberg"),
                (f"{query} wins major government contract worth .3B", "WSJ"),
                (f"{query} expands AI partnership with key enterprise clients", "CNBC"),
                (f"{query} reports record revenue driven by strong demand", "FT"),
            ]
        return [
            NewsItem(title=t, summary="", source=s, url="", pub_date="")
            for t, s in random.sample(templates, min(3, len(templates)))
        ]

    # ── 네이버 금융 뉴스 (국내 한국어) ──────────

    def _naver(self, ticker: str, query: str, limit: int) -> list[NewsItem]:
        """네이버 금융 종목 뉴스 RSS"""
        # 종목코드 기반 직접 뉴스 피드
        url = f"https://finance.naver.com/item/news_news.naver?code={ticker}&page=1&sm=title_entity_id.basic&clusterId="
        req = urllib.request.Request(url, headers=self._HEADERS)
        with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
            html = resp.read().decode("cp949", errors="ignore")

        from bs4 import BeautifulSoup
        soup  = BeautifulSoup(html, "html.parser")
        items = []

        for row in soup.select("table.type5 tr")[:limit]:
            title_tag = row.select_one("td.title a")
            source_tag= row.select_one("td.info")
            date_tag  = row.select_one("td.date")
            if not title_tag:
                continue
            items.append(NewsItem(
                title    = title_tag.get_text(strip=True),
                summary  = "",
                source   = source_tag.get_text(strip=True) if source_tag else "네이버금융",
                url      = "https://finance.naver.com" + title_tag.get("href",""),
                pub_date = date_tag.get_text(strip=True) if date_tag else "",
            ))
        return items

    # ── Yahoo Finance RSS ────────────────────

    def _yahoo_rss(self, ticker: str, query: str, limit: int) -> list[NewsItem]:
        """Yahoo Finance RSS 피드"""
        yahoo_ticker = f"{ticker}.KS" if ticker.isdigit() else ticker
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={yahoo_ticker}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers=self._HEADERS)
        with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
            xml = resp.read().decode("utf-8", errors="ignore")

        items = []
        for m in re.finditer(r"<item>(.*?)</item>", xml, re.DOTALL)[:limit]:
            block   = m.group(1)
            title   = re.search(r"<title>(.*?)</title>", block)
            desc    = re.search(r"<description>(.*?)</description>", block)
            link    = re.search(r"<link>(.*?)</link>", block)
            pubdate = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if title:
                items.append(NewsItem(
                    title    = re.sub(r"<[^>]+>", "", title.group(1)).strip(),
                    summary  = re.sub(r"<[^>]+>", "", desc.group(1) if desc else "").strip()[:200],
                    source   = "Yahoo Finance",
                    url      = link.group(1).strip() if link else "",
                    pub_date = pubdate.group(1).strip() if pubdate else "",
                ))
        return items

    # ── Google News RSS ──────────────────────

    def _google_news(self, ticker: str, query: str, limit: int) -> list[NewsItem]:
        """Google News RSS (한국어/영어)"""
        q   = urllib.parse.quote(f"{query} 주식" if ticker.isdigit() else query)
        hl  = "ko" if ticker.isdigit() else "en"
        url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl=KR&ceid=KR:{hl}"
        req = urllib.request.Request(url, headers=self._HEADERS)
        with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
            xml = resp.read().decode("utf-8", errors="ignore")

        items = []
        for m in re.finditer(r"<item>(.*?)</item>", xml, re.DOTALL)[:limit]:
            block   = m.group(1)
            title   = re.search(r"<title>(.*?)</title>", block)
            source  = re.search(r"<source[^>]*>(.*?)</source>", block)
            pubdate = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if title:
                raw = re.sub(r"<[^>]+>", "", title.group(1)).strip()
                # Google 뉴스는 "제목 - 언론사" 형식
                raw = re.sub(r" - [^-]+$", "", raw)
                items.append(NewsItem(
                    title    = raw,
                    summary  = "",
                    source   = source.group(1) if source else "Google News",
                    url      = "",
                    pub_date = pubdate.group(1).strip() if pubdate else "",
                ))
        return items

    # ── Finnhub (해외주식) ───────────────────

    def _finnhub(self, ticker: str, query: str, limit: int) -> list[NewsItem]:
        """Finnhub Company News API"""
        import os; key = os.getenv("FINNHUB_API_KEY", "")
        if not key:
            return []
        from datetime import date, timedelta
        from_d = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
        to_d   = date.today().strftime("%Y-%m-%d")
        url = (
            f"https://finnhub.io/api/v1/company-news"
            f"?symbol={ticker}&from={from_d}&to={to_d}&token={key}"
        )
        req = urllib.request.Request(url, headers=self._HEADERS)
        with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
            data = json.loads(resp.read())
        items = []
        for d in data[:limit]:
            items.append(NewsItem(
                title    = d.get("headline", ""),
                summary  = d.get("summary", "")[:200],
                source   = d.get("source", "Finnhub"),
                url      = d.get("url", ""),
                pub_date = str(d.get("datetime", "")),
            ))
        return items


# ── Claude 호재/악재 분석기 ───────────────────

class NewsAnalyzer:
    """
    수집된 뉴스를 Claude API에 전달하여
    해당 종목에 대한 호재/악재 여부를 판단한다.
    """

    _SYSTEM = """\
당신은 주식 뉴스 전문 분석가입니다.
주어진 뉴스 목록을 읽고, 특정 종목에 대해 호재인지 악재인지 판단합니다.

판단 기준:
- 호재: 실적 개선, 신사업 진출, 수주, 특허, 주요 계약, 주가 상승 촉진 요인
- 악재: 실적 악화, 소송, 제재, 리콜, 주요 계약 해지, 경쟁 심화, 규제
- 중립: 단순 인사이동, 업계 일반 뉴스, 직접 관련 없는 내용

응답은 반드시 아래 JSON 형식만 출력하고 다른 텍스트는 절대 포함하지 않는다:
{
  "judgment": "호재" | "악재" | "중립" | "분석불가",
  "score": -100 ~ 100 (호재면 양수, 악재면 음수, 중립이면 0),
  "reason": "판단 근거 2~3줄 (한국어)",
  "key_points": ["핵심 포인트1", "핵심 포인트2", "핵심 포인트3"]
}"""

    def __init__(self) -> None:
        self._mock = not bool(GEMINI_API_KEY)
        if not self._mock:
            from google import genai
            self._client = genai.Client(api_key=GEMINI_API_KEY)

    def analyze(
        self,
        ticker:      str,
        ticker_name: str,
        news_items:  list[NewsItem],
    ) -> NewsVerdict:
        """뉴스 목록 → Claude 분석 → NewsVerdict 반환"""
        if not news_items:
            return NewsVerdict(
                ticker=ticker, ticker_name=ticker_name,
                judgment="분석불가", score=0,
                reason="수집된 뉴스가 없습니다.",
                key_points=[], news_count=0, news_titles=[],
            )

        if self._mock:
            return self._mock_verdict(ticker, ticker_name, news_items)

        prompt = self._build_prompt(ticker, ticker_name, news_items)
        try:
            from google.genai import types as gtypes
            resp = self._client.models.generate_content(
                model    = AI_CONFIG["model"],
                contents = self._SYSTEM + "\n\n" + prompt,
                config   = gtypes.GenerateContentConfig(
                    temperature=0, max_output_tokens=600,
                ),
            )
            raw  = resp.text
            data = json.loads(re.sub(r"```json|```", "", raw).strip())
        except Exception as e:
            logger.error("Gemini 뉴스 분석 실패 [{}]: {}", ticker, e)
            return self._fallback(ticker, ticker_name, news_items)

        verdict = NewsVerdict(
            ticker       = ticker,
            ticker_name  = ticker_name,
            judgment     = data.get("judgment", "중립"),
            score        = int(data.get("score", 0)),
            reason       = data.get("reason", ""),
            key_points   = data.get("key_points", [])[:3],
            news_count   = len(news_items),
            news_titles  = [n.title for n in news_items[:5]],
        )

        logger.info(
            "뉴스 분석: {} | {} | 스코어:{:+d} | {}",
            ticker, verdict.judgment, verdict.score, verdict.reason[:50],
        )
        return verdict

    def analyze_batch(
        self,
        targets: list[tuple[str, str]],   # [(ticker, name), ...]
        max_news: int = 8,
    ) -> list[NewsVerdict]:
        """여러 종목을 일괄 분석한다."""
        collector = NewsCollector()
        results   = []
        for ticker, name in targets:
            news  = collector.collect(ticker, name, max_items=max_news)
            verdict = self.analyze(ticker, name, news)
            results.append(verdict)
            time.sleep(0.5)
        return results

    # ── 프롬프트 빌더 ─────────────────────────

    @staticmethod
    def _build_prompt(ticker: str, name: str, items: list[NewsItem]) -> str:
        news_text = ""
        for i, n in enumerate(items[:10], 1):
            news_text += f"\n{i}. [{n.source}] {n.title}"
            if n.summary:
                news_text += f"\n   요약: {n.summary[:150]}"

        return f"""
분석 대상 종목: {ticker} ({name})

아래 뉴스들을 읽고, 이 종목에 대해 호재인지 악재인지 판단해주세요.
뉴스가 직접적으로 이 종목과 관련 없는 경우 '중립'으로 판단하세요.

[수집된 뉴스 {len(items)}건]
{news_text}

위 뉴스들을 종합적으로 분석하여 JSON 형식으로만 응답하세요.
종목명과 관련없는 일반 시장 뉴스는 '중립'으로 처리하세요.
"""

    # ── Mock / Fallback ───────────────────────

    @staticmethod
    def _mock_verdict(
        ticker: str, name: str, items: list[NewsItem]
    ) -> NewsVerdict:
        import random
        options = [
            ("호재", random.randint(30, 85),
             f"{name}의 최근 뉴스는 전반적으로 긍정적입니다. 실적 개선과 신사업 기대감이 반영된 것으로 보입니다.",
             ["분기 실적 서프라이즈 예상", "주요 신규 계약 체결", "외국인 순매수 유입"]),
            ("악재", random.randint(-80, -25),
             f"{name} 관련 부정적 뉴스가 감지됩니다. 단기 주가 하방 압력이 예상됩니다.",
             ["실적 하향 조정 우려", "주요 거래처 계약 취소", "경쟁사 신제품 출시"]),
            ("중립", 0,
             f"{name}에 직접적인 영향을 미치는 뉴스는 제한적입니다.",
             ["업계 일반 동향", "거시경제 변수", "직접 영향 제한"]),
        ]
        j, s, r, kp = random.choice(options)
        return NewsVerdict(
            ticker=ticker, ticker_name=name,
            judgment=j, score=s, reason=r, key_points=kp,
            news_count=len(items),
            news_titles=[n.title for n in items[:5]],
        )

    @staticmethod
    def _fallback(ticker: str, name: str, items: list[NewsItem]) -> NewsVerdict:
        return NewsVerdict(
            ticker=ticker, ticker_name=name,
            judgment="분석불가", score=0,
            reason="AI 분석 실패 — 중립 처리",
            key_points=[], news_count=len(items),
            news_titles=[n.title for n in items[:5]],
        )


# ── 통합 인터페이스 ───────────────────────────

class StockNewsService:
    """
    뉴스 수집 + Claude 분석 + AI 판단 통합 서비스.
    매매 판단 엔진과 함께 사용하기 위한 최상위 인터페이스.
    """

    # 종목코드 → 한국어 이름 매핑 (자주 사용하는 종목)
    KR_NAMES = {
        "005930": "삼성전자",  "000660": "SK하이닉스",
        "035420": "NAVER",    "051910": "LG화학",
        "006400": "삼성SDI",  "035720": "카카오",
        "005380": "현대차",   "000270": "기아",
        "068270": "셀트리온",  "207940": "삼성바이오로직스",
    }
    US_NAMES = {
        "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
        "TSLA": "Tesla", "NVDA": "NVIDIA",    "META": "Meta",
        "AMZN": "Amazon","NFLX": "Netflix",   "AMD": "AMD",
    }

    def __init__(self) -> None:
        self._collector = NewsCollector()
        self._analyzer  = NewsAnalyzer()

    def get_news_verdict(
        self,
        ticker:   str,
        name:     str = "",
        max_news: int = 8,
    ) -> NewsVerdict:
        """단일 종목 뉴스 호재/악재 분석"""
        if not name:
            name = self.KR_NAMES.get(ticker) or self.US_NAMES.get(ticker) or ticker
        news    = self._collector.collect(ticker, name, max_items=max_news)
        verdict = self._analyzer.analyze(ticker, name, news)
        return verdict

    def get_batch_verdicts(
        self,
        tickers:  list[str],
        max_news: int = 6,
    ) -> list[NewsVerdict]:
        """여러 종목 일괄 분석"""
        results = []
        for ticker in tickers:
            name = self.KR_NAMES.get(ticker) or self.US_NAMES.get(ticker) or ticker
            results.append(self.get_news_verdict(ticker, name, max_news))
            time.sleep(0.3)
        return results

    def print_verdicts(self, verdicts: list[NewsVerdict]) -> None:
        """터미널 출력"""
        print("\n" + "═"*60)
        print("  📰 종목별 뉴스 호재/악재 분석")
        print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print("═"*60)
        for v in verdicts:
            score_bar = self._score_bar(v.score)
            print(f"\n  {v.emoji} {v.ticker_name}({v.ticker})")
            print(f"     판정: {v.judgment} | 스코어: {v.score:+d}점 {score_bar}")
            print(f"     근거: {v.reason}")
            for kp in v.key_points:
                print(f"       • {kp}")
            print(f"     뉴스: {v.news_count}건 수집")
            if v.news_titles:
                print(f"     주요: {v.news_titles[0][:50]}...")
        print("\n" + "═"*60)

    @staticmethod
    def _score_bar(score: int) -> str:
        """스코어를 시각적 바로 표현 (-100~+100)"""
        filled = abs(score) // 10
        if score >= 0:
            return "▓" * filled + "░" * (10-filled) + " 📈"
        return "░" * (10-filled) + "▓" * filled + " 📉"


# ── 단독 실행 (테스트) ────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

    service = StockNewsService()

    # 테스트 종목
    tickers = ["005930", "000660", "AAPL", "NVDA"]

    print("\n뉴스 수집 + 호재/악재 분석 시작...")
    verdicts = service.get_batch_verdicts(tickers, max_news=6)
    service.print_verdicts(verdicts)

    # 텔레그램 형식 출력
    print("\n[텔레그램 알림 형식 예시]")
    print(verdicts[0].to_telegram())
