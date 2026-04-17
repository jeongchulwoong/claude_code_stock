# 🤖 AI 기반 국내주식 자동매매 시스템

키움증권 OpenAPI+와 Claude AI를 활용한 국내주식 완전 자동매매 시스템.

> ⚠️ **중요**: 이 시스템은 **Windows 전용**입니다. 키움 OpenAPI+가 Windows + Python 32bit 환경만 지원합니다.

---

## 📐 아키텍처

```
┌──────────────────────────────────────────────┐
│           AI 판단 엔진 (Claude API)            │
└───────────────────┬──────────────────────────┘
                    │
         ┌──────────┴──────────┐
    [전략 필터]           [리스크 관리]
  Momentum / MeanReversion   손절·익절·한도
         │                    │
    [데이터 수집]         [주문 실행]
   키움 OpenAPI+         시장가·지정가
         │                    │
    [SQLite DB]         [텔레그램 알림]
```

---

## 📁 프로젝트 구조

```
kiwoom-auto-trader/
├── core/
│   ├── kiwoom_api.py       # 키움 API 연결 + Mock (비Windows)
│   ├── data_collector.py   # 시세·차트·기술지표 수집
│   ├── ai_judge.py         # Claude API 매수·매도 판단
│   ├── order_manager.py    # 주문 실행 + DB 저장
│   ├── risk_manager.py     # 리스크 파라미터 검증
│   └── telegram_bot.py     # 텔레그램 알림
├── strategies/
│   ├── base_strategy.py    # 전략 추상 인터페이스
│   ├── momentum.py         # 모멘텀 전략
│   └── mean_reversion.py   # 평균 회귀 전략
├── tests/
│   └── paper_trading.py    # 페이퍼 트레이딩 시뮬레이터
├── db/                     # SQLite 거래 내역 (gitignore)
├── logs/                   # 일별 로그 파일 (gitignore)
├── config.py               # 전역 설정 + 리스크 파라미터
├── main.py                 # 진입점
└── requirements.txt
```

---

## ⚙️ 설치 및 설정

### 1. 환경 준비 (Windows 필수)
```bash
# Python 32bit 설치 확인
python -c "import struct; print(struct.calcsize('P') * 8, 'bit')"
# 반드시 32 출력되어야 함

# 가상환경 생성
python -m venv venv
venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt
pip install PyQt5  # Windows 전용
```

### 2. 환경 변수 설정
```bash
cp .env.example .env
# .env 파일을 열어 실제 값으로 채운다
```

`.env` 파일 내용:
```
KIWOOM_ACCOUNT_NO=1234567890
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TRADING_MODE=paper    # 반드시 paper로 시작!
```

### 3. 키움 OpenAPI+ 준비
- [ ] 키움증권 계좌 개설
- [ ] OpenAPI+ 사용 신청 (영업일 1~2일 소요)
- [ ] **모의투자 계좌 신청 필수** (실거래 전 테스트용)
- [ ] 키움 OpenAPI+ 설치 (32bit)

---

## 🚀 실행

### 페이퍼 트레이딩 (권장 — 먼저 반드시 검증)
```bash
# 단일 스캔 테스트
python tests/paper_trading.py

# 메인 루프 (페이퍼 모드)
python main.py
```

### 실거래 전환 (충분한 페이퍼 검증 후)
```bash
# .env에서 TRADING_MODE=live 로 변경 후
python main.py
# ⚠️ 10초 카운트다운 후 시작됨
```

---

## 🛡️ 리스크 파라미터 (`config.py`)

```python
RISK_CONFIG = {
    "max_positions":         5,       # 동시 보유 최대 종목
    "max_invest_per_trade":  500_000, # 1회 최대 투자금 (50만원)
    "stop_loss_pct":        -0.03,    # 손절 -3%
    "take_profit_pct":       0.06,    # 익절 +6%
    "daily_loss_limit":     -200_000, # 일일 최대 손실 (20만원)
    "min_confidence":        70,      # AI 신뢰도 최소값
}
```

> ⚠️ 리스크 파라미터는 반드시 `config.py`에서만 수정한다.

---

## 🔄 개발 단계 (Phase)

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | 키움 API 연결 + 기본 모듈 구조 | ✅ 완료 |
| 2 | 해외 API 연동 (Finnhub + Alpha Vantage) | 🔜 예정 |
| 3 | AI 판단 고도화 (멀티 타임프레임) | 🔜 예정 |
| 4 | 리스크 관리 고도화 + 페이퍼 테스트 | 🔜 예정 |
| 5 | 실거래 전환 + 모니터링 대시보드 | 🔜 예정 |

---

## 📊 AI 판단 흐름

```
시세 수집 → 기술지표 계산 → 전략 필터 → Claude API 판단 → 리스크 검사 → 주문
```

**AI 판단 지표 가중치**
| 지표 | 가중치 |
|---|---|
| RSI 과매도 (<30) | +25점 |
| MACD 골든크로스 | +20점 |
| 거래량 급등 (3배+) | +20점 |
| 외인·기관 동시 순매수 | +15점 |
| 볼린저밴드 하단 터치 | +10점 |
| 저PER (<10) | +10점 |

신뢰도 **70점 미만** → 자동 HOLD 처리

---

## ⚡ 안전장치 체크 순서

1. 일일 손실 한도 초과? → **전체 거래 중단**
2. 최대 보유 종목 수 초과? → **매수 차단**
3. 1회 투자금 한도 초과? → **수량 조정**
4. AI 신뢰도 기준 미달? → **HOLD 처리**

---

## 📋 역할 분담

| 역할 | 담당 |
|---|---|
| PM / 아키텍트 / 코드 리뷰 | Claude |
| 코드 구현 | Gemini |

---

## ⚠️ 주의사항

- **절대 실거래 모드로 바로 시작하지 말 것** — 페이퍼 트레이딩 2주 이상 검증 필수
- API 키와 계좌번호는 `.env` 파일에만 저장, Git에 절대 커밋 금지
- 키움 API TR 요청 딜레이 준수 (초당 5회 한도)
- 장 외 시간 자동 대기, 장 중에만 주문 실행
