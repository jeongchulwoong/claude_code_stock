"""
core/order_book_analyzer.py — 호가(Order Book) 분석 모듈

호가 데이터를 분석하여 단기 방향성을 예측한다.

분석 항목:
  - 매수벽(Buy Wall) / 매도벽(Sell Wall) 탐지
  - 호가 불균형 비율 (Order Imbalance)
  - 스프레드 분석
  - 대량 주문 탐지 (Iceberg Order 의심)
  - 최우선 호가 기준 단기 압력 지수
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass
class OrderBookLevel:
    """단일 호가 레벨"""
    price: int
    qty:   int

    @property
    def amount(self) -> int:
        return self.price * self.qty


@dataclass
class OrderBook:
    """호가 스냅샷"""
    ticker:     str
    current:    int                     # 현재가
    asks:       list[OrderBookLevel]    # 매도 호가 (낮은 가격 우선)
    bids:       list[OrderBookLevel]    # 매수 호가 (높은 가격 우선)
    timestamp:  str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class OrderBookAnalysis:
    """호가 분석 결과"""
    ticker:             str
    imbalance:          float   # -1(매도우세) ~ +1(매수우세)
    spread_pct:         float   # 스프레드 %
    buy_wall_price:     Optional[int]    # 매수벽 가격
    sell_wall_price:    Optional[int]    # 매도벽 가격
    pressure:           str     # "BUY_PRESSURE" | "SELL_PRESSURE" | "BALANCED"
    wall_signal:        str     # "SUPPORT" | "RESISTANCE" | "NONE"
    best_bid:           int
    best_ask:           int
    total_bid_amount:   int
    total_ask_amount:   int
    signal_strength:    float   # 0~1 (신호 강도)


class OrderBookAnalyzer:
    """
    키움 API OnReceiveRealData 또는 OPT10004(호가)에서
    수신한 호가 데이터를 분석한다.
    """

    # 벽(Wall) 탐지: 평균 수량의 N배 이상이면 벽으로 간주
    WALL_THRESHOLD = 5.0

    def analyze(self, ob: OrderBook) -> OrderBookAnalysis:
        """호가 스냅샷을 분석하여 OrderBookAnalysis 반환"""
        if not ob.bids or not ob.asks:
            return self._empty_analysis(ob.ticker)

        # 1. 기본 지표
        best_bid = ob.bids[0].price
        best_ask = ob.asks[0].price
        spread   = best_ask - best_bid
        spread_pct = spread / best_bid * 100 if best_bid > 0 else 0.0

        total_bid = sum(l.qty for l in ob.bids)
        total_ask = sum(l.qty for l in ob.asks)
        total_bid_amt = sum(l.amount for l in ob.bids)
        total_ask_amt = sum(l.amount for l in ob.asks)

        # 2. 호가 불균형 (Order Imbalance)
        # +1 = 매수 압력, -1 = 매도 압력
        imbalance = (total_bid - total_ask) / max(total_bid + total_ask, 1)
        imbalance = round(imbalance, 4)

        # 3. 압력 방향
        if imbalance >= 0.15:
            pressure = "BUY_PRESSURE"
        elif imbalance <= -0.15:
            pressure = "SELL_PRESSURE"
        else:
            pressure = "BALANCED"

        # 4. 벽 탐지
        avg_bid_qty = total_bid / len(ob.bids) if ob.bids else 1
        avg_ask_qty = total_ask / len(ob.asks) if ob.asks else 1

        buy_wall  = self._find_wall(ob.bids, avg_bid_qty)   # 지지선
        sell_wall = self._find_wall(ob.asks, avg_ask_qty)   # 저항선

        if buy_wall and sell_wall:
            # 현재가에 더 가까운 벽이 신호
            buy_dist  = abs(ob.current - buy_wall)
            sell_dist = abs(sell_wall - ob.current)
            wall_signal = "SUPPORT" if buy_dist < sell_dist else "RESISTANCE"
        elif buy_wall:
            wall_signal = "SUPPORT"
        elif sell_wall:
            wall_signal = "RESISTANCE"
        else:
            wall_signal = "NONE"

        # 5. 신호 강도 (0~1)
        strength = min(abs(imbalance) * 3, 1.0)

        analysis = OrderBookAnalysis(
            ticker           = ob.ticker,
            imbalance        = imbalance,
            spread_pct       = round(spread_pct, 3),
            buy_wall_price   = buy_wall,
            sell_wall_price  = sell_wall,
            pressure         = pressure,
            wall_signal      = wall_signal,
            best_bid         = best_bid,
            best_ask         = best_ask,
            total_bid_amount = total_bid_amt,
            total_ask_amount = total_ask_amt,
            signal_strength  = round(strength, 3),
        )

        self._log_analysis(analysis)
        return analysis

    def get_ai_context(self, analysis: OrderBookAnalysis) -> str:
        """AI 판단 프롬프트에 추가할 호가 분석 문자열 반환"""
        p_icon = {"BUY_PRESSURE":"📈","SELL_PRESSURE":"📉","BALANCED":"⚖️"}.get(analysis.pressure,"")
        w_icon = {"SUPPORT":"🟢","RESISTANCE":"🔴","NONE":"⚪"}.get(analysis.wall_signal,"")

        lines = [
            f"━━━ 호가 분석 ━━━",
            f"호가 불균형:  {analysis.imbalance:+.3f} ({analysis.pressure}) {p_icon}",
            f"스프레드:     {analysis.spread_pct:.3f}%",
            f"신호 강도:    {analysis.signal_strength:.2f}",
        ]
        if analysis.buy_wall_price:
            lines.append(f"매수벽(지지): {analysis.buy_wall_price:,}원 {w_icon}")
        if analysis.sell_wall_price:
            lines.append(f"매도벽(저항): {analysis.sell_wall_price:,}원 {w_icon}")
        return "\n".join(lines)

    # ── 내부 메서드 ───────────────────────────

    def _find_wall(
        self, levels: list[OrderBookLevel], avg_qty: float
    ) -> Optional[int]:
        """평균 수량의 WALL_THRESHOLD배 이상인 레벨을 벽으로 탐지"""
        for lv in levels:
            if lv.qty >= avg_qty * self.WALL_THRESHOLD:
                return lv.price
        return None

    @staticmethod
    def _empty_analysis(ticker: str) -> OrderBookAnalysis:
        return OrderBookAnalysis(
            ticker=ticker, imbalance=0.0, spread_pct=0.0,
            buy_wall_price=None, sell_wall_price=None,
            pressure="BALANCED", wall_signal="NONE",
            best_bid=0, best_ask=0,
            total_bid_amount=0, total_ask_amount=0,
            signal_strength=0.0,
        )

    @staticmethod
    def _log_analysis(a: OrderBookAnalysis) -> None:
        logger.debug(
            "호가분석 [{}] 불균형:{:+.3f} | {} | 강도:{:.2f} | 스프레드:{:.3f}%",
            a.ticker, a.imbalance, a.pressure, a.signal_strength, a.spread_pct,
        )

    # ── Mock 호가 생성 (테스트용) ─────────────

    @staticmethod
    def mock_order_book(ticker: str, current_price: int) -> OrderBook:
        """테스트용 Mock 호가 데이터 생성"""
        import random
        random.seed(hash(ticker) % 9999)
        p = current_price

        asks = [
            OrderBookLevel(p + i*100, random.randint(100, 3000))
            for i in range(1, 6)
        ]
        bids = [
            OrderBookLevel(p - i*100, random.randint(100, 3000))
            for i in range(1, 6)
        ]
        # 매수벽 하나 심기
        bids[2] = OrderBookLevel(bids[2].price, 18000)

        return OrderBook(ticker=ticker, current=p, asks=asks, bids=bids)
