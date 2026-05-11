"""
scripts/build_daytrade_ml_dataset.py — 분단타 ML 학습용 CSV 빌더 (offline only).

본 스크립트는 모델 학습을 하지 않는다. 학습 input 이 될 (features + label) row 들을
CSV 로 모은다. main loop / dashboard / broker / order 흐름과 완전히 독립.

Track 정책:
  --track base      (기본) : 결측 microstructure 허용. has_microstructure_data flag 로
                             모델이 결측 케이스를 분리 학습할 수 있도록 한다. 전체 history
                             재사용 가능.
  --track mstruct          : 호가/체결 관측 충분한 후보만 채택. --from-date 이후 +
                             ticker-session 단위 microstructure presence rate 가
                             --mstruct-coverage-min 이상인 경우만 출력.

Label 정책:
  --label-policy fixed  (기본): --upper-pct / --lower-pct / --horizon-minutes 고정.
  --label-policy atr           : ATR multiple. --tp-atr-mult / --sl-atr-mult /
                                 --horizon-minutes / --cost-pct.
                                 (입력 CSV 의 atr_pct 컬럼이 양수여야 한다.)

입력 CSV 형식 (최소):
  ticker, timestamp, session_date, minute_offset, open, high, low, close, volume
  (선택) ma5, ma20, ma120, rsi, volume_ratio, atr_pct, spread_pct,
         rise_1m_pct, rise_3m_pct,
         bid_price, ask_price, bid_qty, ask_qty,
         bid_ask_ratio, queue_imbalance, microprice_gap_pct

출력 CSV 형식:
  daytrade_v1 schema 의 feature 컬럼들 + has_microstructure_data + atr_bucket +
  label_int + label_hit + return_pct + net_return_pct + cost_pct + holding_minutes +
  upper_pct_used + lower_pct_used + label_policy + label_horizon_minutes

사용:
  # base 트랙 + 고정 라벨 (기존 호환)
  python scripts/build_daytrade_ml_dataset.py \\
    --input  some/minute_bars.csv \\
    --output db/ml/daytrade_dataset_v1.csv \\
    --upper-pct 0.008 --lower-pct 0.005 --horizon-minutes 30

  # mstruct 트랙 + ATR 라벨 (권장)
  python scripts/build_daytrade_ml_dataset.py \\
    --input some/minute_bars.csv \\
    --output db/ml/daytrade_mstruct_atr.csv \\
    --track mstruct --from-date 2026-05-06 --mstruct-coverage-min 0.5 \\
    --label-policy atr --tp-atr-mult 1.5 --sl-atr-mult 1.0 \\
    --horizon-minutes 30 --cost-pct 0.4
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.daytrade_labeling import (   # noqa: E402
    TripleBarrierError,
    atr_bucket,
    label_triple_barrier,
    label_triple_barrier_atr,
)
from core.daytrade_ml_features import FEATURE_VERSION                         # noqa: E402
from core.daytrade_ml_schema import has_microstructure_data                   # noqa: E402
from core.ma120_slope import (                                                # noqa: E402
    classify_slope,
    compute_ma120_slope_pct,
    normalize_distance_by_atr,
)

# MA120 slope lookback (분 단위). 라이브 분봉 5분 lookback 기본.
MA120_SLOPE_LOOKBACK_MINUTES_DEFAULT = 5

LABEL_POLICY_FIXED = "fixed"
LABEL_POLICY_ATR   = "atr"

TRACK_BASE = "base"
TRACK_MSTRUCT = "mstruct"


def _read_minute_rows(input_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    "ticker":        str(r.get("ticker", "")).strip(),
                    "timestamp":     str(r.get("timestamp", "")).strip(),
                    "session_date":  str(r.get("session_date", "")).strip(),
                    "minute_offset": float(r.get("minute_offset", "0") or 0),
                    "open":          float(r.get("open",  "0") or 0),
                    "high":          float(r.get("high",  "0") or 0),
                    "low":           float(r.get("low",   "0") or 0),
                    "close":         float(r.get("close", "0") or 0),
                    "volume":        float(r.get("volume", "0") or 0),
                    # 선택 컬럼 — 있으면 살림.
                    "ma5":   float(r.get("ma5",   "0") or 0),
                    "ma20":  float(r.get("ma20",  "0") or 0),
                    "ma120": float(r.get("ma120", "0") or 0),
                    "rsi":         float(r.get("rsi",          "0") or 0),
                    "volume_ratio": float(r.get("volume_ratio", "1") or 1),
                    "atr_pct":     float(r.get("atr_pct",      "0") or 0),
                    "spread_pct":  float(r.get("spread_pct",   "0") or 0),
                    "rise_1m_pct": float(r.get("rise_1m_pct",  "0") or 0),
                    "rise_3m_pct": float(r.get("rise_3m_pct",  "0") or 0),
                    # microstructure source columns — 없으면 0 (base 트랙은 결측 허용).
                    "bid_price":   float(r.get("bid_price",   "0") or 0),
                    "ask_price":   float(r.get("ask_price",   "0") or 0),
                    "bid_qty":     float(r.get("bid_qty",     "0") or 0),
                    "ask_qty":     float(r.get("ask_qty",     "0") or 0),
                    "bid_ask_ratio":      float(r.get("bid_ask_ratio",      "0") or 0),
                    "queue_imbalance":    float(r.get("queue_imbalance",    "0") or 0),
                    "microprice_gap_pct": float(r.get("microprice_gap_pct", "0") or 0),
                })
            except (TypeError, ValueError):
                continue
    return rows


def _build_feature_row(row: dict[str, Any], *, ma120_then: float = 0.0) -> dict[str, Any]:
    """단일 분봉 row → daytrade_v1 feature schema 와 호환되는 평탄 dict.

    main 의 build_daytrade_entry_features 와 같은 스키마를 흉내내되, 본 스크립트는
    StockSnapshot 객체가 없으니 dict 입력에서 직접 뽑는다. AI/strategy 정보는 0/None.
    `has_microstructure_data` 는 bid_qty/ask_qty/queue_imbalance/microprice_gap_pct
    중 하나라도 비-zero 면 1, 모두 0 이면 0 (라이브 / 학습 일치).

    `ma120_then` 은 lookback ma120 (예: 5분 전). 0/없으면 slope=0 (flat) 으로 처리.
    """
    price = float(row.get("close", 0))
    ma5   = float(row.get("ma5", 0))
    ma20  = float(row.get("ma20", 0))
    ma120 = float(row.get("ma120", 0))
    atr_pct = float(row.get("atr_pct", 0))

    def _gap(a: float, b: float) -> float:
        return ((a - b) / b * 100.0) if b else 0.0

    # MA120 slope/quality (live 빌더와 동일 정의).
    if ma120_then <= 0:
        ma120_then_use = ma120   # lookback 없음 → slope 0
    else:
        ma120_then_use = float(ma120_then)
    slope_pct = compute_ma120_slope_pct(ma120, ma120_then_use)
    quality = classify_slope(slope_pct)
    above = 1 if (price > 0 and ma120 > 0 and price > ma120) else 0
    score = 0
    if above:
        score += 1
    if quality in ("weak_up", "strong_up"):
        score += 1
    if quality == "strong_up" and above:
        score += 1
    # distance ATR 정규화 (signed by above/below).
    atr_value = price * (atr_pct / 100.0) if (price > 0 and atr_pct > 0) else 0.0
    distance_atr_signed = 0.0
    if atr_value > 0 and ma120 > 0 and price > 0:
        raw_dist = normalize_distance_by_atr(price, ma120, atr_value)
        if raw_dist != 999.0:
            distance_atr_signed = raw_dist if price >= ma120 else -raw_dist

    feat: dict[str, Any] = {
        "feature_version":     FEATURE_VERSION,
        "ticker":              row.get("ticker", ""),
        "price":               price,
        "ma5":                 ma5,
        "ma20":                ma20,
        "ma120":               ma120,
        "price_ma20_gap_pct":  _gap(price, ma20),
        "price_ma120_gap_pct": _gap(price, ma120),
        "ma5_ma20_gap_pct":    _gap(ma5, ma20),
        "ma120_slope_pct":           round(slope_pct, 6),
        "ma120_above":               above,
        "ma120_quality_strong_up":   1 if quality == "strong_up"   else 0,
        "ma120_quality_weak_up":     1 if quality == "weak_up"     else 0,
        "ma120_quality_flat":        1 if quality == "flat"        else 0,
        "ma120_quality_weak_down":   1 if quality == "weak_down"   else 0,
        "ma120_quality_strong_down": 1 if quality == "strong_down" else 0,
        "ma120_confluence_score":    score,
        "ma120_distance_atr":        round(distance_atr_signed, 6),
        "rsi":                 float(row.get("rsi", 0)),
        "volume_ratio":        float(row.get("volume_ratio", 1) or 1),
        "atr_pct":             atr_pct,
        "spread_pct":          float(row.get("spread_pct", 0)),
        "rise_1m_pct":         float(row.get("rise_1m_pct", 0)),
        "rise_3m_pct":         float(row.get("rise_3m_pct", 0)),
        "strategies_passed":   0,
        "strategies_required": 1,
        "strategy_pass_ratio": 0.0,
        "fast_score":          0,
        "ai_confidence":       0,
        "ai_news_blocked":     False,
        "ai_is_executable":    False,
        "ai_news_score":       0,
        "minutes_since_open":  int(row.get("minute_offset", 0)),
        "bid_price":           float(row.get("bid_price", 0)),
        "ask_price":           float(row.get("ask_price", 0)),
        "bid_qty":             float(row.get("bid_qty", 0)),
        "ask_qty":             float(row.get("ask_qty", 0)),
        "bid_ask_ratio":       float(row.get("bid_ask_ratio", 0)),
        "queue_imbalance":     float(row.get("queue_imbalance", 0)),
        "microprice_gap_pct":  float(row.get("microprice_gap_pct", 0)),
    }
    feat["has_microstructure_data"] = 1 if has_microstructure_data(feat) else 0
    feat["atr_bucket"] = atr_bucket(feat["atr_pct"])
    return feat


def _ma120_then_for_row(
    sorted_rows: list[dict[str, Any]],
    i: int,
    *,
    lookback_minutes: int,
) -> float:
    """sorted_rows[i] 의 lookback_minutes 이전 ma120 값을 반환.

    분봉 minute_offset 기준 가장 가까운 (현재 - lookback) 이하 row 의 ma120 사용.
    찾을 수 없으면 0.0 (호출자가 flat 으로 해석).
    """
    if i <= 0 or lookback_minutes <= 0:
        return 0.0
    cur_off = float(sorted_rows[i].get("minute_offset", i))
    target_off = cur_off - float(lookback_minutes)
    # 역방향 스캔으로 가장 가까운 ≤ target_off row 찾기.
    for j in range(i - 1, -1, -1):
        off = float(sorted_rows[j].get("minute_offset", j))
        if off <= target_off:
            ma_then = float(sorted_rows[j].get("ma120", 0) or 0)
            return ma_then if ma_then > 0 else 0.0
    return 0.0


def _filter_by_from_date(rows: list[dict[str, Any]], from_date: str | None) -> list[dict[str, Any]]:
    if not from_date:
        return rows
    out = []
    for r in rows:
        sd = str(r.get("session_date", "")).strip()
        if not sd:
            # session_date 누락 → timestamp 앞 10자
            sd = str(r.get("timestamp", ""))[:10]
        if sd and sd >= from_date:
            out.append(r)
    return out


def _filter_mstruct_coverage(
    rows: list[dict[str, Any]],
    *,
    coverage_min: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ticker-session 단위 mstruct presence rate 가 coverage_min 이상인 그룹만 채택.

    presence rate = (bid_qty/ask_qty/queue_imbalance/microprice_gap_pct 중 하나라도
    비-zero 인 row 수) / (그룹 row 수). per-row 가 아니라 ticker-session 단위로 거른다 —
    개별 row 결측은 모델이 has_microstructure_data flag 로 학습하면 됨.
    """
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        ticker = str(r.get("ticker", ""))
        session = str(r.get("session_date", "")) or str(r.get("timestamp", ""))[:10]
        by_group.setdefault((ticker, session), []).append(r)

    kept: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "groups_total": len(by_group),
        "groups_kept": 0,
        "groups_dropped": 0,
        "rows_total": len(rows),
        "rows_kept": 0,
    }
    for grp_rows in by_group.values():
        if not grp_rows:
            continue
        present = 0
        for r in grp_rows:
            f = _build_feature_row(r)   # has_microstructure_data 계산 재사용
            if int(f.get("has_microstructure_data", 0)) == 1:
                present += 1
        rate = present / len(grp_rows)
        if rate >= coverage_min:
            kept.extend(grp_rows)
            stats["groups_kept"] += 1
            stats["rows_kept"] += len(grp_rows)
        else:
            stats["groups_dropped"] += 1
    return kept, stats


