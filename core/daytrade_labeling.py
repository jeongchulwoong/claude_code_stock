"""
core/daytrade_labeling.py — 분단타 학습용 triple-barrier labeling (offline only).

설계 원칙:
  - 순수 함수. side-effect 0건. 시간/네트워크 의존성 0건.
  - 입력은 분단위 OHLC 시퀀스. 진입은 entry_index 의 close 가격으로 가정.
  - 같은 봉에서 TP/SL 동시 도달 시 보수적으로 SL 우선 (실거래 안전 가정).
  - horizon 까지 둘 다 미도달 → label=-1.
  - 본 모듈은 main loop 가 import 하지 않는다 (학습 데이터셋 빌드 전용).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TripleBarrierResult:
    """triple-barrier label.

    label:
      1  → TP barrier 먼저 도달 (또는 동시면 SL 우선이므로 label=0)
      0  → SL barrier 먼저 도달 (혹은 동시 도달)
      -1 → horizon 까지 둘 다 미도달
    hit:
      "tp" / "sl" / "tp_sl_same_bar_sl_priority" / "horizon"
    return_pct:        진입가 기준 raw 청산 수익률 (%) — 비용 차감 X.
    cost_pct:          왕복 거래 비용 가정 (%, 0 이면 비용 모델링 없음).
    net_return_pct:    return_pct - cost_pct (cost-adjusted EV 계산용).
    upper_pct/lower_pct: 실제 사용된 barrier 폭 (%, ATR 기반은 호출 시점에 환산된 값).
    """
    label: int
    hit: str
    exit_index: int
    exit_price: float
    return_pct: float
    holding_minutes: int
    cost_pct: float = 0.0
    net_return_pct: float = 0.0
    upper_pct: float = 0.0
    lower_pct: float = 0.0


class TripleBarrierError(ValueError):
    """입력 검증 실패."""


_HIT_TP                = "tp"
_HIT_SL                = "sl"
_HIT_TP_SL_SAME_BAR    = "tp_sl_same_bar_sl_priority"
_HIT_HORIZON           = "horizon"


def _row_get_float(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        v = row.get(key, default)
        return float(v if v is not None else default)
    except Exception:
        return default


def _row_get_int(row: Mapping[str, Any], key: str, default: int = 0) -> int:
    try:
        v = row.get(key, default)
        return int(v if v is not None else default)
    except Exception:
        return default


def label_triple_barrier(
    rows: Sequence[Mapping[str, Any]],
    entry_index: int,
    *,
    upper_pct: float,
    lower_pct: float,
    horizon_minutes: int,
    cost_pct: float = 0.0,
) -> TripleBarrierResult:
    """
    Triple-barrier labeling.

    Args:
      rows: 분봉 시퀀스. 각 row 는 dict-like (high/low/close/minute_offset 등).
            high/low/close 키만 본 함수에서 본다. minute_offset (분 단위 누적) 가 있으면 사용,
            없으면 인덱스 차이로 대체.
      entry_index: 진입 봉 인덱스 (이 봉의 close 를 entry_price 로 사용).
      upper_pct: TP barrier — entry_price * (1 + upper_pct).
      lower_pct: SL barrier — entry_price * (1 - lower_pct). (양수 값 권장)
      horizon_minutes: barrier 미도달 시 종료 시간 (분).

    Raises:
      TripleBarrierError: rows 비었거나 entry_index 범위 오류.
    """
    if not rows:
        raise TripleBarrierError("rows is empty")
    n = len(rows)
    if entry_index < 0 or entry_index >= n:
        raise TripleBarrierError(f"entry_index out of range: {entry_index} (n={n})")
    if upper_pct <= 0 or lower_pct <= 0:
        raise TripleBarrierError(f"barriers must be positive: upper={upper_pct}, lower={lower_pct}")
    if horizon_minutes <= 0:
        raise TripleBarrierError(f"horizon_minutes must be positive: {horizon_minutes}")

    entry_row = rows[entry_index]
    entry_price = _row_get_float(entry_row, "close", 0.0)
    if entry_price <= 0:
        raise TripleBarrierError(f"entry close must be positive (got {entry_price})")

    tp_level = entry_price * (1.0 + float(upper_pct))
    sl_level = entry_price * (1.0 - float(lower_pct))

    entry_minute = _row_get_float(entry_row, "minute_offset", float(entry_index))
    entry_minute_int = int(entry_minute)

    # entry_index 다음 봉부터 검사. 같은 봉의 high/low 를 봐도 의미 없음 (이미 close 가격 진입).
    last_valid_index = entry_index
    for i in range(entry_index + 1, n):
        row = rows[i]
        high = _row_get_float(row, "high", _row_get_float(row, "close", 0.0))
        low  = _row_get_float(row, "low",  _row_get_float(row, "close", 0.0))

        cur_minute = _row_get_float(row, "minute_offset", float(i))
        held = int(cur_minute) - entry_minute_int
        if held > horizon_minutes:
            break
        last_valid_index = i

        hit_tp = high >= tp_level
        hit_sl = low  <= sl_level

        if hit_tp and hit_sl:
            # 같은 봉에서 둘 다 — SL 우선 (보수적).
            ret = (sl_level - entry_price) / entry_price * 100.0
            return TripleBarrierResult(
                label=0,
                hit=_HIT_TP_SL_SAME_BAR,
                exit_index=i,
                exit_price=sl_level,
                return_pct=ret,
                holding_minutes=max(0, held),
                cost_pct=float(cost_pct),
                net_return_pct=ret - float(cost_pct),
                upper_pct=float(upper_pct),
                lower_pct=float(lower_pct),
            )
        if hit_tp:
            ret = (tp_level - entry_price) / entry_price * 100.0
            return TripleBarrierResult(
                label=1,
                hit=_HIT_TP,
                exit_index=i,
                exit_price=tp_level,
                return_pct=ret,
                holding_minutes=max(0, held),
                cost_pct=float(cost_pct),
                net_return_pct=ret - float(cost_pct),
                upper_pct=float(upper_pct),
                lower_pct=float(lower_pct),
            )
        if hit_sl:
            ret = (sl_level - entry_price) / entry_price * 100.0
            return TripleBarrierResult(
                label=0,
                hit=_HIT_SL,
                exit_index=i,
                exit_price=sl_level,
                return_pct=ret,
                holding_minutes=max(0, held),
                cost_pct=float(cost_pct),
                net_return_pct=ret - float(cost_pct),
                upper_pct=float(upper_pct),
                lower_pct=float(lower_pct),
            )

    # horizon 만료 — label=-1. exit_price 는 마지막 본 봉의 close.
    last_index = last_valid_index
    last_row = rows[last_index]
    last_close = _row_get_float(last_row, "close", entry_price)
    last_minute = _row_get_float(last_row, "minute_offset", float(last_index))
    held = max(0, int(last_minute) - entry_minute_int)
    ret = (last_close - entry_price) / entry_price * 100.0
    return TripleBarrierResult(
        label=-1,
        hit=_HIT_HORIZON,
        exit_index=last_index,
        exit_price=last_close,
        return_pct=ret,
        holding_minutes=held,
        cost_pct=float(cost_pct),
        net_return_pct=ret - float(cost_pct),
        upper_pct=float(upper_pct),
        lower_pct=float(lower_pct),
    )


# ──────────────────────────────────────────────────────────────────
# ATR-multiple barrier (forward_atr label policy)
#
# Rationale:
#   고정 pct 라벨 (e.g. 0.8% / 0.5%) 은 ATR 0.4% 종목엔 너무 멀고 ATR 1.2% 종목엔
#   너무 빡빡하다. ATR 기반 multiple 로 환산하면 종목 변동성에 맞춰 barrier 가 비례.
# ──────────────────────────────────────────────────────────────────

def label_triple_barrier_atr(
    rows: Sequence[Mapping[str, Any]],
    entry_index: int,
    *,
    atr_pct: float,
    tp_atr_mult: float = 1.5,
    sl_atr_mult: float = 1.0,
    horizon_minutes: int = 30,
    cost_pct: float = 0.4,
    min_barrier_pct: float = 0.002,
    max_barrier_pct: float = 0.05,
) -> TripleBarrierResult:
    """ATR multiple 기반 triple-barrier labeling.

    Args:
      atr_pct: 진입 시점 ATR (%) — 예: 0.8 = 0.8%. **양수만 허용**.
      tp_atr_mult: TP barrier = entry * (1 + atr_pct/100 * tp_atr_mult). 기본 1.5.
      sl_atr_mult: SL barrier = entry * (1 - atr_pct/100 * sl_atr_mult). 기본 1.0.
      horizon_minutes: barrier 미도달 종료 분. 기본 30.
      cost_pct: 왕복 거래 비용 (%). 기본 0.4 (KOSPI: 거래세 0.18 + 수수료 0.03 + 슬리피지 ~0.18).
      min_barrier_pct: ATR 변환 후 하한 (분수, 0.002 = 0.2%). 너무 작은 ATR 보호.
      max_barrier_pct: 상한 (0.05 = 5%). 이상치 ATR 보호.

    Returns:
      TripleBarrierResult — label / hit / return_pct / net_return_pct / cost_pct /
      upper_pct / lower_pct 모두 채워진다.

    Raises:
      TripleBarrierError — atr_pct ≤ 0 또는 multiplier ≤ 0.
    """
    if atr_pct is None or float(atr_pct) <= 0:
        raise TripleBarrierError(f"atr_pct must be positive: {atr_pct}")
    if tp_atr_mult <= 0 or sl_atr_mult <= 0:
        raise TripleBarrierError(
            f"barrier multipliers must be positive: tp={tp_atr_mult}, sl={sl_atr_mult}"
        )

    atr_frac = float(atr_pct) / 100.0
    upper_pct = max(min_barrier_pct, min(max_barrier_pct, atr_frac * float(tp_atr_mult)))
    lower_pct = max(min_barrier_pct, min(max_barrier_pct, atr_frac * float(sl_atr_mult)))

    return label_triple_barrier(
        rows,
        entry_index=entry_index,
        upper_pct=upper_pct,
        lower_pct=lower_pct,
        horizon_minutes=int(horizon_minutes),
        cost_pct=float(cost_pct),
    )


def atr_bucket(atr_pct: float) -> str:
    """ATR pct 를 학습/평가 단계에서 동일 정의로 분할.

    카테고리:
      - 'low'    : ATR < 0.5%
      - 'medium' : 0.5% ≤ ATR < 1.0%
      - 'high'   : ATR ≥ 1.0%
      - 'unknown': atr_pct 가 None / NaN / ≤ 0
    """
    try:
        v = float(atr_pct)
    except Exception:
        return "unknown"
    if v != v or v <= 0:
        return "unknown"
    if v < 0.5:
        return "low"
    if v < 1.0:
        return "medium"
    return "high"
