"""
해외주식 Gemini AI 신호 생성 스크립트
실시간 가격 수집 + Gemini AI 판단 → foreign_signals 테이블 저장
미국 주식 24시간 거래 대응 (장외거래 포함)

실행:
    python scripts/generate_foreign_signals_ai.py
    python scripts/generate_foreign_signals_ai.py --limit 50   # 상위 N개만
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, GEMINI_API_KEY
from stock_universe import FOREIGN
from google import genai
from google.genai import types as gtypes

client = genai.Client(api_key=GEMINI_API_KEY)

# 우선순위 티커 (Gemini API 한도 내에서 중요 종목 먼저)
PRIORITY_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
    "AVGO", "AMD", "TSM", "ARM", "INTC", "QCOM", "MU", "AMAT", "LRCX",
    "JPM", "V", "MA", "GS", "BRK-B", "COIN",
    "LLY", "UNH", "ABBV", "AMGN", "REGN",
    "COST", "WMT", "HD", "MCD", "SBUX", "BKNG", "UBER", "ABNB",
    "ADBE", "CRM", "ORCL", "NOW", "PLTR", "CRWD", "DDOG", "NET", "PANW",
    "XOM", "CVX", "NEE",
    "CAT", "GE", "RTX", "LMT", "HON",
    "TMUS", "DIS", "SPOT",
    "NVO", "ASML", "SAP", "AZN",
    "TM", "SONY", "BABA", "PDD", "TCEHY",
    "MELI", "SE", "NU",
]

# FOREIGN에서 전체 티커 목록 (우선순위 제외한 나머지)
_all_foreign_tickers = list(dict.fromkeys(
    PRIORITY_TICKERS + [t for t in FOREIGN.values() if t not in PRIORITY_TICKERS]
))


def init_table():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS foreign_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                action TEXT,
                confidence INTEGER,
                reason TEXT,
                current_price REAL,
                change_pct REAL,
                news_sentiment TEXT,
                generated_at TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_foreign_ticker ON foreign_signals(ticker)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_foreign_time ON foreign_signals(generated_at)")


def save_signal(ticker, action, confidence, reason, price, change_pct, news_sentiment):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO foreign_signals "
            "(ticker, action, confidence, reason, current_price, change_pct, news_sentiment, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, action, confidence, reason, price, change_pct, news_sentiment, datetime.now().isoformat())
        )


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - 100 / (1 + rs)
    return rsi.iloc[-1] if not rsi.empty else 50.0


def ask_gemini(ticker, price, change_pct, ma20, ma60, rsi, vol_ratio, pe_ratio, info):
    company_name = info.get('longName', ticker)
    market_cap = info.get('marketCap', 0) / 1e9

    now_utc = datetime.utcnow()
    is_extended = not (13 <= now_utc.hour < 20)
    market_note = "※ 현재 미국 장외거래(Pre/After Market) 시간대 — 변동성 주의\n" if is_extended else ""

    prompt = f"""{market_note}종목: {ticker} ({company_name})
현재가: ${price:.2f} (전일대비 {change_pct:+.2f}%)
시가총액: ${market_cap:.1f}B

기술적 지표:
- RSI(14): {rsi:.1f}
- MA20: ${ma20:.2f} (현재가 대비 {((price-ma20)/ma20*100):+.1f}%)
- MA60: ${ma60:.2f} (현재가 대비 {((price-ma60)/ma60*100):+.1f}%)
- 거래량 비율: {vol_ratio:.1f}x (20일 평균 대비)
- PER: {pe_ratio:.1f}

다음 형식으로만 답변:
판단: BUY 또는 SELL 또는 HOLD
신뢰도: 0-100
이유: 핵심 이유 한 줄 (80자 이내)"""

    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash-lite-preview-06-17',
            contents=prompt,
            config=gtypes.GenerateContentConfig(temperature=0, max_output_tokens=128),
        )
        text = resp.text.strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        action, confidence, reason = "HOLD", 50, "AI 분석 완료"
        for line in lines:
            if line.startswith("판단:"):
                val = line.split(":", 1)[1].strip().upper()
                if val in ("BUY", "SELL", "HOLD"):
                    action = val
            elif line.startswith("신뢰도:"):
                try:
                    confidence = max(0, min(100, int(line.split(":", 1)[1].strip())))
                except Exception:
                    pass
            elif line.startswith("이유:"):
                reason = line.split(":", 1)[1].strip()[:200]

        return action, confidence, reason

    except Exception as e:
        print(f"  ⚠️ Gemini 오류: {e}")
        if rsi < 30 and price < ma20:
            return "BUY", 65, f"RSI 과매도({rsi:.0f}) + MA20 하회"
        elif rsi > 70 and price > ma20 * 1.05:
            return "SELL", 65, f"RSI 과매수({rsi:.0f}) + MA20 상회"
        return "HOLD", 50, f"중립 (RSI:{rsi:.0f})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="분석 종목 수 제한 (0=전체)")
    args = parser.parse_args()

    target = _all_foreign_tickers[:args.limit] if args.limit > 0 else _all_foreign_tickers
    print(f"🤖 Gemini AI 해외주식 분석 시작... ({len(target)}개 종목)\n")
    init_table()

    now_utc = datetime.utcnow()
    if not (13 <= now_utc.hour < 20):
        print("⚠️  미국 장외거래 시간대 — 신호 참고용\n")

    success_count = 0
    for ticker in target:
        try:
            print(f"  {ticker}...", end=" ", flush=True)

            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            hist = yf_ticker.history(period="60d")

            price = (
                info.get('currentPrice')
                or info.get('regularMarketPrice')
                or info.get('preMarketPrice')
                or info.get('postMarketPrice')
                or 0
            )
            if not price:
                print("❌ 가격 없음")
                continue

            prev_close = info.get('previousClose', price)
            change_pct = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0

            if len(hist) >= 30:
                ma20 = hist['Close'].rolling(20).mean().iloc[-1]
                ma60 = hist['Close'].rolling(60).mean().iloc[-1] if len(hist) >= 60 else ma20
                rsi = calc_rsi(hist['Close'])
                vol_avg = hist['Volume'].rolling(20).mean().iloc[-1]
                vol_ratio = hist['Volume'].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
                pe_ratio = info.get('trailingPE') or 0

                action, confidence, reason = ask_gemini(
                    ticker, price, change_pct, ma20, ma60, rsi, vol_ratio, pe_ratio, info
                )
            else:
                action, confidence, reason = "HOLD", 50, "데이터 부족"

            save_signal(ticker, action, confidence, reason, price, round(change_pct, 2), "Gemini AI")
            print(f"✅ {action}({confidence}점) ${price:.2f}")
            success_count += 1

        except Exception as e:
            print(f"❌ {e}")

    print(f"\n✅ 완료: {success_count}/{len(target)}개")


if __name__ == "__main__":
    main()
