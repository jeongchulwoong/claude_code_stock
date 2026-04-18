"""
Real-time stock screener: domestic + foreign large-caps
Usage:
  python scripts/fetch_real_stocks.py          # one-time
  python scripts/fetch_real_stocks.py --watch  # loop every 30 min
  python scripts/fetch_real_stocks.py --watch --interval 60
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

try:
    import FinanceDataReader as fdr
    _FDR_OK = True
except ImportError:
    _FDR_OK = False

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH
from stock_universe import ALL  # name → ticker dict

def calc_rsi(series: pd.Series, period=14) -> float:
    delta = series.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - 100 / (1 + rs)
    v = rsi.iloc[-1] if not rsi.empty else 50.0
    return round(float(v), 1)

def calc_score(rsi, vol_ratio, macd_cross, price_vs_ma20, price_vs_ma60):
    score = 0.0
    reasons = []

    # RSI
    if rsi < 30:
        score += 35; reasons.append(f"RSI oversold({rsi:.0f})")
    elif rsi < 40:
        score += 20; reasons.append(f"RSI low({rsi:.0f})")
    elif rsi < 50:
        score += 10; reasons.append(f"RSI neutral-low({rsi:.0f})")

    # Volume surge
    if vol_ratio > 3.0:
        score += 30; reasons.append(f"Vol surge({vol_ratio:.1f}x)")
    elif vol_ratio > 2.0:
        score += 20; reasons.append(f"Vol up({vol_ratio:.1f}x)")
    elif vol_ratio > 1.5:
        score += 10; reasons.append(f"Vol inc({vol_ratio:.1f}x)")

    # MACD golden cross
    if macd_cross:
        score += 20; reasons.append("MACD cross")

    # Price vs MA20
    if price_vs_ma20 < -0.08:
        score += 25; reasons.append(f"Below MA20({price_vs_ma20*100:.1f}%)")
    elif price_vs_ma20 < -0.03:
        score += 15; reasons.append(f"Below MA20({price_vs_ma20*100:.1f}%)")
    elif price_vs_ma20 < 0:
        score += 5;  reasons.append(f"Near MA20({price_vs_ma20*100:.1f}%)")

    # Price vs MA60 (중장기 저평가)
    if price_vs_ma60 < -0.10:
        score += 20; reasons.append(f"Below MA60({price_vs_ma60*100:.1f}%)")
    elif price_vs_ma60 < -0.05:
        score += 10; reasons.append(f"Near MA60({price_vs_ma60*100:.1f}%)")

    return round(score, 1), reasons

def _get_history_kr(code: str) -> pd.DataFrame | None:
    """한국 종목 코드(숫자 6자리)의 6개월 일봉 DataFrame 반환 (FDR 우선)"""
    if _FDR_OK:
        try:
            df = fdr.DataReader(code, pd.Timestamp.today() - pd.Timedelta(days=200))
            if df is not None and len(df) >= 20:
                df = df.rename(columns=str.capitalize)
                return df
        except Exception:
            pass
    return None


def fetch_and_score(ticker: str, name: str):
    try:
        # 한국 종목: .KS / .KQ 접미사 → 6자리 코드
        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
        code  = ticker.split(".")[0] if is_kr else None

        df = None
        if is_kr:
            df = _get_history_kr(code)

        if df is None:
            df = yf.download(ticker, period="6mo", interval="1d",
                             progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            return None

        close  = df["Close"].squeeze()
        volume = df["Volume"].squeeze()

        # 현재가: Google Finance → yfinance fast_info → 종가 순 fallback
        price = None
        try:
            from core.price_fetcher import get_current_price
            gf = get_current_price(ticker)
            if gf and gf > 0:
                price = float(gf)
        except Exception:
            pass
        if not price:
            try:
                last = yf.Ticker(ticker).fast_info.last_price
                if last and not pd.isna(last):
                    price = float(last)
            except Exception:
                pass
        if not price:
            price = float(close.iloc[-1])

        rsi      = calc_rsi(close)

        vol_avg   = float(volume.iloc[-20:-1].mean())
        vol_today = float(volume.iloc[-1])
        vol_ratio = round(vol_today / vol_avg, 2) if vol_avg > 0 else 1.0

        exp12 = close.ewm(span=12, adjust=False).mean()
        exp26 = close.ewm(span=26, adjust=False).mean()
        macd   = exp12 - exp26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_cross = bool(
            macd.iloc[-1] > signal.iloc[-1] and
            macd.iloc[-2] <= signal.iloc[-2]
        )

        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        price_vs_ma20 = (price - ma20) / ma20
        price_vs_ma60 = (price - ma60) / ma60

        score, reasons = calc_score(rsi, vol_ratio, macd_cross,
                                    price_vs_ma20, price_vs_ma60)

        return {
            "ticker":    ticker,
            "name":      name,
            "price":     round(price, 2),
            "score":     score,
            "reasons":   ", ".join(reasons) if reasons else "Neutral",
            "rsi":       rsi,
            "vol_ratio": vol_ratio,
        }
    except Exception as e:
        print(f"  [SKIP] {ticker}: {e}")
        return None

def run_once():
    all_stocks = [(ticker, name) for name, ticker in ALL.items()]
    run_date   = datetime.now().strftime("%Y-%m-%d")
    now        = datetime.now().isoformat()
    ts         = datetime.now().strftime("%H:%M:%S")

    print(f"\n[{ts}] Scanning {len(all_stocks)} stocks...")

    results = []
    for i, (ticker, name) in enumerate(all_stocks, 1):
        print(f"  [{i:3d}/{len(all_stocks)}] {name:<28} ({ticker})", end=" ", flush=True)
        r = fetch_and_score(ticker, name)
        if r:
            results.append(r)
            print(f"score:{r['score']:5.1f}  RSI:{r['rsi']:5.1f}  vol:{r['vol_ratio']:.1f}x")
        else:
            print("no data")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM screener_results WHERE run_date = ?", (run_date,))
    for r in results:
        conn.execute(
            "INSERT INTO screener_results "
            "(run_date,ticker,name,price,score,reasons,screened_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_date, r["ticker"], r["name"], round(r["price"], 2),
             r["score"], r["reasons"], now)
        )
    conn.commit()
    conn.close()

    results.sort(key=lambda x: -x["score"])
    print("\n" + "="*65)
    print(f"  {len(results)} stocks saved  |  Top buy candidates:")
    print(f"  {'#':<4} {'Name':<28} {'Score':>6} {'Price':>12} {'RSI':>6} {'Vol':>5}")
    print("  " + "-"*63)
    for i, r in enumerate(results[:15], 1):
        flag = "**" if r["score"] >= 40 else ("* " if r["score"] >= 20 else "  ")
        print(f"  {flag}{i:<3} {r['name']:<28} {r['score']:>6.1f} "
              f"{r['price']:>12,.2f} {r['rsi']:>6.1f} {r['vol_ratio']:>4.1f}x")
    print("="*65)
    print(f"  Dashboard -> http://localhost:5000/advanced")
    return len(results)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch",    action="store_true",
                        help="Run continuously")
    parser.add_argument("--interval", type=int, default=30,
                        help="Interval in minutes (default: 30)")
    args = parser.parse_args()

    if args.watch:
        print(f"Watch mode: scanning every {args.interval} min. Ctrl+C to stop.")
        while True:
            try:
                run_once()
                next_run = datetime.now().strftime("%H:%M")
                print(f"  Next scan in {args.interval} min (at approx "
                      f"{int(next_run[:2])*60+int(next_run[3:])+args.interval} min from midnight)")
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print("\nStopped.")
                break
    else:
        run_once()

if __name__ == "__main__":
    main()
