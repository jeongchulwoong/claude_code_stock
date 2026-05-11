# 단타 시스템 라이브 활성화 체크리스트

**대상:** 50만원 소액 자본 단타 시스템
**원칙:** 한 번의 실수도 치명적 — 모든 단계에서 운영자 명시 확인 필요
**작성:** 2026-05-11

---

## 활성화 전 필수 확인 사항

### 0. 현재 상태 (이 체크리스트 작성 시점)

| 항목 | 값 | 위치 |
|---|---|---|
| 운용 자본 | 500,000원 | `config.py: RISK_CONFIG.capital_limit` |
| 동시 보유 종목 | 1개 | `config.py: RISK_CONFIG.max_positions` |
| 1회 최대 투입 | 250,000원 (자본 50%) | `config.py: RISK_CONFIG.max_invest_per_trade` |
| 일 신규 진입 | 2건 | `config.py: RISK_CONFIG.daytrade_max_per_day` |
| 거래당 리스크 | 자본 0.5% | `config.py: RISK_CONFIG.risk_per_trade_pct` |
| 손절 (ATR) | 진입 - 1.5×ATR | `config.py: RISK_CONFIG.stop_loss_atr_mult` |
| 일 손실 한도 | -10,000원 (-2%) | `config.py: RISK_CONFIG.daily_loss_limit` |
| 연속 손실 중단 | 2연패 | `config.py: RISK_CONFIG.consecutive_loss_halt` |
| 시간 청산 | 60분 강제 / 30분 미수익 | `config.py: RISK_CONFIG.daytrade_time_stop_*` |
| ML 게이트 | **paper_only=True / use_as_entry_gate=False** | `config.py: DAYTRADE_ML_CONFIG` |
| 운영 ML 모델 | RF baseline (roc_auc 0.595, "weak") | `db/ml/approved_model_manifest.json` |

→ **이미 보수적**. 추가 강화가 필요한 경우 아래 "더 보수적 가동" 섹션 참조.

### 1. 시스템 헬스 점검

```bash
# 1) 모든 테스트 통과 확인
bash scripts/verify.sh

# 2) 키움 API 토큰 갱신 정책 동작 확인
python -m pytest tests/test_kiwoom_token_lifecycle.py -v

# 3) 주문 거부 / 중복 차단 회귀 테스트
python -m pytest tests/test_order_manager.py -v
python -m pytest tests/test_risk_manager.py -v
```

### 2. paper_trading / dry_run 로 1주일 모니터링

매니페스트가 명시: `DAYTRADE_ML_CONFIG.paper_only=True` 유지 + 일반 매매 코어는
`order_manager.execute()` 의 hoga 기본값 `"03"` (시장가) 이지만, BUY 경로는
`place_limit_buy_with_repricing()` 가 자동으로 한도가 + 재호가 사용.

확인 사항:
- [ ] `logs/orders.log` (또는 risk_events) 에 dry-run / 실주문 분기 명확
- [ ] 일 결제 한도 / 매수여력 한도 초과 시 자동 차단 작동
- [ ] WS 끊김 → 재연결 → 미수신 체결 보강 확인 (intraday 시뮬레이션)

### 3. ML 게이트 (선택, paper_only 유지 권장)

| 매니페스트 게이트 항목 | 현재 통과 | 활성화 조건 |
|---|---|---|
| operational.promotion_gate_status | "not_promoted" | 운영자 manual 편집으로 "promoted" |
| operational.operator_signoff_at | "" | 운영자 manual edit (ISO datetime) |
| phase_2 통과 (daily_top_2 EV+, walk-forward 7s+, DD≤5%, etc.) | 0~2/5 | 5/5 필요 |

**현재 phase_2 통과 0~2/5 → ML 게이트 절대 활성화 금지.**
`use_as_entry_gate=True` 로 변경하지 마세요.

### 4. 첫 라이브 거래 전 (모든 항목 OK 후에만)

```bash
# 1) 키움 모의투자 환경에서 1주일 동안 다음 모두 발생 + 정상 처리 확인
#    - 신규 매수 진입 (limit + 재호가)
#    - 손절 발동 (ATR-based stop)
#    - 익절 발동 (R/R 2:1)
#    - 시간 청산 (30분/60분 룰)
#    - 일 손실 한도 도달 → 자동 중단
#    - 연속 2연패 → 자동 중단

# 2) 첫 실거래일 — 자본 일부만 (예: 100,000원) 으로 시작
#    config.py 의 RISK_CONFIG.capital_limit 임시 100_000 로 낮춰서 가동
```

---

## "더 보수적" 가동 (선택)

50만원 자본을 더 안전하게 가동하려면 아래 override:

