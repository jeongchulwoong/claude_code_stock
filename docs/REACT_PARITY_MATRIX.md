# React Parity Matrix — Quant Desk Admin UI

**상태**: Phase F8 (React 100% replacement of Jinja UI). Jinja UI는 모든 React 항목이
"complete" 가 될 때까지 fallback 으로 유지한다. 본 매트릭스가 모두 ✅ 가 되면 별도 phase 에서
Jinja 대형 dashboard HTML/JS 를 제거한다.

---

## 화면별 Parity Matrix

| 기존 화면 | 기존 기능 | 기존 API | React 대체 컴포넌트 | 상태 |
|---|---|---|---|---|
| **/login** | 비번 로그인 (Jinja form) | `/login` POST form | `apps/login/LoginApp.tsx` (JWT) | ✅ Phase F6 — 완료 |
| **/advanced 헤더** | brand / 운영 모드 / role pill / logout | (session) | `apps/admin/AdminShell.tsx` 의 `qd-shell-bar` | ✅ Phase F7 — 완료 |
| **/advanced 탭 1: Overview** | 운영 모드 · 매수가능 · 잔고손익 · 주문 상태 · 일일손실 · 데이터 헬스 · trader heartbeat · 단타 게이트 | `/api/summary`, `/api/balance`, `/api/orders`, `/api/admin/system_status`, `/api/admin/process_status`, `/api/admin/daytrade_state` | `features/overview/AdminOverviewIsland.tsx` (7 카드) | ✅ Phase F3+F6 — 완료 |
| **/advanced 탭 2: Portfolio** | 평가금액 · 매입금액 · 평가손익/수익률 · 오늘/누적 실현손익 · 매수가능 · 보유 종목 카드 · 섹터 비중 차트 · stale/error 상태 | `/api/portfolio` (실제 필드: `holdings[].sector / weight / eval_amt / pnl / pnl_rate`) | `features/portfolio/AdminPortfolioIsland.tsx` + `SectorChart.tsx` (SVG donut, 8색 팔레트, segment hover) | ✅ Phase F7 — 완료 |
| **/advanced 탭 3: 분단타** | 상단 상태 · 매수가능 · 1주 가능 상한 · 일일 진입 · process status · 5그룹 20개 설정값 · 차단 사유 | `/api/admin/minute_daytrade` | `features/daytrade/DaytradeTab.tsx` (4 그룹 카드 — 자금/시간/신호/손익) | ✅ Phase F7 — 완료 |
| **/advanced 탭 4: Realtime** | process heartbeat · websocket · 최근 위험 이벤트 · critical pinning · category filter · stale | `/api/admin/risk_events`, `/api/orders` | `features/realtime/AdminRealtimeIsland.tsx` (4 섹션 bucket: critical / orders / risk / system) | ✅ Phase F4 — 완료 |
| **/advanced 탭 5: Risk Events** | 위험 이벤트 list · rollup/펼치기 · 필터 · ticker 검색 · 민감정보 redaction | `/api/admin/risk_events` | `features/riskEvents/AdminRiskEventsIsland.tsx` (group by ticker+category, COMPRESS_THRESHOLD=3) | ✅ Phase F4 — 완료 |
| **/advanced 탭 6: Orders** | 최근 주문 (체결/차단/대기) · 판단 근거 · 수량/가격/시각 | `/api/orders` | `features/orders/OrdersTab.tsx` (200건 cap, status 톤, 사유 ellipsis) | ✅ Phase F7 — 완료 |
| **/advanced 탭 7: Screener** | admin 스크리너 결과 (점수/사유/시장) | `/api/screener` | `features/screener/ScreenerTab.tsx` (200건 cap) | ✅ Phase F7 — 완료 |
| **/advanced 탭 8: Settings** | 운영 정책 read-only display | `/api/config` GET | `features/settings/SettingsTab.tsx` (4 그룹: risk / long_risk / schedule / daytrade_ml) | ✅ Phase F7 — 완료 |
| **/advanced 탭 9: Watchlist** | watchlist 종목 관리 · 추가/삭제 (`user_config.json`) | `/api/config` GET/POST | `features/watchlist/WatchlistTab.tsx` (read-only display 우선, 편집은 후속 phase) | ✅ Phase F8 — 완료 (read-only) |
| **/advanced 탭 10: Strategy Stats** | 전략별 시그널 수 / 정확도 / 승률 | `/api/strategy_stats` | `features/strategies/StrategiesTab.tsx` | ✅ Phase F8 — 완료 |
| **/advanced 탭 11: Chart** | 일별 실현손익 chart · 종목별 통계 chart | `/api/daily_pnl`, `/api/ticker_stats`, `/api/chart` | `features/chart/ChartTab.tsx` (SVG line/bar, no external lib) | ✅ Phase F8 — 완료 (daily_pnl + ticker_stats) |
| **/client** | 공개 스크리너 (점수 ≥70 종목) | `/api/public/screener` | `apps/client/ClientScreenerIsland.tsx` | ✅ Phase F5 — 완료 |
| **5001 (realtime_app)** | Socket.IO 실시간 push | `/api/admin/risk_events` 등 | (현재 Flask/Jinja, React 미전환) | ⚠️ 후속 phase — 5000 admin shell 통합으로 충분 |

---

## API 데이터 계약 매핑

### `/api/portfolio` (실제 응답 → React 정규화)