def _label_one(
    ticker_rows: list[dict[str, Any]],
    i: int,
    *,
    label_policy: str,
    upper_pct: float,
    lower_pct: float,
    horizon_minutes: int,
    tp_atr_mult: float,
    sl_atr_mult: float,
    cost_pct: float,
):
    if label_policy == LABEL_POLICY_ATR:
        atr_pct = float(ticker_rows[i].get("atr_pct", 0) or 0)
        if atr_pct <= 0:
            return None   # ATR 미상 → 스킵 (label_policy=atr 에선 폐기)
        return label_triple_barrier_atr(
            ticker_rows, entry_index=i,
            atr_pct=atr_pct,
            tp_atr_mult=tp_atr_mult,
            sl_atr_mult=sl_atr_mult,
            horizon_minutes=horizon_minutes,
            cost_pct=cost_pct,
        )
    return label_triple_barrier(
        ticker_rows, entry_index=i,
        upper_pct=upper_pct,
        lower_pct=lower_pct,
        horizon_minutes=horizon_minutes,
        cost_pct=cost_pct,
    )


def build_dataset(
    input_path: Path,
    output_path: Path,
    *,
    upper_pct: float = 0.02,
    lower_pct: float = 0.02,
    horizon_minutes: int = 60,
    track: str = TRACK_BASE,
    label_policy: str = LABEL_POLICY_FIXED,
    from_date: str | None = None,
    mstruct_coverage_min: float = 0.5,
    tp_atr_mult: float = 1.5,
    sl_atr_mult: float = 1.0,
    cost_pct: float = 0.0,
    ma120_slope_lookback_minutes: int = MA120_SLOPE_LOOKBACK_MINUTES_DEFAULT,
) -> int:
    """labeling + feature 빌드. 생성된 row 수 반환.

    Backward-compat: 기존 호출 (track/label_policy 미지정) 은 base + fixed 로 동작.
    """
    rows = _read_minute_rows(input_path)
    if not rows:
        print(f"[warn] no rows read from {input_path}", file=sys.stderr)
        return 0

    # mstruct 트랙 전처리: from_date 필터 + ticker-session coverage 필터.
    if track == TRACK_MSTRUCT:
        rows = _filter_by_from_date(rows, from_date)
        rows, mstats = _filter_mstruct_coverage(rows, coverage_min=mstruct_coverage_min)
        print(
            f"[mstruct] from_date>={from_date} coverage>={mstruct_coverage_min} "
            f"groups kept={mstats['groups_kept']}/{mstats['groups_total']} "
            f"rows kept={mstats['rows_kept']}/{mstats['rows_total']}",
            file=sys.stderr,
        )
        if not rows:
            print("[warn] mstruct filter dropped all rows — nothing to write", file=sys.stderr)
            return 0
    elif from_date:
        # base 트랙도 from_date 가 있으면 적용 (회귀 방지: 미지정 시 무영향).
        rows = _filter_by_from_date(rows, from_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(_build_feature_row(rows[0]).keys()) + [
        "timestamp", "session_date",
        "label_int", "label_hit", "return_pct", "net_return_pct", "cost_pct",
        "upper_pct_used", "lower_pct_used", "label_policy", "label_horizon_minutes",
        "holding_minutes",
    ]
    by_session: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        ticker = str(row.get("ticker", ""))
        session = str(row.get("session_date", "")) or str(row.get("timestamp", ""))[:10]
        by_session.setdefault((ticker, session), []).append(row)

    output_rows: list[dict[str, Any]] = []
    for ticker_rows in by_session.values():
        ticker_rows.sort(key=lambda r: (float(r.get("minute_offset", 0)), str(r.get("timestamp", ""))))
        for i in range(len(ticker_rows)):
            try:
                lbl = _label_one(
                    ticker_rows, i,
                    label_policy=label_policy,
                    upper_pct=upper_pct,
                    lower_pct=lower_pct,
                    horizon_minutes=horizon_minutes,
                    tp_atr_mult=tp_atr_mult,
                    sl_atr_mult=sl_atr_mult,
                    cost_pct=cost_pct,
                )
            except TripleBarrierError:
                continue
            if lbl is None:
                continue
            ma120_then = _ma120_then_for_row(
                ticker_rows, i, lookback_minutes=ma120_slope_lookback_minutes,
            )
            feat = _build_feature_row(ticker_rows[i], ma120_then=ma120_then)
            feat.update({
                "timestamp":       ticker_rows[i].get("timestamp", ""),
                "session_date":    ticker_rows[i].get("session_date", ""),
                "label_int":       lbl.label,
                "label_hit":       lbl.hit,
                "return_pct":      round(lbl.return_pct, 4),
                "net_return_pct":  round(lbl.net_return_pct, 4),
                "cost_pct":        round(lbl.cost_pct, 4),
                "upper_pct_used":  round(lbl.upper_pct, 6),
                "lower_pct_used":  round(lbl.lower_pct, 6),
                "label_policy":    label_policy,
                "label_horizon_minutes": int(horizon_minutes),
                "holding_minutes": lbl.holding_minutes,
            })
            output_rows.append(feat)

    # Persist in global time order so CLI consumers can reproduce the same
    # chronological split without having to know the grouping internals.
    output_rows.sort(key=lambda r: (str(r.get("timestamp", "")), str(r.get("ticker", ""))))
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    return len(output_rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Build daytrade ML training dataset (triple-barrier labels).",
    )
    p.add_argument("--input",  required=True, help="입력 분봉 CSV 경로")
    p.add_argument(
        "--output",
        default="db/ml/daytrade_dataset_v1.csv",
        help="출력 CSV 경로 (default db/ml/daytrade_dataset_v1.csv)",
    )

    # Track 정책.
    p.add_argument(
        "--track", choices=[TRACK_BASE, TRACK_MSTRUCT], default=TRACK_BASE,
        help=(
            "base = 결측 허용 (has_microstructure_data flag 추가, 기본). "
            "mstruct = 호가/체결 관측 충분한 후보만 (--from-date + --mstruct-coverage-min)."
        ),
    )
    p.add_argument(
        "--from-date", default=None,
        help="이 날짜(YYYY-MM-DD) 이후 session 만 사용. mstruct 권장: 2026-05-06.",
    )
    p.add_argument(
        "--mstruct-coverage-min", type=float, default=0.5,
        help="mstruct 트랙: ticker-session 별 microstructure presence rate 하한 (default 0.5).",
    )

    # Label 정책.
    p.add_argument(
        "--label-policy", choices=[LABEL_POLICY_FIXED, LABEL_POLICY_ATR],
        default=LABEL_POLICY_FIXED,
        help="fixed = 고정 pct (기본). atr = ATR multiple.",
    )
    p.add_argument("--upper-pct",       type=float, default=0.02, help="fixed: TP barrier (예: 0.008 = +0.8%%)")
    p.add_argument("--lower-pct",       type=float, default=0.02, help="fixed: SL barrier (양수)")
    p.add_argument("--horizon-minutes", type=int,   default=60,   help="barrier 미도달 종료 분")
    p.add_argument("--tp-atr-mult",     type=float, default=1.5,  help="atr: TP = atr_pct × tp_atr_mult")
    p.add_argument("--sl-atr-mult",     type=float, default=1.0,  help="atr: SL = atr_pct × sl_atr_mult")
    p.add_argument(
        "--cost-pct", type=float, default=0.0,
        help="왕복 비용 (%, 0 이면 비용 미적용). 권장: 0.4 (KOSPI retail).",
    )
    p.add_argument(
        "--ma120-slope-lookback-minutes", type=int, default=MA120_SLOPE_LOOKBACK_MINUTES_DEFAULT,
        help="ma120 slope 계산 lookback (분). 기본 5.",
    )

    args = p.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[error] input file not found: {in_path}", file=sys.stderr)
        return 2

    out_path = Path(args.output)
    n = build_dataset(
        in_path,
        out_path,
        upper_pct=float(args.upper_pct),
        lower_pct=float(args.lower_pct),
        horizon_minutes=int(args.horizon_minutes),
        track=str(args.track),
        label_policy=str(args.label_policy),
        from_date=args.from_date,
        mstruct_coverage_min=float(args.mstruct_coverage_min),
        tp_atr_mult=float(args.tp_atr_mult),
        sl_atr_mult=float(args.sl_atr_mult),
        cost_pct=float(args.cost_pct),
        ma120_slope_lookback_minutes=int(args.ma120_slope_lookback_minutes),
    )
    print(f"wrote {n} rows -> {out_path}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