```python
# config.py 또는 user_config.json 에 다음 override
RISK_CONFIG_ULTRA_CONSERVATIVE = {
    "capital_limit":          500_000,
    "max_positions":          1,
    "max_invest_per_trade":   100_000,    # 자본 20% (기본 50% → 20%)
    "daytrade_max_per_day":   1,           # 일 1건 (기본 2 → 1)
    "risk_per_trade_pct":     0.003,       # 자본 0.3% (기본 0.5% → 0.3%)
    "stop_loss_atr_mult":     1.2,         # 더 타이트 stop (기본 1.5 → 1.2)
    "take_profit_atr_mult":   2.5,         # R/R 2:1 → 2:1.2
    "daily_loss_limit":      -5_000,       # 자본 -1% (기본 -2% → -1%)
    "consecutive_loss_halt":  1,           # 1연패 중단 (기본 2 → 1)
    "daytrade_time_stop_hard_min": 30,     # 30분 강제청산 (기본 60 → 30)
    "min_confidence":         80,          # AI 신뢰도 75 → 80
}
```

이 override 는 `user_config.json` 또는 환경변수로 적용. **코드 수정 X**.

---

## 활성화 단계 (점진적 가동)

### Step A — Paper-only 검증 (1~2주, 필수)

```bash
# 백그라운드 가동 (paper_only=True 그대로)
scripts/start_all.bat

# 일별 모니터링
python scripts/check_microstructure_status.py      # microstructure 누적 확인
python scripts/check_ml_promotion_readiness.py     # ML 게이트 진척도
```

**합격 기준:**
- [ ] 7~14 영업일 무중단 가동
- [ ] 주문 거부 / 중복 차단 작동 확인
- [ ] 토큰 갱신 자동화 작동
- [ ] paper 결과 평가: 일평균 수익률, MDD, 거래 수

### Step B — 100,000원 라이브 (1주)

```python
# config.py 임시 수정:
RISK_CONFIG["capital_limit"] = 100_000
RISK_CONFIG["max_invest_per_trade"] = 50_000   # 자본 50%
```

**합격 기준:**
- [ ] 5 영업일 누적 -10% 이내 (최대 -10,000원)
- [ ] 일 평균 거래 1~2건 (체결률 80% 이상)
- [ ] 위험 이벤트 (위험 등급 risk_events) 0건

### Step C — 500,000원 라이브 (점진적)

```python
RISK_CONFIG["capital_limit"] = 500_000   # 원래 값 복원
RISK_CONFIG["max_invest_per_trade"] = 100_000   # 보수: 자본 20%
```

**합격 기준:**
- [ ] 10 영업일 누적 -2% 이내
- [ ] consecutive_loss_halt 정상 작동
- [ ] daily_loss_limit 정상 작동

### Step D — ML 게이트 활성화 (Phase B/C 완료 후, 별도 결정)

**필수 조건 (모두 만족):**
1. 매니페스트 `phase_2_baseline_validation` exit_criteria 5/5 통과
2. Forward paper 30+ 세션 walk-forward EV ≥ 0
3. Microstructure 169 종목 × 30+ 세션 누적
4. 운영자 매니페스트 manual edit:
   - `operational.promotion_gate_status = "promoted"`
   - `operational.operator_signoff_at = "<ISO datetime>"`
5. `DAYTRADE_ML_CONFIG.use_as_entry_gate` 변경 (이것도 운영자 manual)

---

## 자동 중단 트리거 (이미 구현됨, 검증만)

다음은 시스템이 자동으로 감지/처리하는 항목 — 코드 변경 없이 작동:

| 트리거 | 조건 | 동작 |
|---|---|---|
| 일 손실 한도 | -10,000원 | 신규 진입 차단 (당일) |
| 연속 손실 | 2연패 | 신규 진입 차단 (당일) |
| 시간 청산 (하드) | 60분 보유 | 강제 청산 |
| 시간 청산 (소프트) | 30분 보유 + 수익<+0.5R | 청산 |
| 토큰 만료 (8005) | 키움 응답 | 23h 선제 갱신 + reactive 재시도 |
| WS 끊김 | 연결 끊김 | 자동 재연결 + 체결 보강 |
| 중복 주문 시도 | 동일 ticker pending | OrderResult(BLOCKED) |

---

## 위험 시그널 (즉시 가동 중단)

다음 발생 시 즉시 시스템 중단 + 사용자 검토:

- 단일 거래 손실 > 자본 0.5% (예: 50만원 자본에 단건 -2,500원 초과)
- 일 손실 > 자본 1% (예: -5,000원 초과)
- 같은 종목 같은 분 안에서 매수 → 매도 → 매수 (whipsaw)
- broker 응답 timeout > 5초 (네트워크 문제 가능성)
- ML score events 가 어떤 종목에서 갑자기 100% outlier (분포 시프트, AE drift monitor)

이 상황에서는 다음을 즉시 실행:
```bash
# 1) main 프로세스 중단
taskkill /F /IM python.exe   # 또는 Ctrl+C

# 2) 보유 포지션 수동 청산 (HTS 또는 모의 환경)

# 3) 로그 확인
tail -100 logs/main.log
tail -50 logs/orders.log
```

---

## 활성화 결정 책임

이 체크리스트의 모든 항목 확인은 **운영자 책임**.
시스템 코드 (config.py / order_manager.py) 의 보수 설정은 안전 기본값이지만,
**실거래 활성화 자체는 사용자 명시 결정** — Claude / AI assistant 가 자동으로
flip 하지 않는다.

매니페스트 정책 (`memory/project_kiwoom_token.md`, `.claude/rules/trading-safety.md`)
이 명확히 못 박은 사항.
