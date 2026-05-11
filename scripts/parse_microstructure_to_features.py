"""
scripts/parse_microstructure_to_features.py — Kiwoom 0D (호가잔량) JSON events 를
분단위 microstructure feature 로 집계.

입력 DB: db/ml/kiwoom_microstructure_l1_minute_recent30_*.db (real_type='0D')

Kiwoom field codes (호가잔량 0D):
  23: 현재가 (signed string)
  41-50: 매도호가 1~10 depth (signed price)
  51-60: 매수호가 1~10 depth (signed price)
  61: 누적거래량
  71-80: 매도호가잔량 1~10 (bid quantities? in Kiwoom 0D context — verify)
  81-90: 매수호가잔량 1~10
  139: 가중평균거래원가
  121-122: 체결거래량

⚠️ Kiwoom 의 호가잔량 (0D) 에서:
  - 41-50: 매도호가 (ask prices), 71-80: 매도잔량 (ask quantities)
  - 51-60: 매수호가 (bid prices), 81-90: 매수잔량 (bid quantities)
  (Kiwoom OpenAPI 표준 문서 기준)

집계 키: (ticker, session_date, minute_offset).
같은 분에 여러 snapshot 있으면 마지막 snapshot 사용 (분 끝 시점 quote book).

출력 컬럼:
  ticker, session_date, minute_offset,
  spread_pct, bid_qty (top-3), ask_qty (top-3),
  queue_imbalance, microprice_gap_pct, depth_imbalance_5,
  bid_levels_active, ask_levels_active

사용:
  python scripts/parse_microstructure_to_features.py \\
    --in  db/ml/kiwoom_microstructure_l1_minute_recent30_20260509_171535.db \\
    --out db/ml/microstructure_features_per_minute_20260511.csv
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402


def _parse_signed(v: str) -> float:
    """Kiwoom 의 signed 가격 문자열 ('+98700', '-2652', ' 0', '0') 을 float 로."""
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_qty(v: str) -> float:
    if v is None:
        return 0.0
    try:
        return float(str(v).strip())
    except ValueError:
        return 0.0


def _parse_event_time(ts: str) -> tuple[str, int]:
    """ISO timestamp → (session_date, minute_offset_from_open).

    09:00 KST 를 분단위 0 으로. 점심 휴장 시간도 그대로 카운트 (간단).
    """
    if not ts:
        return "", 0
    try:
        dt = pd.to_datetime(ts, errors="coerce")
        if pd.isna(dt):
            return "", 0
        session_date = dt.strftime("%Y-%m-%d")
        # minute_offset = (hh - 9) * 60 + mm
        offset = (dt.hour - 9) * 60 + dt.minute
        if offset < 0:
            offset = 0
        return session_date, int(offset)
    except Exception:
        return "", 0


def _build_features(values: dict) -> dict:
    """단일 호가잔량 snapshot → microstructure features.

    필드 매핑 (Kiwoom 0D, 본 DB 실측 기준):
      23: 현재가 (signed)
      41-50: 매도호가 (ask prices, ascending) — 신뢰 가능
      51-60: 매수호가 (bid prices, descending) — 신뢰 가능
      71-80: 깊이별 잔량 (depth quantities, side 정보 부정확) — 절대값 사용
      81-90: 반대쪽 깊이별 잔량 — 본 수집기에서 대부분 0 (수집 누락) → 사용 안 함
    """
    ask_prices = [abs(_parse_signed(values.get(str(k), 0))) for k in range(41, 51)]
    bid_prices = [abs(_parse_signed(values.get(str(k), 0))) for k in range(51, 61)]
    depth_qtys = [abs(_parse_qty(values.get(str(k), 0))) for k in range(71, 81)]
    opposite_qtys = [abs(_parse_qty(values.get(str(k), 0))) for k in range(81, 91)]
    current_price = abs(_parse_signed(values.get("23", 0)))

    bid1 = bid_prices[0] if bid_prices else 0.0
    ask1 = ask_prices[0] if ask_prices else 0.0
    spread = (ask1 - bid1) if (bid1 > 0 and ask1 > 0 and ask1 > bid1) else 0.0
    mid = (ask1 + bid1) / 2.0 if (bid1 > 0 and ask1 > 0) else 0.0
    spread_pct = (spread / mid * 100.0) if mid > 0 else 0.0

    depth_top1 = depth_qtys[0] if depth_qtys else 0.0
    depth_top3 = sum(depth_qtys[:3])
    depth_top5 = sum(depth_qtys[:5])
    depth_top10 = sum(depth_qtys)
    levels_active = sum(1 for q in depth_qtys if q > 0)
    opposite_top3 = sum(opposite_qtys[:3])

    # depth concentration: top-1 비중. 높으면 한 호가에 잔량 집중 (단기 wall).
    depth_concentration = (depth_top1 / depth_top3) if depth_top3 > 0 else 0.0

    # imbalance: 71-80 (한쪽) vs 81-90 (반대편). 반대편이 0 인 경우가 많아 약한 신호.
    imbalance = ((depth_top3 - opposite_top3) / (depth_top3 + opposite_top3)) if (depth_top3 + opposite_top3) > 0 else 0.0

    # tick 가격 변동 (현재가 vs mid)
    price_to_mid_bps = ((current_price - mid) / mid * 10000.0) if mid > 0 and current_price > 0 else 0.0

    # 누적 거래량 (61) 과 거래대금 (139) — 활동성 측정
    cum_volume = _parse_qty(values.get("61", 0))
    weighted_avg_value = _parse_qty(values.get("139", 0))

    return {
        "current_price":      current_price,
        "bid_price":          bid1,
        "ask_price":          ask1,
        "spread_pct":         spread_pct,
        "ms_depth_top1":      depth_top1,
        "ms_depth_top3":      depth_top3,
        "ms_depth_top5":      depth_top5,
        "ms_depth_top10":     depth_top10,
        "ms_depth_concentration": depth_concentration,
        "ms_levels_active":   levels_active,
        "ms_imbalance":       imbalance,
        "ms_price_to_mid_bps": price_to_mid_bps,
        "ms_cum_volume":      cum_volume,
        "ms_weighted_avg_value": weighted_avg_value,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    p.add_argument("--out", dest="out_path", required=True)
    args = p.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"[error] not found: {in_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(in_path))
    cur = conn.execute(
        "SELECT ticker, received_at, real_type, values_json "
        "FROM microstructure_events "
        "WHERE real_type='0D' "
        "ORDER BY received_at ASC"
    )

    # 분단위 집계: 같은 (ticker, session, minute) 의 마지막 snapshot 사용.
    by_key: dict[tuple[str, str, int], dict] = {}
    rows = 0
    skipped = 0
    for ticker, received_at, _, values_json in cur:
        rows += 1
        try:
            values = json.loads(values_json) if values_json else {}
        except Exception:
            skipped += 1
            continue
        session, offset = _parse_event_time(received_at)
        if not session:
            skipped += 1
            continue
        feats = _build_features(values)
        if not (feats["bid_price"] > 0 and feats["ask_price"] > 0):
            skipped += 1
            continue
        feats.update({
            "ticker": str(ticker), "session_date": session, "minute_offset": offset,
            "received_at": received_at,
        })
        by_key[(str(ticker), session, offset)] = feats   # 가장 최근이 덮어씀
    conn.close()

    print(f"parsed {rows} events, skipped {skipped}, unique (ticker,session,minute): {len(by_key)}")

    df = pd.DataFrame(list(by_key.values()))
    df = df.sort_values(["session_date", "ticker", "minute_offset"]).reset_index(drop=True)
    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    # 기초 통계
    print()
    print(f"tickers: {df['ticker'].nunique()}, sessions: {df['session_date'].nunique()}")
    print(f"minute_offset range: {df['minute_offset'].min()} ~ {df['minute_offset'].max()}")
    print(f"avg ms_depth_top3: {df['ms_depth_top3'].mean():.2f}")
    print(f"avg ms_depth_concentration: {df['ms_depth_concentration'].mean():.4f}")
    print(f"avg spread_pct: {df['spread_pct'].mean():.4f}")
    print(f"avg ms_imbalance: {df['ms_imbalance'].mean():.4f}")
    print(f"avg ms_price_to_mid_bps: {df['ms_price_to_mid_bps'].mean():.4f}")
    print(f"\nwrote {len(df)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
