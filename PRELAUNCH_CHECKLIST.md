# 🚀 실전 투입 전 체크리스트 (2026-04-19)

## ✅ 확인 완료 항목

### 1. 단타/장투 분리 로직
- ✅ `STYLE_DAY` (단타) / `STYLE_LONG` (장투) 명확히 구분
- ✅ 단타: `WATCH_LIST` 사용 (5분 간격 스캔)
- ✅ 장투: `WATCH_LIST_LONG` 사용 (30분 간격 스캔)
- ✅ 단타 강제 청산: 15:20~15:29 (장투 제외)
- ⚠️ **중요**: 단타는 한국 주식만 거래 (해외 주식은 장투만)

### 2. 리스크 관리 (config.py)
- ✅ 단타 설정:
  - 최대 포지션: 2개
  - 1회 최대 투자: 30만원
  - 손절: -1.5% / 익절: +2.5%
  - 일일 손실 한도: -10만원
  - AI 신뢰도 최소: 82점
- ✅ 장투 설정:
  - 최대 포지션: 6개
  - 1회 최대 투자: 100만원
  - 손절: -7% / 익절: +20%
  - 일일 손실 한도: -50만원
  - AI 신뢰도 최소: 75점

### 3. 주문 실행 (order_manager.py)
- ✅ 중복 주문 방지 (`_pending` 세트)
- ✅ 페이퍼/실거래 모드 분기
- ✅ 모든 주문 DB 저장
- ✅ 리스크 체크 통과 후 주문 실행

### 4. 포지션 사이징 (position_sizer.py)
- ✅ Kelly Criterion 1/4 적용 (보수적)
- ✅ ATR 기반 손절선 자동 계산
- ✅ 신뢰도 가중치 적용
- ✅ RISK_CONFIG 하드 상한 준수

### 5. 안전장치
- ✅ 일일 손실 한도 초과 시 자동 거래 중단
- ✅ 손절/익절 자동 실행
- ✅ 뉴스 악재 시 진입 차단
- ✅ 장 마감 전 단타 강제 청산

## ⚠️ 실전 투입 전 필수 확인 사항

### 1. 환경 변수 (.env)
```bash
TRADING_MODE=paper  # 처음엔 paper로 시작!
KIWOOM_ACCOUNT_NO=your_account
KIWOOM_APPKEY=your_key
KIWOOM_SECRETKEY=your_secret
GEMINI_API_KEY=your_gemini_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 2. 감시 종목 설정 (config.py 또는 user_config.json)
- ⚠️ **단타 WATCH_LIST**: 한국 주식만 포함 확인
  - 현재: 삼성전자, SK하이닉스, NAVER, LG화학, 삼성SDI, 현대차, 카카오, 셀트리온
  - ❌ Apple, NVIDIA 등 해외 주식 제거 필요!
- ✅ **장투 WATCH_LIST_LONG**: 한국+해외 가능
  - 현재: 삼성전자, SK하이닉스, LG에너지솔루션, Apple, Microsoft, NVIDIA, Amazon

### 3. 실거래 전환 절차
1. 페이퍼 모드로 최소 1주일 테스트
2. 리스크 파라미터 검증 (손절/익절 작동 확인)
3. 텔레그램 알림 정상 작동 확인
4. `.env`에서 `TRADING_MODE=live` 변경
5. 10초 카운트다운 후 실거래 시작

### 4. 키움 API 연동
- ✅ 로그인 타임아웃: 30초
- ✅ TR 타임아웃: 10초
- ✅ 재연결 최대 시도: 3회
- ⚠️ 실거래 시 계좌번호 정확성 재확인

### 5. 모니터링
- ✅ 대시보드: `python dashboard/realtime_app.py`
- ✅ 텔레그램 양방향 명령 지원
- ✅ 일일/주간 리포트 자동 생성
- ✅ 성과 귀인 분석 (장 마감 후)

## 🔧 실전 투입 전 수정 필요 사항

### 🚨 긴급: WATCH_LIST에서 해외 주식 제거
현재 `config.py`의 `_DEFAULT_WATCH_NAMES`에 Apple, NVIDIA가 포함되어 있습니다.
단타는 한국 주식만 거래해야 하므로 제거 필요!

```python
# 수정 전
_DEFAULT_WATCH_NAMES = [
    "삼성전자", "SK하이닉스", "NAVER", "LG화학", "삼성SDI",
    "현대차", "카카오", "셀트리온", "Apple", "NVIDIA",  # ❌ 해외 주식
]

# 수정 후
_DEFAULT_WATCH_NAMES = [
    "삼성전자", "SK하이닉스", "NAVER", "LG화학", "삼성SDI",
    "현대차", "카카오", "셀트리온",  # ✅ 한국 주식만
]
```

## 📋 실전 당일 체크리스트

- [ ] `.env` 파일 확인 (TRADING_MODE, 계좌번호)
- [ ] WATCH_LIST 한국 주식만 포함 확인
- [ ] 키움 API 로그인 테스트
- [ ] 텔레그램 봇 연결 확인
- [ ] 대시보드 접속 확인
- [ ] 가용 현금 확인
- [ ] 리스크 파라미터 최종 확인
- [ ] 09:00 장 시작 전 시스템 가동
- [ ] 첫 스캔 시 스크리너 실행 확인
- [ ] 첫 거래 발생 시 텔레그램 알림 확인

## 🆘 비상 연락처 / 중단 절차

### 긴급 중단 방법
1. `Ctrl+C` (SIGINT) → 정상 종료 프로세스 실행
2. 텔레그램 `/halt` 명령 → 즉시 거래 중단
3. 미체결 주문 자동 취소
4. 포지션 현황 최종 리포트 생성

### 문제 발생 시
- 로그 확인: `logs/trade_YYYYMMDD.log`
- DB 확인: `db/trade_log.db`
- 대시보드: `http://localhost:5001`
- 텔레그램 `/status` 명령으로 실시간 상태 확인

---

**마지막 업데이트**: 2026-04-19
**다음 점검**: 실전 투입 직전 (월요일 08:50)