```
실제 키                     →  React NormalizedHolding
holdings[].ticker           →  ticker
holdings[].name             →  name
holdings[].qty              →  qty
holdings[].avg_price        →  avgPrice
holdings[].current_price    →  currentPrice  (legacy 'cur_price' fallback)
holdings[].eval_amt         →  evalAmount
holdings[].pnl              →  pnl
holdings[].pnl_rate         →  pnlPct        (legacy 'pnl_pct' / 'evlt_pl_pct' fallback)
holdings[].sector           →  sector
holdings[].weight           →  weight        (legacy 'weight_pct' fallback)

sectors[].sector            →  NormalizedSector.sector
sectors[].eval_amt          →  evalAmount
sectors[].weight            →  weight

tot_evlu_amt / tot_pur_amt / tot_evlt_pl / tot_evlt_pl_rate / realized_pnl /
today_realized_pnl / buying_power / holdings_count / updated_at  →  PortfolioSummary
```

### `/api/balance` + `/api/summary` + `/api/admin/*`

| 화면 라벨 | API 출처 | 키 | 표시 규칙 |
|---|---|---|---|
| 매수가능 | `/api/balance` 또는 `/api/portfolio` | `buying_power` | null → `--` / 0 → `0원` |
| 평가금액 | `/api/portfolio` | `tot_evlu_amt` | null → `--` |
| 평가손익(미실현) | `/api/portfolio` | `tot_evlt_pl` + `tot_evlt_pl_rate` | tone: pos/neg/zero |
| 실현손익(누적) | `/api/portfolio` 또는 `/api/summary` | `realized_pnl` | tone |
| 오늘 실현손익 | `/api/portfolio` | `today_realized_pnl` | tone |
| trader heartbeat | `/api/admin/process_status` | `main.heartbeat_age_sec`, `main.stale` | ≥60s → stale warn |
| 단타 게이트 | `/api/admin/daytrade_state` | `daytrade.{state, today_entries, max_daily_entries, halted, ...}` | tone by state |
| 데이터 헬스 | `/api/admin/system_status` | `kiwoom / rest / websocket / telegram` | ok/warn/fail |
| 일일 손실 | `/api/admin/system_status` | `daily_loss.used_pct / used_text / limit_text` | ≥80% → fail |

값 표시 규칙:
- API 값 없음 (`null` / `undefined`) → `--`
- API 값 0 → `0원` / `0.00%` (구분)
- 폴링 실패 → 카드 헤더에 `갱신 실패` (amber pill)
- 스냅샷 stale (>60s no heartbeat) → `stale` 표시

---

## React AdminShell 구조

```
frontend/apps/admin/
  main.tsx                 # #admin-shell-react-root 단일 mount + legacy fallback
  App.tsx                  # legacy (Overview-only) — fallback 으로만 사용
  AdminShell.tsx           # 11 tab admin console (URL hash deep-link)
  admin-shell.css          # sticky header + tab nav + 반응형
  features/
    overview/              # 7 cards (Overview Tab)
    portfolio/             # SectorChart + holdings grid
    minuteDaytrade/        # 4-group control snapshot
    realtime/              # 4-section bucket
    riskEvents/            # rollup + filter
    orders/                # table
    screener/              # table
    watchlist/             # F8 신규 — read-only watchlist display
    strategies/            # F8 신규 — strategy stats
    chart/                 # F8 신규 — daily PnL + ticker stats SVG
    settings/              # config read-only
```

**Mount 정책:**
- `/advanced` 접속 → `#admin-shell-react-root` 1개에 AdminShell 1회 mount.
- 기존 4개 island root (`overview / risk-events / realtime / portfolio`) 는 legacy fallback 으로 보존.
- AdminShell mount 성공 시 `:has()` CSS 로 Jinja header/tab/panel 자동 숨김 (이중 표시 방지).
- React mount 실패 시 Jinja UI 정상 fallback.

---

## start_all 자동화

- `frontend/dist/.vite/manifest.json` 부재 시 `npm install` (node_modules 없으면) + `npm run build` 1회 자동.
- `-NoBuild` 옵션으로 자동 빌드 비활성 가능.
- 5000/5001 좀비 listener 자동 정리.
- Ctrl+C → background + trader 모두 정리.

---

## Jinja 삭제 가능/불가능 판정

**현재 상태 (F8 완료 시점)**: ⚠️ **삭제 보류**.

이유:
- React tab 11종 모두 mount/렌더 가능하지만, 운영자가 브라우저에서 *실제 Kiwoom 계좌 값*으로 1주 이상 검증해야 함.
- Realtime 5001 (Socket.IO push) 은 별도 React 미전환 — 현재는 Jinja 화면 그대로.
- watchlist 편집 (POST `/api/config`) 은 read-only 단계까지만 — 편집 UI 는 후속 phase.

**삭제 게이트**:
1. ✅ 매 탭 React mount + 데이터 정상 표시
2. ⚠️ 사용자가 브라우저에서 실 계좌로 1주 검증 (운영 책임 영역)
3. ⚠️ Realtime 5001 React 전환 또는 Jinja 5001 유지 결정
4. ⚠️ `bash scripts/verify.sh` PASS=7 + frontend test 모두 통과
5. ⚠️ 사용자 명시 승인 ("Jinja 제거 진행")

---

## 안전 invariant 보존 (Phase F8)

본 phase 에서 *수정하지 않은* 파일:
- `core/order_manager.py`
- `core/risk_manager.py`
- `core/position_sizer.py`
- `core/kiwoom_api.py`
- `core/kiwoom_ws.py`
- `main.py`
- `user_config.json`
- 기타 broker/strategy/backtest 코드

본 phase 의 모든 변경은 `frontend/`, `dashboard/templates/advanced_dashboard.html` 의 mount root 추가, `tests/` 회귀, `docs/`. dashboard JSON API 는 **읽기만** 사용.
