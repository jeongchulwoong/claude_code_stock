"""
해외주식 AI 신호 생성 스크립트
실시간 가격 수집 + AI 판단 → foreign_signals 테이블 저장

실행:
    python scripts/generate_foreign_signals.py
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

from config import DB_PATH
from stock_universe import FOREIGN

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

def main():
    print("🌐 해외주식 신호 생성 시작...")
    init_table()

    # 주요 해외주식 리스트
    target_tickers = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        "AVGO", "AMD", "NFLX", "JPM", "V", "MA", "COST", "WMT"
    ]

    success_count = 0

    for ticker in target_tickers:
        try:
            print(f"  처리 중: {ticker}...", end=" ")

            # yfinance로 가격 수집
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info

            # 현재가
            price = info.get('currentPrice') or info.get('regularMarketPrice') or 0

            # 변동률
            prev_close = info.get('previousClose', price)
            if prev_close > 0:
                change_pct = ((price - prev_close) / prev_close) * 100
            else:
                change_pct = 0.0

            # 기술적 분석 + AI 스타일 판단
            hist = yf_ticker.history(period="60d")
            if len(hist) >= 30:
                ma20 = hist['Close'].rolling(20).mean().iloc[-1]
                ma60 = hist['Close'].rolling(60).mean().iloc[-1] if len(hist) >= 60 else ma20
                rsi = calc_rsi(hist['Close'])

                # 거래량 분석
                vol_avg = hist['Volume'].rolling(20).mean().iloc[-1]
                vol_current = hist['Volume'].iloc[-1]
                vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0

                # MACD
                ema12 = hist['Close'].ewm(span=12).mean().iloc[-1]
                ema26 = hist['Close'].ewm(span=26).mean().iloc[-1]
                macd = ema12 - ema26

                # 종합 AI 판단
                action, confidence, reason = analyze_stock(
                    price, ma20, ma60, rsi, vol_ratio, macd, change_pct, info
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
                news_sentiment="기술적분석"
            )

            print(f"✅ {action} ({confidence}점) ${price:.2f} ({change_pct:+.2f}%)")
            success_count += 1

        except Exception as e:
            print(f"❌ 실패: {e}")
            # 실패해도 기본 데이터 저장
            try:
                yf_ticker = yf.Ticker(ticker)
                price = yf_ticker.info.get('currentPrice', yf_ticker.info.get('regularMarketPrice', 0))
                save_signal(ticker, "HOLD", 0, f"데이터 수집 실패", price, 0.0, "N/A")
            except:
                pass

    print(f"\n✅ 완료: {success_count}/{len(target_tickers)}개 처리")

def analyze_stock(price, ma20, ma60, rsi, vol_ratio, macd, change_pct, info):
    """
    종합 AI 스타일 분석
    - 기술적 지표 + 펀더멘털 + 모멘텀 종합 판단
    """
    score = 0
    reasons = []

    # 1. RSI 분석 (과매도/과매수)
    if rsi < 30:
        score += 25
        reasons.append(f"RSI 과매도({rsi:.0f})")
    elif rsi < 40:
        score += 15
        reasons.append(f"RSI 저평가({rsi:.0f})")
    elif rsi > 70:
        score -= 20
        reasons.append(f"RSI 과매수({rsi:.0f})")
    elif rsi > 60:
        score -= 10
        reasons.append(f"RSI 고평가({rsi:.0f})")

    # 2. 이동평균선 분석
    ma20_diff = (price - ma20) / ma20
    ma60_diff = (price - ma60) / ma60

    if ma20_diff < -0.05:  # MA20 아래 5% 이상
        score += 20
        reasons.append(f"MA20 하회({ma20_diff*100:.1f}%)")
    elif ma20_diff > 0.05:  # MA20 위 5% 이상
        score -= 15
        reasons.append(f"MA20 상회({ma20_diff*100:.1f}%)")

    if ma60_diff < -0.10:  # MA60 아래 10% 이상
        score += 15
        reasons.append(f"MA60 하회({ma60_diff*100:.1f}%)")

    # 3. 골든크로스/데드크로스
    if ma20 > ma60 * 1.02:
        score += 10
        reasons.append("골든크로스")
    elif ma20 < ma60 * 0.98:
        score -= 10
        reasons.append("데드크로스")

    # 4. 거래량 급증
    if vol_ratio > 2.0:
        score += 15
        reasons.append(f"거래량 급증({vol_ratio:.1f}x)")
    elif vol_ratio > 1.5:
        score += 8
        reasons.append(f"거래량 증가({vol_ratio:.1f}x)")

    # 5. MACD
    if macd > 0:
        score += 5
        reasons.append("MACD 상승")
    else:
        score -= 5
        reasons.append("MACD 하락")

    # 6. 모멘텀 (당일 변동률)
    if change_pct > 3:
        score += 10
        reasons.append(f"강한 상승({change_pct:+.1f}%)")
    elif change_pct < -3:
        score -= 10
        reasons.append(f"급락({change_pct:+.1f}%)")

    # 7. 펀더멘털 (PER, PEG)
    pe_ratio = info.get('trailingPE', 0)
    if pe_ratio > 0:
        if pe_ratio < 15:
            score += 10
            reasons.append(f"저PER({pe_ratio:.1f})")
        elif pe_ratio > 40:
            score -= 10
            reasons.append(f"고PER({pe_ratio:.1f})")

    # 최종 판단
    if score >= 50:
        action = "BUY"
        confidence = min(85, 70 + score // 5)
    elif score <= -30:
        action = "SELL"
        confidence = min(80, 60 + abs(score) // 5)
    else:
        action = "HOLD"
        confidence = 50 + abs(score) // 3

    reason = " | ".join(reasons[:4]) if reasons else "중립"

    return action, confidence, reason

def calc_rsi(series, period=14):
    """RSI 계산"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - 100 / (1 + rs)
    return rsi.iloc[-1] if not rsi.empty else 50.0

if __name__ == "__main__":
    main()
