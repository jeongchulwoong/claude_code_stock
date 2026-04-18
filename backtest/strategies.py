"""
backtest/strategies.py — 백테스팅용 전략 신호 함수 모음

각 함수: (df, i) → "BUY" | "SELL" | "HOLD"
look-ahead bias 방지를 위해 df.iloc[:i+1] 범위만 참조.
"""

from __future__ import annotations

import pandas as pd


# ── 1. 모멘텀 전략 ────────────────────────────

def momentum_strategy(df: pd.DataFrame, i: int) -> str:
    """
    진입: RSI<35 + 거래량 급등(2배+) + MA5>MA20 + MACD 골든크로스
    청산: RSI>70 (전략 매도)
    """
    row = df.iloc[i]

    # 매수 조건
    if (
        row["rsi"]       < 35 and
        row["vol_ratio"] >= 2.0 and
        row["ma5"]       > row["ma20"] and
        row["macd_cross"]
    ):
        return "BUY"

    # 매도 조건
    if row["rsi"] > 70:
        return "SELL"

    return "HOLD"


# ── 2. 평균 회귀 전략 ─────────────────────────

def mean_reversion_strategy(df: pd.DataFrame, i: int) -> str:
    """
    진입: 볼린저밴드 하단 + RSI<30 + 스토캐스틱K<20
    청산: 볼린저밴드 중단 회귀 (bb_pct > 0.5)
    """
    row = df.iloc[i]

    if (
        row["bb_pct"]   <= 0.1 and
        row["rsi"]       < 30 and
        row["stoch_k"]   < 20
    ):
        return "BUY"

    if row["bb_pct"] > 0.5:
        return "SELL"

    return "HOLD"


# ── 3. 듀얼 모멘텀 전략 ──────────────────────

def dual_momentum_strategy(df: pd.DataFrame, i: int) -> str:
    """
    절대 모멘텀: 12개월 수익률 > 0
    상대 모멘텀: MA5 > MA60 (추세 확인)
    진입: 두 조건 + RSI<60 (과매수 진입 방지)
    """
    row  = df.iloc[i]
    look = 252   # 12개월(약 252거래일)

    if i < look:
        return "HOLD"

    past_price   = df.iloc[i - look]["close"]
    abs_momentum = (row["close"] - past_price) / past_price > 0

    if (
        abs_momentum and
        row["ma5"]  > row["ma60"] and
        row["rsi"]  < 60
    ):
        return "BUY"

    if row["ma5"] < row["ma60"]:
        return "SELL"

    return "HOLD"


# ── 4. 골든크로스 전략 ───────────────────────

def golden_cross_strategy(df: pd.DataFrame, i: int) -> str:
    """
    MA5 / MA20 골든크로스·데드크로스 단순 추종
    """
    row  = df.iloc[i]

    if row["ma_cross"]:              # 골든크로스 발생
        return "BUY"

    if (
        row["ma5"] < row["ma20"] and
        df.iloc[i - 1]["ma5"] >= df.iloc[i - 1]["ma20"]   # 데드크로스
    ):
        return "SELL"

    return "HOLD"


# ── 5. RSI 역추세 전략 ───────────────────────

def rsi_contrarian_strategy(df: pd.DataFrame, i: int) -> str:
    """
    강한 과매도(RSI<25) 구간에서 매수, 회복(RSI>55) 시 매도
    """
    row = df.iloc[i]

    if row["rsi"] < 25:
        return "BUY"

    if row["rsi"] > 55:
        return "SELL"

    return "HOLD"


# ── 6. 컴보 전략 (AI 가중치 기반) ────────────

def combo_strategy(df: pd.DataFrame, i: int) -> str:
    """
    AI 판단 엔진의 가중치 테이블을 그대로 점수화하여 매매.
    점수 >= 55 → BUY, <= -30 → SELL
    """
    row   = df.iloc[i]
    score = 0.0

    # RSI 과매도
    if row["rsi"] < 30:
        score += 25
    elif row["rsi"] < 40:
        score += 10

    # MACD 골든크로스
    if row["macd_cross"]:
        score += 20
    elif row["macd_hist"] > 0:
        score += 5

    # 거래량 급등
    if row["vol_ratio"] >= 3.0:
        score += 20
    elif row["vol_ratio"] >= 2.0:
        score += 10

    # 볼린저밴드 하단
    if row["bb_pct"] <= 0.1:
        score += 10
    elif row["bb_pct"] >= 0.9:
        score -= 10

    # MA 배열
    if row["ma5"] > row["ma20"]:
        score += 10
    else:
        score -= 5

    # 스토캐스틱
    if row["stoch_k"] < 20:
        score += 10
    elif row["stoch_k"] > 80:
        score -= 10

    if score >= 55:
        return "BUY"
    if score <= -30:
        return "SELL"

    return "HOLD"


# ── 전략 레지스트리 ───────────────────────────

STRATEGY_REGISTRY: dict[str, callable] = {
    "momentum":       momentum_strategy,
    "mean_reversion": mean_reversion_strategy,
    "dual_momentum":  dual_momentum_strategy,
    "golden_cross":   golden_cross_strategy,
    "rsi_contrarian": rsi_contrarian_strategy,
    "combo":          combo_strategy,
}


# ── 7. 돌파 전략 ─────────────────────────────

def breakout_strategy(df: pd.DataFrame, i: int) -> str:
    """
    20일 고점 돌파 + 거래량 1.5배 + RSI 45~70 + MACD > 0
    """
    if i < 20:
        return "HOLD"
    row      = df.iloc[i]
    high_20  = df["high"].iloc[i-20:i].max()

    if (
        row["close"]    > high_20 and
        row["vol_ratio"]>= 1.5 and
        45 <= row["rsi"]<= 70 and
        row["macd"]     > 0
    ):
        return "BUY"

    # 청산: 10일 저점 하회
    if i >= 10 and row["close"] < df["low"].iloc[i-10:i].min():
        return "SELL"
    if row["rsi"] > 75:
        return "SELL"
    return "HOLD"


# ── 8. 거래량 급등 전략 ───────────────────────

def volume_surge_strategy(df: pd.DataFrame, i: int) -> str:
    """
    거래량 3배+ + RSI < 60 + 양봉 + BB 하단·중단
    """
    row = df.iloc[i]
    is_bull = row["close"] > row["open"]

    if (
        row["vol_ratio"] >= 3.0 and
        row["rsi"]        < 60 and
        is_bull and
        row["bb_pct"]     < 0.7
    ):
        return "BUY"

    if row["vol_ratio"] < 1.0 and row["rsi"] > 65:
        return "SELL"
    return "HOLD"


# 레지스트리에 추가
STRATEGY_REGISTRY["breakout"]      = breakout_strategy
STRATEGY_REGISTRY["volume_surge"]  = volume_surge_strategy
