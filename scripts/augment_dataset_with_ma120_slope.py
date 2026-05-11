"""
scripts/augment_dataset_with_ma120_slope.py — 기존 event dataset 에 MA120 slope 9개 피처 추가.

본 스크립트는 두 가지 lookback 소스를 차례로 시도한다:
  1) **minute_bars DB join** (--minute-db, 권장): 각 event 의 (ticker, minute_offset - lookback)
     에 해당하는 분봉 row 의 ma120 을 fetch. 학습 / 라이브 양쪽이 동일 정의 되도록 정밀 매칭.
  2) **dataset 내부 lookback** (fallback): 같은 (ticker, session_date) 그룹의 row 들 중
     minute_offset 이 (현재 - lookback) 이하인 가장 가까운 row 의 ma120 사용. event dataset
     은 sparse 라 대부분 매칭 안 됨 (slope=0).

look-ahead bias 방지: ma120_then 은 항상 minute_offset < current 의 row 에서만.
DB 또는 dataset 어느 쪽도 못 찾으면 ma120_then=0 → slope=0 (flat).

사용:
  python scripts/augment_dataset_with_ma120_slope.py \\
    --in  db/ml/<existing_dataset>.csv \\
    --out db/ml/<augmented_dataset>.csv \\
    --minute-db db/kiwoom_minute_bars.db \\
    --lookback-minutes 5
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.ma120_slope import classify_slope, compute_ma120_slope_pct, normalize_distance_by_atr  # noqa: E402


def _load_minute_ma120_lookup(
    minute_db: Path,
    *,
    tickers: list[str],
    session_dates: list[str],
) -> dict[tuple[str, str, int], float]:
    """minute_bars DB 에서 (ticker, session_date, minute_offset) → ma120 lookup table 빌드.

    학습/라이브 분포 일치를 위해 timeframe='1m' 으로만 제한.
    """
    if not minute_db or not minute_db.exists():
        return {}
    conn = sqlite3.connect(str(minute_db))
    try:
        if tickers and session_dates:
            placeholders_t = ",".join("?" * len(tickers))
            placeholders_s = ",".join("?" * len(session_dates))
            q = (
                "SELECT ticker, session_date, minute_offset, ma120 "
                "FROM minute_bars "
                f"WHERE timeframe='1m' AND ticker IN ({placeholders_t}) "
                f"AND session_date IN ({placeholders_s})"
            )
            params = list(tickers) + list(session_dates)
        else:
            q = (
                "SELECT ticker, session_date, minute_offset, ma120 "
                "FROM minute_bars WHERE timeframe='1m'"
            )
            params = []
        cur = conn.execute(q, params)
        out: dict[tuple[str, str, int], float] = {}
        for ticker, session, off, ma120 in cur:
            try:
                ma_val = float(ma120) if ma120 is not None else 0.0
            except (TypeError, ValueError):
                ma_val = 0.0
            try:
                off_int = int(off)
            except (TypeError, ValueError):
                continue
            out[(str(ticker), str(session), off_int)] = ma_val
        return out
    finally:
        conn.close()


def augment(
    in_path: Path,
    out_path: Path,
    *,
    lookback_minutes: int = 5,
    minute_db: Path | None = None,
) -> int:
    df = pd.read_csv(in_path)
    if "ma120" not in df.columns:
        print("[error] dataset missing ma120 column", file=sys.stderr)
        return 0

    df["ma120"] = pd.to_numeric(df["ma120"], errors="coerce").fillna(0.0)
    # event dataset 은 minute_offset 대신 minutes_since_open 을 쓴다. 두 컬럼 모두 시도.
    if "minute_offset" not in df.columns:
        if "minutes_since_open" in df.columns:
            df["minute_offset"] = pd.to_numeric(df["minutes_since_open"], errors="coerce").fillna(0).astype(int)
        else:
            df["minute_offset"] = 0
    df["minute_offset"] = pd.to_numeric(df["minute_offset"], errors="coerce").fillna(0).astype(int)
    if "price" not in df.columns:
        df["price"] = 0.0
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0.0)
    if "atr_pct" not in df.columns:
        df["atr_pct"] = 0.0
    df["atr_pct"] = pd.to_numeric(df["atr_pct"], errors="coerce").fillna(0.0)
    if "ticker" not in df.columns:
        df["ticker"] = ""
    if "session_date" not in df.columns and "timestamp" in df.columns:
        df["session_date"] = df["timestamp"].astype(str).str[:10]

    # 그룹별 정렬 + ma120_then 계산.
    df = df.sort_values(["ticker", "session_date", "minute_offset"]).reset_index(drop=True)

    # DB lookup table 빌드 (있으면).
    db_lookup: dict[tuple[str, str, int], float] = {}
    if minute_db is not None:
        unique_tickers = df["ticker"].astype(str).unique().tolist()
        unique_sessions = df["session_date"].astype(str).unique().tolist()
        print(f"[ma120_slope] loading minute_bars: {len(unique_tickers)} tickers × {len(unique_sessions)} sessions ...", file=sys.stderr)
        # 큰 IN 절은 sqlite 가 limit 가지므로 chunked 로 처리.
        chunk_t = 200
        for ti in range(0, len(unique_tickers), chunk_t):
            sub_tickers = unique_tickers[ti:ti+chunk_t]
            chunk = _load_minute_ma120_lookup(
                minute_db, tickers=sub_tickers, session_dates=unique_sessions,
            )
            db_lookup.update(chunk)
        print(f"[ma120_slope] loaded {len(db_lookup)} (ticker,session,offset) keys", file=sys.stderr)

    ma120_then_arr = []
    slope_pct_arr = []
    above_arr = []
    qsu = []
    qwu = []
    qfl = []
    qwd = []
    qsd = []
    confluence_arr = []
    distance_arr = []

    # 효율: 그룹별로 처리.
    grouped = df.groupby(["ticker", "session_date"], sort=False)
    db_hits = 0
    fallback_hits = 0
    for (ticker_key, session_key), idx in grouped.groups.items():
        sub = df.loc[idx]
        offsets = sub["minute_offset"].values
        ma120_vals = sub["ma120"].values
        prices = sub["price"].values
        atrs = sub["atr_pct"].values
        n = len(sub)
        ticker_str = str(ticker_key)
        session_str = str(session_key)
        for i in range(n):
            cur_off = int(offsets[i])
            target_off = cur_off - lookback_minutes
            ma_then = 0.0
            # 1) DB lookup 우선 — 정확히 (ticker, session, target_off) 매칭.
            if db_lookup:
                v = db_lookup.get((ticker_str, session_str, target_off), 0.0)
                if v and v > 0:
                    ma_then = float(v)
                    db_hits += 1
                else:
                    # 가까운 minute_offset 도 시도 (target_off-2 ~ target_off).
                    for back in range(1, 3):
                        v2 = db_lookup.get((ticker_str, session_str, target_off - back), 0.0)
                        if v2 and v2 > 0:
                            ma_then = float(v2)
                            db_hits += 1
                            break
            # 2) fallback: dataset 내부 lookback (sparse — 거의 매칭 안 됨)
            if ma_then <= 0:
                for j in range(i - 1, -1, -1):
                    if int(offsets[j]) <= target_off:
                        candidate = float(ma120_vals[j])
                        if candidate > 0:
                            ma_then = candidate
                            fallback_hits += 1
                        break
            ma120_then_arr.append(ma_then)

            ma120_now = float(ma120_vals[i])
            ma_then_use = ma_then if ma_then > 0 else ma120_now
            slope = compute_ma120_slope_pct(ma120_now, ma_then_use)
            quality = classify_slope(slope)
            slope_pct_arr.append(round(slope, 6))
            above = 1 if (prices[i] > 0 and ma120_now > 0 and prices[i] > ma120_now) else 0
            above_arr.append(above)
            qsu.append(1 if quality == "strong_up" else 0)
            qwu.append(1 if quality == "weak_up" else 0)
            qfl.append(1 if quality == "flat" else 0)
            qwd.append(1 if quality == "weak_down" else 0)
            qsd.append(1 if quality == "strong_down" else 0)
            score = 0
            if above:
                score += 1
            if quality in ("weak_up", "strong_up"):
                score += 1
            if quality == "strong_up" and above:
                score += 1
            confluence_arr.append(score)

            atr_val = float(prices[i]) * (float(atrs[i]) / 100.0) if (prices[i] > 0 and atrs[i] > 0) else 0.0
            distance = 0.0
            if atr_val > 0 and ma120_now > 0 and prices[i] > 0:
                raw_d = normalize_distance_by_atr(float(prices[i]), ma120_now, atr_val)
                if raw_d != 999.0:
                    distance = raw_d if prices[i] >= ma120_now else -raw_d
            distance_arr.append(round(distance, 6))

    df["ma120_then"] = ma120_then_arr
    df["ma120_slope_pct"] = slope_pct_arr
    df["ma120_above"] = above_arr
    df["ma120_quality_strong_up"] = qsu
    df["ma120_quality_weak_up"] = qwu
    df["ma120_quality_flat"] = qfl
    df["ma120_quality_weak_down"] = qwd
    df["ma120_quality_strong_down"] = qsd
    df["ma120_confluence_score"] = confluence_arr
    df["ma120_distance_atr"] = distance_arr

    # 통계 보고
    rows = len(df)
    has_lookback = int((df["ma120_then"] > 0).sum())
    print(f"rows: {rows} | with_ma120_lookback: {has_lookback} ({has_lookback/rows*100:.1f}%) | db_hits={db_hits} fallback={fallback_hits}")
    quality_dist = {
        "strong_up": int((df["ma120_quality_strong_up"] == 1).sum()),
        "weak_up":   int((df["ma120_quality_weak_up"] == 1).sum()),
        "flat":      int((df["ma120_quality_flat"] == 1).sum()),
        "weak_down": int((df["ma120_quality_weak_down"] == 1).sum()),
        "strong_down": int((df["ma120_quality_strong_down"] == 1).sum()),
    }
    print(f"slope quality distribution: {quality_dist}")
    print(f"above_ma120: {int((df['ma120_above']==1).sum())} / {rows}")

    df.to_csv(out_path, index=False)
    print(f"wrote {rows} rows -> {out_path}")
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    p.add_argument("--out", dest="out_path", required=True)
    p.add_argument("--lookback-minutes", type=int, default=5)
    p.add_argument("--minute-db", default=None,
                   help="권장: db/kiwoom_minute_bars.db (lookback ma120 정밀 매칭)")
    args = p.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"[error] not found: {in_path}", file=sys.stderr)
        return 2
    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    minute_db = Path(args.minute_db) if args.minute_db else None
    if minute_db is not None and not minute_db.exists():
        print(f"[warn] minute_db not found: {minute_db} — fallback only", file=sys.stderr)
        minute_db = None

    n = augment(in_path, out_path, lookback_minutes=int(args.lookback_minutes), minute_db=minute_db)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
