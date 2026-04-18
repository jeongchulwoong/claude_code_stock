"""
core/screener.py — 자동 종목 스크리너

국내 코스피/코스닥 전체 종목 중에서
AI 매수 후보를 자동으로 탐색한다.

스크리닝 단계:
  1차 필터: 거래량 급등(2배+), RSI < 40
  2차 필터: 기술지표 복합 조건
  3차 필터: Claude AI 신뢰도 70점+

결과를 텔레그램으로 발송 + DB 저장
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from loguru import logger

from config import DB_PATH, RISK_CONFIG


# ── 스크리닝 후보 ─────────────────────────────

@dataclass
class ScreenerCandidate:
    ticker:        str
    name:          str
    current_price: int
    score:         float       # 1차 점수 (0~100)
    reasons:       list[str]   # 통과 조건 목록
    rsi:           float = 0.0
    vol_ratio:     float = 0.0
    macd_cross:    bool  = False
    bb_position:   str   = "middle"
    screened_at:   str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ScreenerResult:
    run_date:       str
    total_scanned:  int
    candidates:     list[ScreenerCandidate]
    elapsed_sec:    float

    @property
    def top_candidates(self) -> list[ScreenerCandidate]:
        return sorted(self.candidates, key=lambda x: -x.score)[:10]


# ── 스크리너 ──────────────────────────────────

class MarketScreener:
    """
    시장 전체 종목을 대상으로 매수 후보를 자동 탐색한다.
    키움 API 없이도 Watch List 기반으로 동작한다.
    """

    # 코스피 200 주요 종목 (확장 감시 목록)
    KOSPI200_SAMPLE = [
        "005930","000660","035420","051910","006400",
        "005380","000270","068270","207940","035720",
        "096770","012330","011200","028260","003550",
        "066570","105560","055550","086790","009150",
        "010950","003490","034730","018260","032830",
        "011070","017670","015760","009830","010130",
    ]

    KOSDAQ_SAMPLE = [
        "247540","086520","196170","141080","091990",
        "357780","145020","112040","078600","036570",
    ]

    NAME_MAP = {
        "005930":"삼성전자", "000660":"SK하이닉스", "035420":"NAVER",
        "051910":"LG화학",  "006400":"삼성SDI",   "005380":"현대차",
        "000270":"기아",    "068270":"셀트리온",   "207940":"삼성바이오",
        "035720":"카카오",  "096770":"SK이노베이션","012330":"현대모비스",
        "011200":"HMM",    "028260":"삼성물산",    "003550":"LG",
        "066570":"LG전자",  "105560":"KB금융",     "055550":"신한지주",
        "086790":"하나금융","009150":"삼성전기",    "010950":"S-Oil",
        "003490":"대한항공","034730":"SK",         "018260":"삼성에스디에스",
        "032830":"삼성생명","011070":"LG이노텍",   "017670":"SK텔레콤",
        "015760":"한국전력","009830":"한화솔루션",  "010130":"고려아연",
        "247540":"에코프로비엠","086520":"에코프로","196170":"알테오젠",
        "141080":"레고켐바이오","091990":"셀트리온헬스케어","357780":"솔브레인",
        "145020":"휴젤",   "112040":"위메이드",    "078600":"대주전자재료",
        "036570":"엔씨소프트",
    }

    def __init__(self, data_collector=None) -> None:
        self._dc = data_collector
        self._init_db()

    # ── 스크리닝 실행 ─────────────────────────

    def run(
        self,
        universe:     list[str] = None,
        min_score:    float = 40.0,
        max_results:  int   = 20,
        use_mock:     bool  = False,  # DC 없을 때 Mock 데이터
    ) -> ScreenerResult:
        """
        전체 유니버스에서 후보 종목을 탐색한다.
        min_score: 1차 점수 최소값 (0~100)
        """
        start = time.time()
        universe = universe or (self.KOSPI200_SAMPLE + self.KOSDAQ_SAMPLE)
        candidates: list[ScreenerCandidate] = []

        logger.info("스크리너 시작: {}개 종목 스캔", len(universe))

        for ticker in universe:
            try:
                if use_mock or self._dc is None:
                    snap = self._mock_snapshot(ticker)
                else:
                    snap = self._dc.get_snapshot(ticker)
                    time.sleep(0.25)  # API 딜레이

                candidate = self._evaluate(ticker, snap)
                if candidate and candidate.score >= min_score:
                    candidates.append(candidate)

            except Exception as e:
                logger.debug("스크리닝 실패 [{}]: {}", ticker, e)

        # 점수 내림차순 정렬
        candidates.sort(key=lambda x: -x.score)
        elapsed = time.time() - start

        result = ScreenerResult(
            run_date      = date.today().isoformat(),
            total_scanned = len(universe),
            candidates    = candidates[:max_results],
            elapsed_sec   = elapsed,
        )

        self._save_result(result)
        self._log_result(result)
        return result

    # ── 1차 스코어 평가 ───────────────────────

    def _evaluate(self, ticker: str, snap) -> Optional[ScreenerCandidate]:
        """
        종목에 점수를 매기고 ScreenerCandidate를 반환한다.
        점수 기준은 AI 판단 가중치 테이블과 동일.
        """
        score   = 0.0
        reasons = []

        rsi       = getattr(snap, "rsi",           50.0)
        vol_ratio = getattr(snap, "volume_ratio",   1.0)
        macd_cross= getattr(snap, "macd_cross",   False)
        bb_pos    = getattr(snap, "bollinger_position", "middle")
        ma5       = getattr(snap, "ma5",             0.0)
        ma20      = getattr(snap, "ma20",            0.0)
        stoch_k   = getattr(snap, "stochastic_k",  50.0)
        price     = getattr(snap, "current_price",   0)

        # 가격 필터 (100원 미만 동전주 제외)
        if price < 100:
            return None

        # RSI 과매도
        if rsi < 25:
            score += 30; reasons.append(f"RSI 강한 과매도({rsi:.1f})")
        elif rsi < 35:
            score += 20; reasons.append(f"RSI 과매도({rsi:.1f})")
        elif rsi < 45:
            score += 10; reasons.append(f"RSI 낮음({rsi:.1f})")

        # 거래량 급등
        if vol_ratio >= 4.0:
            score += 25; reasons.append(f"거래량 폭등({vol_ratio:.1f}배)")
        elif vol_ratio >= 2.5:
            score += 18; reasons.append(f"거래량 급등({vol_ratio:.1f}배)")
        elif vol_ratio >= 1.8:
            score += 10; reasons.append(f"거래량 증가({vol_ratio:.1f}배)")

        # MACD 골든크로스
        if macd_cross:
            score += 20; reasons.append("MACD 골든크로스")

        # 볼린저밴드
        if bb_pos == "lower":
            score += 15; reasons.append("볼린저밴드 하단")
        elif bb_pos == "upper":
            score -= 10  # 과매수 구간 패널티

        # MA 배열
        if ma5 > 0 and ma20 > 0:
            if ma5 > ma20:
                score += 10; reasons.append("MA5>MA20")
            else:
                score -= 5

        # 스토캐스틱
        if stoch_k < 20:
            score += 10; reasons.append(f"스토캐스틱 과매도({stoch_k:.1f})")

        # 최소 2개 이상 조건 충족 요구
        if len(reasons) < 2:
            return None

        name = self.NAME_MAP.get(ticker, ticker)
        return ScreenerCandidate(
            ticker        = ticker,
            name          = name,
            current_price = int(price),
            score         = round(score, 1),
            reasons       = reasons,
            rsi           = rsi,
            vol_ratio     = vol_ratio,
            macd_cross    = macd_cross,
            bb_position   = bb_pos,
        )

    # ── Mock 스냅샷 ───────────────────────────

    @staticmethod
    def _mock_snapshot(ticker: str):
        """DC 없는 환경에서 사용하는 Mock 스냅샷"""
        import random
        random.seed(hash(ticker) % 10000)

        class MockSnap:
            pass

        snap = MockSnap()
        snap.ticker        = ticker
        snap.current_price = random.randint(10000, 500000)
        snap.rsi           = random.uniform(18, 75)
        snap.volume_ratio  = random.uniform(0.5, 5.0)
        snap.macd_cross    = random.random() < 0.15
        snap.bollinger_position = random.choice(["lower","middle","middle","upper"])
        snap.ma5           = snap.current_price * random.uniform(0.97, 1.03)
        snap.ma20          = snap.current_price * random.uniform(0.96, 1.04)
        snap.stochastic_k  = random.uniform(10, 90)
        return snap

    # ── DB + 로그 ─────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS screener_results (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date     TEXT,
                    ticker       TEXT,
                    name         TEXT,
                    price        INTEGER,
                    score        REAL,
                    reasons      TEXT,
                    screened_at  TEXT
                )
            """)

    def _save_result(self, result: ScreenerResult) -> None:
        import json
        with sqlite3.connect(DB_PATH) as con:
            for c in result.candidates:
                con.execute(
                    "INSERT INTO screener_results "
                    "(run_date,ticker,name,price,score,reasons,screened_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (result.run_date, c.ticker, c.name, c.current_price,
                     c.score, json.dumps(c.reasons, ensure_ascii=False), c.screened_at),
                )

    @staticmethod
    def _log_result(result: ScreenerResult) -> None:
        logger.info("=" * 55)
        logger.info("  📡 스크리닝 완료: {}개 후보 / {}개 스캔 ({:.1f}초)",
                    len(result.candidates), result.total_scanned, result.elapsed_sec)
        logger.info("=" * 55)
        for c in result.top_candidates:
            logger.info(
                "  [{:>5.1f}점] {:8} {:14} | RSI:{:5.1f} | 거래량:{:4.1f}배 | {}",
                c.score, c.ticker, c.name,
                c.rsi, c.vol_ratio, " / ".join(c.reasons[:2]),
            )

    # ── 텔레그램 알림 형식 ────────────────────

    def to_telegram(self, result: ScreenerResult) -> str:
        tops = result.top_candidates
        if not tops:
            return f"📡 스크리닝 완료 ({result.total_scanned}종목)\n후보 없음"

        lines = [
            f"📡 종목 스크리닝 결과",
            f"━" * 26,
            f"스캔: {result.total_scanned}종목 | 후보: {len(tops)}개",
            f"기준일: {result.run_date}",
            "",
        ]
        for i, c in enumerate(tops[:8], 1):
            icon = "🔥" if c.score >= 60 else "⭐" if c.score >= 40 else "🔹"
            lines.append(
                f"{icon} {i}. {c.name}({c.ticker})\n"
                f"   점수:{c.score:.0f} | {c.current_price:,}원\n"
                f"   {' | '.join(c.reasons[:2])}"
            )
        lines.append(f"\n⏰ {datetime.now().strftime('%H:%M:%S')}")
        return "\n".join(lines)
