# 🤖 AI 기반 국내주식 자동매매 시스템

키움증권 OpenAPI+와 Claude AI를 활용한 국내주식 완전 자동매매 시스템.

> ⚠️ **중요**: 국내주식 자동매매는 **Windows 전용**입니다. (키움 OpenAPI+ 제약)

---

## 📐 전체 아키텍처

```
┌─────────────────────────────────────────────────────┐
│      고도화 AI 판단 엔진 (Claude API)                  │
│  멀티 타임프레임(일/주/월) + 뉴스 감성 + 캘리브레이션    │
└───────────────┬──────────────────┬──────────────────┘
                │                  │
         [국내주식]            [해외주식]
      키움 OpenAPI+        Finnhub + Alpha Vantage
      전략 필터 → 주문       신호 → 텔레그램 알림
                │
     ┌──────────┴──────────┐
     │  웹 모니터링 대시보드  │  ← http://localhost:5000
     │  Flask + Chart.js    │
     └──────────────────────┘
                │
     ┌──────────┴──────────┐
     │   백테스팅 엔진       │
     │  6전략 + 최적화       │
     └──────────────────────┘
```

---

## 📁 프로젝트 구조

```
kiwoom-auto-trader/
├── core/
│   ├── kiwoom_api.py           # 키움 API + Mock
│   ├── data_collector.py       # 시세·기술지표 수집
│   ├── ai_judge.py             # AI 판단 기본
│   ├── ai_judge_advanced.py    # AI 고도화 (멀티TF + 감성)
│   ├── order_manager.py        # 주문 실행 + SQLite
│   ├── risk_manager.py         # 4단계 안전장치
│   └── telegram_bot.py         # 텔레그램 알림
├── strategies/
│   ├── momentum.py             # 모멘텀 전략
│   └── mean_reversion.py       # 평균 회귀 전략
├── backtest/
│   ├── data_loader.py          # yfinance + 지표 계산
│   ├── engine.py               # 이벤트 기반 시뮬레이터
│   ├── strategies.py           # 6가지 전략 함수
│   ├── optimizer.py            # 그리드 서치 최적화
│   ├── report.py               # HTML 리포트 + CSV
│   └── run_backtest.py         # CLI 실행
├── foreign/
│   ├── api_client.py           # Finnhub + Alpha Vantage
│   ├── signal_engine.py        # 해외주식 AI 신호 + 텔레그램
│   └── scheduler.py            # 미국 시장 스케줄러
├── dashboard/
│   ├── app.py                  # Flask 서버
│   ├── db_reader.py            # DB 조회
│   └── templates/index.html    # 대시보드 UI
├── tests/
│   └── paper_trading.py        # 페이퍼 트레이딩
├── config.py                   # 전역 설정 (리스크 파라미터)
├── main.py                     # 진입점
└── requirements.txt
```

---

## 🚀 실행 방법

```bash
# 설치
pip install -r requirements.txt

# 국내주식 자동매매 (페이퍼)
python main.py

# 웹 대시보드
python dashboard/app.py          # → http://localhost:5000

# 해외주식 신호 발송
python foreign/scheduler.py --once
python foreign/scheduler.py --interval 30

# 백테스팅
python backtest/run_backtest.py --tickers 005930 000660
python backtest/run_backtest.py --tickers 005930 --optimize
```

---

## 🛡️ 리스크 파라미터 (`config.py`)

```python
RISK_CONFIG = {
    "max_positions":         5,
    "max_invest_per_trade":  500_000,   # 50만원
    "stop_loss_pct":        -0.03,      # -3%
    "take_profit_pct":       0.06,      # +6%
    "daily_loss_limit":     -200_000,   # -20만원
    "min_confidence":        70,        # AI 신뢰도 최소
}
```

---

## 🤖 AI 판단 고도화

| 타임프레임 | 지표 | 역할 |
|---|---|---|
| 일봉 (단기) | RSI, MACD, BB, MA, 거래량 | 진입 타이밍 |
| 주봉 (중기) | RSI, MACD, MA 추세 | 추세 확인 |
| 월봉 (장기) | RSI, MA3/12 | 대세 방향 |

- **시장 국면 분류**: bull / bear / sideways
- **타임프레임 정렬**: STRONG / MIXED / WEAK
- **뉴스 감성 통합**: Finnhub 뉴스 키워드 분석
- **신뢰도 캘리브레이션**: 과거 적중률 기반 자동 보정

---

## 📊 백테스팅 전략

| 전략 | 핵심 조건 |
|---|---|
| `momentum` | RSI<35 + 거래량2배 + MACD크로스 |
| `mean_reversion` | BB하단 + RSI<30 + 스토캐스틱<20 |
| `dual_momentum` | 12개월 절대모멘텀 + MA추세 |
| `golden_cross` | MA5/20 골든크로스 |
| `rsi_contrarian` | RSI<25 역추세 |
| `combo` | AI 가중치 점수화 |

---

## 🔄 개발 단계

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | 키움 API + 전체 모듈 구조 | ✅ |
| 2 | 백테스팅 엔진 + 최적화 | ✅ |
| 3 | 웹 모니터링 대시보드 | ✅ |
| 4 | 해외주식 파이프라인 | ✅ |
| 5 | AI 판단 고도화 | ✅ |
| 6 | 실거래 전환 + 검증 | 🔜 |

---

> ⚠️ 페이퍼 트레이딩 2주 이상 검증 후 실거래 전환 필수
