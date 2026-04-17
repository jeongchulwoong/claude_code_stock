"""
strategies/base_strategy.py — 전략 추상 인터페이스

모든 전략은 BaseStrategy를 상속하여 구현한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.data_collector import StockSnapshot
from core.ai_judge import AIVerdict


class BaseStrategy(ABC):
    """
    매매 전략 추상 클래스.

    서브클래스는 should_enter(), should_exit()를 구현해야 한다.
    AI 판단은 AIJudge가 담당하며, 전략은 사전 필터 역할을 한다.
    """

    name: str = "base"

    @abstractmethod
    def should_enter(self, snap: StockSnapshot) -> bool:
        """
        매수 진입 조건 사전 필터.
        True를 반환해야 AI 판단으로 넘어간다.
        """

    @abstractmethod
    def should_exit(self, snap: StockSnapshot, verdict: Optional[AIVerdict] = None) -> bool:
        """
        매도 종료 조건.
        True를 반환하면 매도 주문을 실행한다.
        """

    def describe(self) -> str:
        return f"Strategy: {self.name}"
