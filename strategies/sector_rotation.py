"""
strategies/sector_rotation.py — 섹터 로테이션 전략

아이디어:
  시장 국면(bull/bear/sideways)에 따라
  유리한 섹터의 종목에 집중한다.

  bull  → 반도체, IT, 자동차 (경기민감)
  bear  → 바이오, 에너지, 금융 (방어적)
  sideways → 고배당, 리츠 (수익 안정)

진입 조건:
  - 현재 시장 국면을 판단
  - 해당 국면의 선호 섹터 종목만 매수 시도
  - 해당 종목 자체 기술지표 추가 필터

청산 조건:
  - 섹터가 불리한 국면으로 전환
  - 개별 종목 손절·익절 (RiskManager 담당)
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict
from strategies.base_strategy import BaseStrategy


# 국면별 선호 섹터 매핑
PHASE_SECTORS = {
    "bull":     {"반도체", "IT", "자동차", "Tech", "EV"},
    "bear":     {"바이오", "에너지", "금융", "Media"},
    "sideways": {"화학", "반도체", "IT", "Tech"},
    "unknown":  set(),   # 모든 섹터 허용
}

# 종목 → 섹터
TICKER_SECTOR = {
    "005930":"반도체","000660":"반도체","035420":"IT","051910":"화학",
    "006400":"화학",  "005380":"자동차","000270":"자동차","068270":"바이오",
    "207940":"바이오","035720":"IT",   "096770":"에너지","066570":"IT",
    "105560":"금융",  "055550":"금융",  "086790":"금융",
    "AAPL":"Tech","MSFT":"Tech","NVDA":"Tech","TSLA":"EV",
    "META":"Tech","AMZN":"Consumer","NFLX":"Media","AMD":"Tech",
}


class SectorRotationStrategy(BaseStrategy):
    """섹터 로테이션 전략"""

    name = "sector_rotation"

    def __init__(self, market_phase: str = "unknown") -> None:
        self._phase = market_phase   # 외부에서 주입

    def set_phase(self, phase: str) -> None:
        """시장 국면 업데이트 (AdvancedAIJudge에서 주기적으로 호출)"""
        if phase != self._phase:
            logger.info("섹터 로테이션: 국면 변경 {} → {}", self._phase, phase)
        self._phase = phase

    def should_enter(self, snap: StockSnapshot) -> bool:
        # 1. 종목의 섹터 확인
        sector = TICKER_SECTOR.get(snap.ticker, "기타")

        # 2. 현재 국면의 선호 섹터 여부
        preferred = PHASE_SECTORS.get(self._phase, set())
        if preferred and sector not in preferred:
            logger.debug(
                "섹터 로테이션 제외 [{}]: {} 섹터는 {} 국면에 비선호",
                snap.ticker, sector, self._phase,
            )
            return False

        # 3. 개별 기술지표 필터 (기본 조건)
        conditions = {
            "RSI < 65":          snap.rsi < 65,
            "거래량 1.2배+":     snap.volume_ratio >= 1.2,
            "MA5 > MA20 or BB": (snap.ma5 >= snap.ma20 or snap.bollinger_position == "lower"),
        }

        passed = all(conditions.values())
        if passed:
            logger.info(
                "섹터 로테이션 진입 [{}]: {} 섹터 ({} 국면 선호)",
                snap.ticker, sector, self._phase,
            )
        return passed

    def should_exit(
        self,
        snap: StockSnapshot,
        verdict: Optional[AIVerdict] = None,
    ) -> bool:
        sector    = TICKER_SECTOR.get(snap.ticker, "기타")
        preferred = PHASE_SECTORS.get(self._phase, set())

        # 국면 전환으로 섹터가 비선호가 되면 청산
        if preferred and sector not in preferred:
            logger.info(
                "섹터 로테이션 청산 [{}]: {} → {} 국면 전환으로 비선호",
                snap.ticker, sector, self._phase,
            )
            return True

        if snap.rsi > 72:
            return True

        if verdict and verdict.action == "SELL":
            return True

        return False
