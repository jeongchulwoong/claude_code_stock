"""
해외주식 Gemini AI 신호 생성 스크립트
실시간 가격 수집 + Gemini AI 판단 → foreign_signals 테이블 저장

실행:
    python scripts/generate_foreign_signals_ai.py
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Windows 터미널 UTF-8 강제 설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, GEMINI_API_KEY
from stock_universe import FOREIGN
import google.generativeai as genai

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)

def init_table():
    """foreign_signals 테이블 초기화"""
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

def save_signal(ticker: str, action: str, confidence: int, reason: str,
                price: float, change_pct: float, news_sentiment: str):
    """신호를 DB에 저장"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO foreign_signals "
            "(ticker, action, confidence, reason, current_price, change_pct, news_sentiment, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, action, confidence, reason, price, change_pct, news_sentiment, datetime.now().isoformat())
        )

def calc_rsi(series, period=14):
    """RSI 계산"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - 100 / (1 + rs)
    return rsi.iloc[-1] if not rsi.empty else 50.0

def ask_gemini(ticker: str, price: float, change_pct: float,
               ma20: float, ma60: float, rsi: float, vol_ratio: float,
               pe_ratio: float, info: dict) -> tuple[str, int, str]:
    """
    Gemini AI에게 종목 분석 요청
    """
    company_name = info.get('longName', ticker)
    market_cap = info.get('marketCap', 0) / 1e9  # 십억 달러

    prompt = f"""당신은 전문 주식 트레이더입니다. 다음 종목을 분석하고 매수/매도/보유 판단을 내려주세요.

종목: {ticker} ({company_name})
현재가: ${price:.2f} (전일대비 {change_pct:+.2f}%)
시가총액: ${market_cap:.1f}B

기술적 지표:
- RSI(14): {rsi:.1f}
- MA20: ${ma20:.2f} (현재가 대비 {((price-ma20)/ma20*100):+.1f}%)
- MA60: ${ma60:.2f} (현재가 대비 {((price-ma60)/ma60*100):+.1f}%)
- 거래량 비율: {vol_ratio:.1f}x (20일 평균 대비)
- PER: {pe_ratio:.1f}

다음 형식으로 답변해주세요:
판단: BUY 또는 SELL 또는 HOLD
신뢰도: 0-100 사이의 숫자
이유: 한 줄로 핵심 이유 (80자 이내)

예시:
판단: BUY
신뢰도: 75
이유: RSI 과매도 + MA20 하회 + 거래량 급증으로 반등 가능성 높음"""

    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        response = model.generate_content(prompt)
        text = response.text.strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        action = "HOLD"
        confidence = 50
        reason = "AI 분석 완료"

        for line in lines:
            if line.startswith("판단:"):
                action = line.split(":", 1)[1].strip().upper()
            elif line.startswith("신뢰도:"):
                try:
                    confidence = int(line.split(":", 1)[1].strip())
                except:
                    pass
            elif line.startswith("이유:"):
                reason = line.split(":", 1)[1].strip()[:200]

        return action, confidence, reason

    except Exception as e:
        print(f"  ⚠️ Gemini API 오류: {e}")
        # 폴백: 기술적 분석
        if rsi < 30 and price < ma20:
            return "BUY", 65, f"RSI 과매도({rsi:.0f}) + MA20 하회"
        elif rsi > 70 and price > ma20 * 1.05:
            return "SELL", 65, f"RSI 과매수({rsi:.0f}) + MA20 상회"
        else:
            return "HOLD", 50, f"중립 (RSI:{rsi:.0f})"

def main():
    print("🤖 Gemini AI 해외주식 분석 시작...\n")
    init_table()

    # 주요 해외주식 리스트 (50개 주요 종목)
    target_tickers = [
        # 빅테크 (Mag 7 + AI)
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
        "ADBE", "CRM", "ORCL", "NOW", "PLTR", "CRWD",
        # 반도체 (AI 수혜)
        "AVGO", "AMD", "INTC", "QCOM", "MU", "AMAT", "LRCX", "ASML",
        "KLAC", "TSM", "MRVL", "ADI",
        # 금융
        "JPM", "BAC", "GS", "V", "MA", "AXP", "PYPL", "BRK-B",
        # 헬스케어
        "LLY", "UNH", "JNJ", "ABBV", "TMO", "ISRG",
        # 소비재
        "COST", "WMT", "HD", "MCD", "SBUX", "NKE", "KO", "PG",
        # 에너지/산업
        "XOM", "CVX", "CAT", "BA", "GE", "RTX",
        # 중국/아시아
        "BABA", "TCEHY", "TM", "SONY",
    ]

    success_count = 0

    for ticker in target_tickers:
        try:
            print(f"  분석 중: {ticker}...", end=" ", flush=True)

            # yfinance로 데이터 수집
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            hist = yf_ticker.history(period="60d")

            # 현재가
            price = info.get('currentPrice') or info.get('regularMarketPrice') or 0
            if price == 0:
                print("❌ 가격 없음")
                continue

            # 변동률
            prev_close = info.get('previousClose', price)
            change_pct = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0

            # 기술적 지표
            if len(hist) >= 30:
                ma20 = hist['Close'].rolling(20).mean().iloc[-1]
                ma60 = hist['Close'].rolling(60).mean().iloc[-1] if len(hist) >= 60 else ma20
                rsi = calc_rsi(hist['Close'])

                vol_avg = hist['Volume'].rolling(20).mean().iloc[-1]
                vol_current = hist['Volume'].iloc[-1]
                vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0

                pe_ratio = info.get('trailingPE', 0)

                # Gemini AI 판단
                action, confidence, reason = ask_gemini(
                    ticker, price, change_pct, ma20, ma60, rsi, vol_ratio, pe_ratio, info
                )
            else:
                action, confidence = "HOLD", 50
                reason = "데이터 부족 (30일 미만)"

            # DB 저장
            save_signal(
                ticker=ticker,
                action=action,
                confidence=confidence,
                reason=reason,
                price=price,
                change_pct=round(change_pct, 2),
                news_sentiment="Gemini AI"
            )

            print(f"✅ {action} ({confidence}점) ${price:.2f}")
            success_count += 1

        except Exception as e:
            print(f"❌ 실패: {e}")

    print(f"\n✅ 완료: {success_count}/{len(target_tickers)}개 처리")

if __name__ == "__main__":
    main()
