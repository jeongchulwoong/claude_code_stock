"""
core/trailing_stop.py — ATR 기반 단계별 트레일링 스톱 (paper-only research module).

설계 원칙:
  - 순수 함수 + frozen dataclass 결과. side-effect 0건. 어떤 입력에도 raise 안 함.
  - **trading-critical 코드 0 수정.** 호출자가 broker 영향 결정.
    본 모듈은 stop 가격을 계산해 반환할 뿐, 주문/체결을 직접 일으키지 않는다.
  - main loop 진입 결정 영향 0 — 매니페스트 phase_2 통과 + 운영자 명시 결정 후에만
    실제 청산에 사용.

단계 (롱 기준):
  단계 0 (초기)            : entry_price - atr_multiplier_initial * ATR
  단계 1 (BE lockin)       : profit_atr ≥ steps[0] → entry_price (break-even)
  단계 2 (+1 ATR lockin)   : profit_atr ≥ steps[1] → entry_price + (steps[1]-1.0) * ATR
  단계 3 (chandelier)      : profit_atr ≥ steps[2] → HWM - chandelier_atr * ATR

trigger:
  - 롱: 현재 봉의 low ≤ current_stop 면 stop 가격에 청산 (시장가 가정)
  - 단조 증가 보장: current_stop 은 절대 내려가지 않는다 (보호 우선)

숏 미러 (short=True):
  - 단계 0: entry + initial * ATR (entry 위)
  - 단계 1: entry (BE)
  - 단계 2: entry - (steps[1]-1.0) * ATR (entry 아래로 lockin)
  - 단계 3: LWM (low water mark) + chandelier * ATR
  - trigger: high ≥ current_stop
  - 단조 감소 보장: current_stop 은 절대 올라가지 않는다

reference:
  - Tharp 2008 "Trade Your Way" — R-multiple framework
  - Chesler/Lefebvre "Chandelier Exit" — Le Beau adaption

호출 예 (롱 신호 진입 시점):
  >>> stop = TrailingStopState(entry_price=10000.0, entry_atr_value=100.0, is_long=True)
  >>> stop = update_trailing_stop(stop, candle_high=10150, candle_low=10080,
  ...                              steps=(1.0, 2.0, 3.0), chandelier_atr=1.5,
  ...                              initial_atr_mult=1.5)
  >>> stop.current_stop  # break-even lockin (profit_atr=1.5 ≥ steps[0]=1.0)
  10000.0
  >>> is_triggered(stop, candle_low=9900)
  True   # 청산 신호
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

TRAILING_STOP_VERSION = "trailing_stop_v1"


@dataclass(frozen=True)
class TrailingStopState:
    """포지션별 트레일링 스톱 상태.

    immutable (frozen). update 는 새 인스턴스 반환 — 호출자가 상태 관리 책임.

    Fields:
      entry_price       : 진입가 (고정).
      entry_atr_value   : 진입 시점 ATR 의 절대 가격 (예: ATR_pct=1%, price=10000 → atr=100).
                          업데이트 X — 진입 시점에 고정.
      is_long           : True=롱, False=숏.
      high_water_mark   : 진입 이후 최고가 (롱) 또는 최저가 (숏). 초기=entry_price.
      current_stop      : 현재 stop 가격. 초기=entry - initial_atr_mult*ATR (롱).
      stage             : 0/1/2/3 — 현재 진입한 단계 (단조 증가).
      last_update_minute: 마지막 업데이트의 minute_offset (트레이스용, 옵션).
    """
    entry_price: float
    entry_atr_value: float
    is_long: bool = True
    high_water_mark: float = 0.0
    current_stop: float = 0.0
    stage: int = 0
    last_update_minute: int = -1


def init_trailing_stop(
    *,
    entry_price: float,
    entry_atr_value: float,
    is_long: bool = True,
    initial_atr_mult: float = 1.5,
) -> TrailingStopState:
    """진입 시점 초기 trailing stop 상태 생성.

    Returns:
      stage=0, HWM=entry_price, current_stop=초기 stop (롱은 entry-1.5*ATR, 숏은 entry+1.5*ATR)
    """
    ep = _safe_float(entry_price, 0.0)
    atr = _safe_float(entry_atr_value, 0.0)
    mult = _safe_float(initial_atr_mult, 1.5)
    if ep <= 0 or atr <= 0:
        # 유효 ATR 없으면 stop = entry (사실상 BE) — 호출자가 가드.
        initial_stop = ep
    elif is_long:
        initial_stop = ep - mult * atr
    else:
        initial_stop = ep + mult * atr
    return TrailingStopState(
        entry_price=ep,
        entry_atr_value=atr,
        is_long=bool(is_long),
        high_water_mark=ep,
        current_stop=initial_stop,
        stage=0,
        last_update_minute=-1,
    )


def update_trailing_stop(
    state: TrailingStopState,
    *,
    candle_high: float,
    candle_low: float,
    steps: tuple[float, float, float] = (1.0, 2.0, 3.0),
    chandelier_atr: float = 1.5,
    initial_atr_mult: float = 1.5,
    minute_offset: int = -1,
) -> TrailingStopState:
    """단일 캔들의 high/low 로 trailing stop 갱신. 새 state 반환.

    Args:
      state: 직전 TrailingStopState.
      candle_high / candle_low: 현재 캔들의 high/low.
      steps: (BE, +1ATR lock, chandelier) 진입 임계 (ATR 배수 단위).
      chandelier_atr: chandelier 모드에서 HWM 으로부터 N*ATR 아래 stop.
      initial_atr_mult: 초기 stop = entry - mult*ATR (롱).
      minute_offset: 트레이스용 (옵션).

    Returns:
      새 TrailingStopState. current_stop 은 단조 증가 (롱) / 단조 감소 (숏).
    """
    high = _safe_float(candle_high, 0.0)
    low = _safe_float(candle_low, 0.0)
    if state.entry_atr_value <= 0 or state.entry_price <= 0:
        return state   # 유효 ATR 없으면 갱신 불가
    if high <= 0 or low <= 0:
        return state

    atr = state.entry_atr_value
    entry = state.entry_price
    s0 = _safe_float(steps[0], 1.0) if len(steps) > 0 else 1.0
    s1 = _safe_float(steps[1], 2.0) if len(steps) > 1 else 2.0
    s2 = _safe_float(steps[2], 3.0) if len(steps) > 2 else 3.0
    cand = _safe_float(chandelier_atr, 1.5)
    init = _safe_float(initial_atr_mult, 1.5)

    if state.is_long:
        # HWM = max(prev, high)
        hwm = max(state.high_water_mark, high)
        # profit_atr = (high - entry) / atr — high 기준 (가장 유리한 점에서 단계 판정)
        profit_atr = (hwm - entry) / atr if atr > 0 else 0.0
        if profit_atr >= s2:
            new_stage = 3
            new_stop = hwm - cand * atr
        elif profit_atr >= s1:
            new_stage = 2
            new_stop = entry + (s1 - 1.0) * atr   # +1 ATR lockin (s1=2.0 → +1 ATR)
        elif profit_atr >= s0:
            new_stage = 1
            new_stop = entry                       # break-even
        else:
            new_stage = 0
            new_stop = entry - init * atr          # 초기 유지
        # 단조 증가 보장 + stage 도 단조 증가
        merged_stop = max(state.current_stop, new_stop)
        merged_stage = max(state.stage, new_stage)
    else:
        # 숏 미러
        hwm = min(state.high_water_mark, low) if state.high_water_mark > 0 else low
        profit_atr = (entry - hwm) / atr if atr > 0 else 0.0
        if profit_atr >= s2:
            new_stage = 3
            new_stop = hwm + cand * atr
        elif profit_atr >= s1:
            new_stage = 2
            new_stop = entry - (s1 - 1.0) * atr
        elif profit_atr >= s0:
            new_stage = 1
            new_stop = entry
        else:
            new_stage = 0
            new_stop = entry + init * atr
        # 단조 감소 보장
        merged_stop = min(state.current_stop, new_stop) if state.current_stop > 0 else new_stop
        merged_stage = max(state.stage, new_stage)

    return replace(
        state,
        high_water_mark=hwm,
        current_stop=merged_stop,
        stage=merged_stage,
        last_update_minute=int(minute_offset) if minute_offset >= 0 else state.last_update_minute,
    )


def is_triggered(state: TrailingStopState, *, candle_low: float, candle_high: float) -> bool:
    """현재 캔들의 high/low 가 stop 을 건드렸는지.

    롱: low ≤ current_stop → trigger
    숏: high ≥ current_stop → trigger

    같은 캔들에서 update 와 is_triggered 호출 시:
      - update 가 먼저: HWM 갱신 후 stop 갱신, 그 후 trigger 체크 → SL 이후 가격 회복 케이스 보호 X
      - is_triggered 먼저: 직전 stop 으로 판정 → 보수적 (SL 우선)
      - 호출자가 결정. **권장: is_triggered 가 먼저** (intra-bar SL → TP 케이스에서 SL 우선).
    """
    low = _safe_float(candle_low, 0.0)
    high = _safe_float(candle_high, 0.0)
    stop = _safe_float(state.current_stop, 0.0)
    if stop <= 0:
        return False
    if state.is_long:
        return low > 0 and low <= stop
    return high > 0 and high >= stop


def simulate_trade(
    *,
    entry_price: float,
    entry_atr_value: float,
    future_bars: list[dict],
    is_long: bool = True,
    horizon_minutes: int = 30,
    steps: tuple[float, float, float] = (1.0, 2.0, 3.0),
    chandelier_atr: float = 1.5,
    initial_atr_mult: float = 1.5,
    cost_pct: float = 0.4,
) -> dict:
    """진입 시점부터 horizon 까지 분봉 시퀀스 갈아 트레일링 스톱 시뮬.

    Args:
      future_bars: list of dict {minute_offset, high, low, close}.
                   entry 직후 봉부터, horizon_minutes 이내까지.

    Returns:
      dict with:
        exit_reason   : 'trailing_stop' / 'horizon' / 'no_bars'
        exit_minute   : 청산 봉의 minute_offset
        exit_price    : 청산 가격 (stop 가격 또는 horizon close)
        gross_return_pct
        net_return_pct (gross - cost)
        max_stage     : 도달한 최대 단계
        max_profit_atr: 진입 후 최대 profit (ATR 배수)
        bars_held     : 보유 분봉 수
    """
    state = init_trailing_stop(
        entry_price=entry_price,
        entry_atr_value=entry_atr_value,
        is_long=is_long,
        initial_atr_mult=initial_atr_mult,
    )
    if not future_bars or state.entry_atr_value <= 0:
        return {
            "exit_reason": "no_bars" if not future_bars else "invalid_atr",
            "exit_minute": -1, "exit_price": entry_price,
            "gross_return_pct": 0.0, "net_return_pct": -cost_pct,
            "max_stage": 0, "max_profit_atr": 0.0, "bars_held": 0,
        }

    max_profit_atr = 0.0
    bars_held = 0
    for bar in future_bars:
        try:
            off = int(bar.get("minute_offset", -1))
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))
            close = float(bar.get("close", 0))
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue
        bars_held += 1

        # 보수적: trigger 먼저 (같은 봉에서 SL/TP 동시 발생 시 SL 우선)
        if is_triggered(state, candle_low=low, candle_high=high):
            ep = state.current_stop
            if state.is_long:
                ret = (ep - state.entry_price) / state.entry_price * 100.0
            else:
                ret = (state.entry_price - ep) / state.entry_price * 100.0
            return {
                "exit_reason": "trailing_stop",
                "exit_minute": off, "exit_price": ep,
                "gross_return_pct": ret,
                "net_return_pct": ret - cost_pct,
                "max_stage": state.stage,
                "max_profit_atr": max_profit_atr,
                "bars_held": bars_held,
            }

        # 갱신 (HWM + stop)
        state = update_trailing_stop(
            state, candle_high=high, candle_low=low,
            steps=steps, chandelier_atr=chandelier_atr,
            initial_atr_mult=initial_atr_mult, minute_offset=off,
        )
        if state.entry_atr_value > 0:
            cur_profit = ((high if state.is_long else low) - state.entry_price) / state.entry_atr_value
            if state.is_long:
                max_profit_atr = max(max_profit_atr, cur_profit)
            else:
                max_profit_atr = max(max_profit_atr, -cur_profit)

        if bars_held >= horizon_minutes:
            break

    # horizon 종료 — 마지막 봉의 close 로 청산
    if state.is_long:
        ret = (close - state.entry_price) / state.entry_price * 100.0
    else:
        ret = (state.entry_price - close) / state.entry_price * 100.0
    return {
        "exit_reason": "horizon",
        "exit_minute": off, "exit_price": close,
        "gross_return_pct": ret,
        "net_return_pct": ret - cost_pct,
        "max_stage": state.stage,
        "max_profit_atr": max_profit_atr,
        "bars_held": bars_held,
    }


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        n = float(v)
    except Exception:
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n
