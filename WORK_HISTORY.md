# 📝 키움 비동기 퀀트 자동매매 프로젝트 작업 히스토리 (WORK_HISTORY.md)

---

## 📅 [2026-09-16 10:55] 키움 TR 보유종목 매입단가(매수가) '1원' 표출 버그 완벽 해결 및 6단계 다중 방어 해석 알고리즘(6-Layer Resolution) & 프론트엔드 안전 렌더링 배포 완료

### 1. 작업 개요 및 목적
- **1) 보유종목 '매수가 1원' 표출 버그 원인 규명 및 전면 해결:**
  - **원인 ① (프론트엔드 Default Coercion의 함정):** `ActivePositionsBento.tsx` 및 `Header.tsx`에서 `const buyPrice = pos.buy_price || 1;` 구문으로 인해, 백엔드로부터 `buy_price`가 `0` 또는 누락으로 전달될 경우 `0 || 1` 연산에 의해 모든 종목의 매수가가 강제로 '1원'으로 표출되던 결함을 규명.
  - **원인 ② (TR 스키마 단가 필드 파편화 및 단가 누락 시 역산 부재):** 키움 TR(`kt00018`, `kt00004`, `kt00005`, `opw00018`)에서 `pchs_avg_pric` 단가 필드가 누락되고 `pchs_amt`(매입금액), `evlu_amt`(평가금액), `evlu_pfls_amt`(평가손익), `evlu_pfls_rt`(수익률)만 전송될 때 단가를 복원하는 다중 역산 파이프라인이 부재했던 점을 해결.
  - **원인 ③ (DB 복원 데이터의 1원 오염):** 과거 1원으로 저장되었던 DB 데이터가 복원될 때 자가 치유(Self-Healing) 로직이 부재했던 결함 해결.
- **2) 6단계 매수가 다중 방어 해석 알고리즘 탑재 (`async_portfolio.py`):**
  - **Layer 1 (직접 단가 키 탐색):** `pchs_avg_pric`, `pchs_price`, `pchs_prc`, `buy_uv`, `ccls_avg_pric`, `ccls_prc`, `pur_prc`, `thst_buy_uv`, `매입단가`, `매수가` 등 30여 개 키에서 1원 초과 유효값 추출.
  - **Layer 2 (매입금액 / 수량 역산):** `pchs_amt` / `qty`를 통해 정확한 매입단가 산출.
  - **Layer 3 (평가손익 역산):** `(evlu_amt - pnl) / qty`를 통해 매입원금 및 단가 산출.
  - **Layer 4 (수익률 역산):** `current_price / (1 + yield_rate / 100)`를 통해 현재가와 수익률 기반 단가 복원.
  - **Layer 5 (직전 포지션 상속):** 인메모리 `prev_positions`의 유효 매수가 보존 및 상속.
  - **Layer 6 (현재가 안전 폴백):** 어떠한 단가 필드도 유효하지 않을 경우 `current_price`를 안전 기본값으로 채택하여 0원/1원 왜곡을 원천 차단.
- **3) DB 및 API 서버 자가 치유(Self-Healing) 탑재 (`api_server.py`, `async_portfolio.py`):**
  - DB 복원 및 실시간 브로드캐스트 루프에서 `buy_p <= 1.0 and cur_p > 1.0`인 경우 현재가(`cur_p`)로 자동 보정하여 오염 데이터 차단.
- **4) 프론트엔드 안전 렌더링 및 단가 복원 (`ActivePositionsBento.tsx`, `Header.tsx`):**
  - `|| 1` 구문 제거 및 `rawBuyPrice > 1 ? rawBuyPrice : (rawCurPrice > 1 ? rawCurPrice : 0)` 안전 fallback 적용.
  - PnL 및 수익률 계산 시 0으로 나누기 방지 및 백엔드 원본 손익/수익률 우선 바인딩.

### 2. 주요 수정 파일 및 변경 내역
- `async_portfolio.py`:
  - `buy_p_keys`, `pchs_amt_keys`, `cur_p_keys`, `evlu_amt_keys`, `pnl_keys`, `rt_keys` 전수 확장.
  - 6단계 매수가 다중 방어 해석 알고리즘 탑재.
  - `restore_positions_from_db` 내 DB 오염 데이터 자가 치유 로직 추가.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio()` 내 1원/0원 방지 자가 치유 및 정합성 보장.
- `frontend/src/components/ActivePositionsBento.tsx` & `frontend/src/components/Header.tsx`:
  - `const buyPrice = pos.buy_price || 1;` 결함 제거 및 현재가 기반 안전 fallback 로직 적용.
- `test_async_trading_loop.py`:
  - `MockDatabaseManager.get_portfolio_positions` 구현 및 Test 17 (매수가 6단계 다중 방어 해석 및 1원 버그 원천 방어) 신설.

### 3. 검증 결과
- **테스트 스위트:** `test_async_trading_loop.py` 총 17개 종합 테스트 100% 통과 (`ALL PASS`).
- **단위 테스트:** `test_async_core.py`, `test_api_server.py`, `test_strategy_quant.py`, `test_valuation.py`, `test_indicators.py` 전 항목 100% 통과.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (0 errors).
- **형상 관리 및 배포:** `fix/rendering-optimization-and-safety-fixes` 브랜치 커밋 `a0534f7` 원격 push 완료.

---

## 📅 [2026-09-15 13:00] UI 렌더링 최적화(Display State 10분 주기 분리), 보유 포지션 파이프라인 정합성 복원 및 감시 종목 고가 필터 1차 방어 로직 복원 완료

### 1. 작업 개요 및 목적
- **1) Frontend UI 렌더링 최적화 및 깜빡임 해결 (Display State 분리):**
  - 실시간 매매/트레이딩 엔진은 WebSocket 실시간 최신 데이터를 사용하되, 화면 표출 전용 상태(`displayPortfolio`)를 분리하여 10분(600,000ms) 정주기로만 동기화하도록 제어.
  - 사용자가 헤더의 '새로고침' 버튼을 클릭하거나 최초 마운트 시에는 즉각 동기화하여, 실시간 틱 데이터 유입에 따른 헤더(총자산, D+2 예수금) 및 KPI 카드의 깜빡임/잦은 리렌더링을 완전히 방지.
- **2) 프로세스 분리 환경(`start.py`) 대응 보유 포지션 데이터 파이프라인 복원:**
  - `start.py`에 의해 `api_server.py`와 `main_rest_async.py`가 독립 프로세스로 실행될 때, `api_server.py`가 MariaDB의 `portfolio` 테이블과 `balance` 테이블을 상시 조회/동기화하도록 개선하여 대시보드에 4개 보유 종목이 누락 없이 표출되도록 정합성 복원.
- **3) 감시 종목(Watchlist) 1차 방어 로직(현재가 > D+2 예수금 제외) 복원:**
  - `update_watchlist()`에서 `current_price > available_cash`인 고가 종목을 Watchlist 등록 단계에서 사전에 즉시 배제하는 1차 방어 가드와 전용 디버그 로그(`🚫 [Watchlist 필터] 탈락: 잔고 부족...`)를 복원하여 안전성을 원천 확보.

### 2. 주요 수정 파일 및 변경 내역
- `frontend/src/hooks/useWebSocket.ts`:
  - `displayPortfolio` 화면 표출 전용 상태 신설 및 10분 정주기 타이머(`setInterval 10m`) 구축.
  - `handleRefresh` 함수에서 수동 즉시 동기화 지원.
- `frontend/src/App.tsx`:
  - `<Header />`, `<KpiMetricsRow />`, `<LogViewer />`, `<ActivePositionsBento />`에 `displayPortfolio` 연동.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio()`에서 `ctx.db`의 `portfolio` 테이블(`get_portfolio_positions()`) 및 `balance` 테이블(`get_latest_balance()`) 상시 조회/동기화 로직 탑재.
- `main_rest_async.py`:
  - `update_watchlist()` 내 `current_price > available_cash` 고가 종목 1차 방어 가드 및 `price_over_cash` 탈락 카운터/로그 복원.
- `GSD_MASTERPLAN.md`: Phase 16 신설 및 완료 기록.

### 3. 검증 결과
- **단위/통합 테스트:** `test_async_trading_loop.py` (11/11 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS) 전 테스트 스위트 통과.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (0 errors).
- **형상 관리:** `fix/rendering-optimization-and-safety-fixes` 브랜치 커밋.

---

## 📅 [2026-09-15] 키움 계좌 잔고 TR(kt00005/OPW00018) 실데이터(148,442원) 정합성 복원, 대용금 오맵핑 차단, 4개 보유 종목 UI 연동 및 데이터 요동 현상 완전 해결

### 1. 작업 개요 및 목적
- **실제 키움 앱 계좌 데이터와 대시보드 간 불일치 전면 해결:**
  - 실제 키움증권 계좌(`국내잔고.png`: 총평가 148,442원, 총매입 147,400원, 총손익 +778원(+0.53%), 4개 보유 종목)와 `예수금.png` (D+2예수금 1,122원, 대용금 103,890원)의 수치를 100% 일치하도록 파싱 및 상태 동기화 로직 전면 개편.
- **총자산 vs 대용금 오맵핑 및 계산식 오류 원천 차단:**
  - 기존 `final_total_asset` 계산식에서 `parsed_raw_entr > 0`일 때 Case A로 강제 진입하여 `tot_evlu_amt`(148,442원)가 무시되고 예수금(1,122원) 또는 대용금(100,842원 근처)으로 오염되던 결함을 완벽 해결.
  - 키움 TR의 `tot_evlu_amt`(총평가금액 = 148,442원)를 최우선 1순위로 매핑하고, 대용금(`sub_amt`: 103,890원)을 분리 로깅하도록 정규화.
- **멀티데이터(`output2`) 4개 보유 주식 정밀 파싱 및 UI 연동:**
  - 흥아해운(003280, 2주 @ 1,980원), 한국전력(015760, 1주 @ 32,950원), 비에이치(090460, 1주 @ 19,520원), KODEX 코스닥150(229200, 1주 @ 14,080원)의 수량, 매입단가, 현재가, 평가손익, 수익률을 누락 없이 파싱하여 `portfolio.positions`에 등록.
- **Backend/Frontend 상태 관리 일원화 및 잔고 요동(Fluctuation) 완전 제거:**
  - REST `/portfolio` 폴링과 WebSocket 브로드캐스트 간의 데이터 경합 및 덮어쓰기 충돌을 해결.
  - `AsyncPortfolioManager`를 단일 진실 공급원(Single Source of Truth)으로 확립하고, 프론트엔드(`useWebSocket.ts`)에서 WS 실시간 메시지를 우선 반영하여 1,122원과 148,442원 사이의 요동 및 포지션 0개 깜빡임 현상 제거.
- **동적 감시 목록(Dynamic Watchlist) 정상화 및 전체 퀀트 매매 워크플로우 시뮬레이션 검증:**
  - 소액 잔고(1,122원)로 인해 우량 감시 종목이 전부 탈락하던 문제 해결: 감시 목록은 거래대금 상위 20~30개 종목을 온전히 유지하고, 매수 직전에만 잔고 체크하도록 분리.
  - '피보나치 타점 매수 -> 포지션 편입 -> 보유 중 트레일링 감시 -> 3단계 분할 익절/스탑로스 전량 매도'의 전체 퀀트 매매 사이클 E2E 검증.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `_sync_account_balance()` 파싱 로직 개편: `candidates_total` 계산식 도입으로 `tot_evlu_amt`(148,442원)를 최우선 적용하고, `sub_amt`(대용금 103,890원)를 명시적으로 분리.
  - `update_watchlist()`: 고가 종목 탈락 대신 `affordable` 플래그 관리로 전환하여 20~30개 거래대금 상위 종목의 피보나치 감시 목록 유지.
- `async_portfolio.py`:
  - `sync_positions`: `pchs_avg_pric` 누락 시 `pchs_amt / qty`로 단가 자동 보정, `prpr` 누락 시 `evlu_amt / qty`로 현재가 보정, 평가손익(`pnl`)과 수익률(`yield_rate`) 정규화.
  - `get_snapshot()`: `invested_pchs > 0`일 때 `total_yield_rate` 정확한 수익률 산출 및 `positions` 전수 직렬화.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio`: `ctx.portfolio` 인메모리 스냅샷을 단일 진실 공급원으로 확립하여 DB 폴백과의 경합 요동 제거.
- `frontend/src/hooks/useWebSocket.ts`:
  - WebSocket 실시간 `PORTFOLIO_UPDATE` 우선 적용, REST 폴백 시 WS 연결 중 덮어쓰기 방지, `stock_count`와 `positions` 동기화.
- `test_async_trading_loop.py`:
  - Test 10 (`test_actual_account_balance_and_4_holdings_sync`) 신설: 실제 키움 앱 데이터(148,442원, 1,122원, 4개 보유 종목) 정밀 검증.
  - Test 11 (`test_dynamic_watchlist_and_full_quant_workflow`) 신설: 동적 감시 목록 갱신 및 전체 매매 사이클(매수->1차익절->2차익절->전량청산) E2E 검증.
- `GSD_MASTERPLAN.md`: Phase 14 완료 및 Phase 15 신설 반영.

### 3. 검증 결과
- **단위/통합 테스트:** `test_async_trading_loop.py` (11/11 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS) 전 테스트 스위트 통과.
- **프론트엔드 빌드:** `npm run build` Vite 프로덕션 번들링 성공 (0 errors).
- **형상 관리:** `fix/actual-balance-match-and-portfolio-display` 브랜치 커밋.

---

## 📅 [2026-09-09] 키움 계좌 잔고 TR(kt00005/OPW00018) 총자산 vs D+2 예수금 필드 오맵핑 전면 교정 및 정밀 파싱 엔진 구축

### 1. 작업 개요 및 목적
- **총자산 vs D+2 예수금 동일 수치 덮어쓰기 결함 해결:** 키움 API `kt00005` 응답에서 `tot_evlu_amt` 필드가 D+2 추정예수금(`dnca_tot_amt`: 100,842원)과 동일한 값으로 내려올 때, 단순 예수금 원금(`prvs_rcdl_excc_amt`/`entr`: 114,922원)이 무시되고 총자산과 D+2 예수금이 동일하게 10만 원대로 덮어씌워지던 문제를 전면 해결.
- **키움 TR 응답 키 우선순위 및 계산식 재설계:**
  - `total_asset` (총자산): HTS 기준 단순 예수금 원금(`prvs_rcdl_excc_amt`, `entr`, `deposit`) + 보유 주식 평가액(`invested_eval`) 또는 자산평가금액(`tot_asst_amt`, `aset_evlt_amt`)으로 산출 (약 11만 원대 값 확정).
  - `available_cash` (D+2 주문가능금액): D+2 정산 추정예수금(`dnca_tot_amt`, `d2_deposit`, `ord_psbl_cash`, `ord_psbl_amt`)으로 엄격히 독립 분리 (약 10만 원대 값 확정).
- **계좌 동기화 시 API 원본 Raw Data 상세 브리핑 로깅 탑재:** 계좌 싱크 시 수신된 주요 키(`prvs_rcdl_excc_amt`, `dnca_tot_amt`, `tot_evlu_amt`, `ord_psbl_cash`, `entr` 등)의 실제 수신 값을 콘솔과 로그에 명확히 표출.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `_sync_account_balance()` 파싱 로직 개편: `raw_entr_keys`를 최우선 순위로 추출하여 `final_total_asset = parsed_raw_entr + invested_eval` 산출.
  - `pure_total_asset_keys`(`tot_asst_amt`, `aset_evlt_amt`) 및 `tot_evlu_keys` 분리.
  - `📊 [계좌 TR Raw Data]` 상세 분석 로그 추가 (수신 필드, 단순 예수금, D+2 예수금, 보유주식 평가금, 총자산/주문가능 최종값 표출).
- `api_server.py`:
  - 서버 기동 시 DB 복원 로그에 총자산과 D+2 예수금 각각 분리 표출.
- `dashboard.py`:
  - 상단 메트릭 카드의 예수금 레이블을 `D+2 예수금 (주문가능)`으로 최신화.
- `test_async_trading_loop.py`:
  - `test_kiwoom_real_balance_parsing_various_schemas` (Test 9) 신설: 키움 실전 REST 표준 패턴, 순수 총자산 필드 패턴, 주식 보유 + 예수금 분할 패턴 등 3대 시나리오 검증 통과.
- `GSD_MASTERPLAN.md`: Phase 13 추가 및 완료 반영.

### 3. 검증 결과
- **단위 테스트:** `test_async_trading_loop.py` (9/9 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS), `test_async_core.py`, `test_market_data_buffer.py`, `test_indicators.py`, `test_valuation.py`, `test_notifier.py`, `test_backtest.py` 전 스위트 무결성 검증 완료.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (dist 갱신 완료).
- **형상 관리:** `fix/account-balance-parsing-fields` 브랜치 커밋.

---

### 1. 작업 개요 및 목적
- **실전투자(REAL/LIVE) 모드 전면 전환:** 기본 실행 모드를 모의투자(MOCK)에서 실제 주문이 체결되는 실전투자(LIVE)로 전환하고, 상단 헤더에 에메랄드 컬러의 `LIVE (실전투자)` 배지 표출.
- **KST (Asia/Seoul, UTC+9) 타임존 고정:** MariaDB 로그 적재/조회 및 WebSocket 실시간 스트리밍 시 UTC로 표기되던 시간을 한국 표준시(KST)로 일괄 변환 고정.
- **상단 헤더 실시간 계좌 잔고 & 보유 포지션 드롭다운 구현:** 상단 헤더 중앙에 `[ 💰 총자산: X원 | 💵 D+2 예수금: Y원 | 📦 보유 종목: Z개 ▾ ]` 상시 관제 뱃지 바를 배치하고, 클릭 시 보유 주식 상세 목록(종목명, 코드, 수량, 매입가, 현재가, 평가손익, 수익률)이 실시간으로 표출되는 인터랙티브 팝오버 드롭다운 구현.
- **보유 포지션 파싱 다중 스키마 보강:** 키움 API `kt00005`의 `output2`, `Output2`, `list`, `acnt_dtl_list`, `holdings`, `output` 등 모든 필드 규격을 100% 포괄하여 보유 종목 누락 방지.

### 2. 주요 수정 파일 및 변경 내역
- `async_kiwoom_client.py`:
  - `__init__` 기본 모드를 실전투자(`self.mode = "REAL"`, `is_demo = False`)로 전환하고 환경 변수(`IS_REAL`, `KIWOOM_MODE`) 연동
- `start.py` & `main_rest_async.py`:
  - 기본 실행 인자를 실전투자(`--real`)로 통일
- `database.py`:
  - `KST = timezone(timedelta(hours=9))` 및 `format_kst_time_str` 도입, `get_recent_logs()` KST 변환 적용
- `api_server.py`:
  - `lifespan` 시 기본 실전투자 클라이언트 기동, `log_broadcast_loop` 내 KST 타임존 적용
- `frontend/src/components/Header.tsx`:
  - `LIVE (실전투자)` 에메랄드 뱃지 및 중앙 `[총자산 | D+2 예수금 | 보유 종목]` 실시간 바 + 포지션 상세 드롭다운 메뉴 탑재
- `frontend/src/components/LogViewer.tsx`:
  - 상단 윈도우 바에 `[ 총자산: X원 | 예수금: Y원 | 보유: Z개 ]` 실시간 서머리 인디케이터 추가
- `frontend/src/App.tsx`:
  - `portfolio` 상태를 `<Header />` 및 `<LogViewer />`에 직결 전달
- `async_portfolio.py`:
  - `sync_positions` 내 다중 스키마 키(`stk_cd`, `code`, `mksc_shrn_iscd`, `pdno`, `item_code` 등) 전수 파싱 지원

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` Vite 5.4.21 번들링 성공 (0 errors, dist 갱신 완료)
- **단위 테스트:** `test_async_trading_loop.py` (8/8 통과), `test_api_server.py` (14/14 통과), `test_notifier.py` (3/3 통과) 100% ALL PASS

---

## 📅 [2026-09-08] 총 평가자산 및 D+2 주문가능 예수금 필드 독립 분리 & 장 마감 정산 리포트 기말자산 오류 해결

### 1. 작업 개요 및 목적
- **총 평가자산 및 주문가능 예수금 필드 독립 분리:** 키움 API 계좌 조회(`kt00005`) 시 '총 평가자산(`tot_evlu_amt`/`aset_evlt_amt`: 114,922원)'과 'D+2 주문가능금액(`dnca_tot_amt`/`ord_psbl_cash`: 100,842원)'이 별도의 독립된 필드로 정확히 분리 파싱되도록 구조 개선.
- **장 마감 퀀트 정산 리포트 기말자산 모순 해결:** 당일 손익이 0원임에도 기말자산이 100,842원으로 표시되던 원인이 `total_asset`을 `current_capital`(가용현금)으로 덮어썼던 계산 버그임을 규명하고, 총 평가자산 기반으로 기말자산을 산출하도록 수정. D+2 가용 예수금 항목을 리포트에 명시.

### 2. 주요 수정 파일 및 변경 내역
- `async_portfolio.py`:
  - `AsyncPortfolioManager`에 `self.total_asset` 독립 인스턴스 변수 추가
  - `sync_capital(available_cash, total_asset)` 시그니처 확장 및 `get_snapshot()` 내 총 자산과 주문가능 현금 분리 직렬화
- `main_rest_async.py`:
  - `_sync_account_balance`: API 응답 내 총 평가자산 키(`tot_evlu_amt`, `aset_evlt_amt`, `tot_asst_amt` 등)와 D+2 주문가능금액 키(`dnca_tot_amt`, `ord_psbl_cash` 등)를 독립 파싱하여 포트폴리오 관리자에 분리 주입
  - 실시간 계좌 싱크 로그 포맷 개선: `총자산 114,922원 / D+2 예수금 100,842원 / 보유 0종목 (미체결: 1건)`
- `notifier.py`:
  - `notify_daily_settlement`: 기초자산(114,922원)과 기말자산(114,922원)의 정합성을 확립하고 `• D+2예수금: 100,842원 (주문가능 현금)` 항목 추가
- `api_server.py`:
  - `lifespan` 시 DB `balance`로부터 `total_asset`과 `current_capital` 각각 독립 복원
- `test_async_trading_loop.py` & `test_notifier.py`:
  - 총자산 및 D+2 예수금 독립 분리 및 정산 알림 포맷 단위 테스트 추가 및 검증 완료

### 3. 검증 결과
- **단위 테스트:** `python test_async_trading_loop.py` (8/8 통과), `python test_api_server.py` (14/14 통과), `python test_notifier.py` (3/3 통과) 100% ALL PASS

---

## 📅 [2026-09-08] 대시보드 실시간 터미널 LogViewer 개편, D+2 예수금 단일화 및 실시간 감시 쓰로틀링 루프 복구

### 1. 작업 개요 및 목적
- **대시보드 UI 전면 개편:** 미작동하던 '감시종목 차트'를 제거하고, 봇의 실시간 매매/감시 로그가 쏟아지는 검은색 배경의 터미널 스타일 `<LogViewer/>` 컴포넌트 신규 구현 및 WebSocket/REST 듀얼 스트리밍 연동.
- **예수금 유령 차감 원인 해결 및 D+2 기준 단일화:** 당일 단순 예수금(`entr`)과 D+2 추정예수금(`dnca_tot_amt`/`ord_psbl_cash`) 간의 혼용으로 인해 114,922원에서 100,842원으로 차감되어 보이던 결함을 파악하고, 'D+2 실제 주문가능금액'으로 기준을 일원화하며 미체결 주문(`미체결: N건`) 및 증거금 차감 정산 로깅 추가.
- **실시간 감시 1회성 중단 결함 해결 및 쓰로틀링 적용:** `trading_loop` 내 `try-finally` 배치 결함으로 1회 틱 처리 후 실시간 스트림 태스크가 취소되던 버그를 해결하고 Watchdog 자동 재가동 메커니즘을 구축. 매 틱마다 매수 평가는 무조건 실행하되, `⏱ [실시간 감시]` 로그 출력은 종목당 5초 또는 괴리율 0.5% 이상 변동 시에만 출력하도록 쓰로틀링 적용.

### 2. 주요 수정 파일 및 변경 내역
- `frontend/src/components/LogViewer.tsx` (신규):
  - macOS/Linux 터미널 윈도우 스타일, 순수 블랙 배경, 실시간 스트림 인디케이터, 자동 스크롤(Auto-scroll ON/OFF), 로그 레벨 필터(전체/감시/체결/경보/시스템), 키워드 검색, 복사/지우기 기능 탑재.
- `frontend/src/components/StrategyControlsBento.tsx` (신규):
  - 봇 제어(시작/정지/동기화), 실시간 K-Breakout & Kelly 비중 슬라이더 튜너, KODEX 200 시장 필터 및 서킷 브레이커 상태 인디케이터.
- `frontend/src/App.tsx`:
  - 좌측 상단에 `<LogViewer/>` 배치 및 우측 하단에 `<StrategyControlsBento/>` 배치하여 2x2 반응형 벤토 그리드 완성.
- `frontend/src/hooks/useWebSocket.ts` & `frontend/src/types.ts`:
  - `LogMessage`에 `'WATCH'` 레벨 추가, REST `/api/logs` 폴백 동기화 및 `clearLogs` 핸들러 제공.
- `api_server.py`:
  - `@api_router.get("/logs")` 엔드포인트 신설 (최근 100건 조회).
  - `log_broadcast_loop` 백그라운드 태스크를 통해 MariaDB `logs` 테이블 신규 레코드 실시간 WebSocket 브로드캐스팅.
  - `/ws/logs` 및 `/kiwoom/ws/logs` 접속 즉시 직전 100건 `LOGS_INIT` 전송.
- `database.py`:
  - `DatabaseManager.get_recent_logs(limit=100)` 비동기 메서드 추가.
- `async_kiwoom_client.py`:
  - `get_unexecuted_orders` (미체결 주문 조회) 메서드 추가.
- `main_rest_async.py`:
  - `_sync_account_balance`: D+2 주문가능금액(`dnca_tot_amt`, `ord_psbl_cash`) 우선 파싱 및 미체결 건수(`uncl_cnt`) 추적, 차감 내역 상세 로깅.
  - `_evaluate_buy_condition`: 매수 조건 매 틱 전수 평가 유지 + 실시간 감시 대기 로그 5초/0.5%p 쓰로틀링 및 DB/WS 로깅.
  - `trading_loop`: 루프 외부 `try-finally` 재배치 및 워커 자동 복구 Watchdog 탑재.
- `test_api_server.py` & `test_async_trading_loop.py`:
  - Test 14 (REST 로그 조회) 및 Test 8 (D+2 예수금 단일화 & 쓰로틀링 검증) 추가.

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` Vite 5.4.21 번들링 성공 (0 errors)
- **API 서버 테스트:** `python test_api_server.py` 14개 테스트 100% 통과
- **트레이딩 루프 테스트:** `python test_async_trading_loop.py` 8개 테스트 100% 통과

---

## 📅 [2026-09-08] CircuitBreaker is_open AttributeError 해결 및 상태 조회 방어 로직 강화

### 1. 작업 개요 및 목적
- `api_server.py`의 `get_bot_status` API 호출 시 발생하던 `AttributeError: 'CircuitBreaker' object has no attribute 'is_open'` 에러로 인한 콘솔 로그 도배 결함 해결.

### 2. 주요 수정 파일 및 변경 내역
- `async_kiwoom_client.py`:
  - `CircuitBreaker` 클래스에 `@property is_open` 추가 (`self.state == "OPEN"`)
- `api_server.py`:
  - `get_bot_status` 내 서킷 브레이커 상태 확인 시 `cb.state == "OPEN"`, `cb.is_open`, `can_proceed()` 다중 폴백 및 `try-except` 방어 로직 적용
- `test_api_server.py`:
  - 서킷 브레이커 CLOSED/OPEN 상태 연동 단위 테스트 케이스 추가 및 검증 완료

### 3. 검증 결과
- **백엔드 테스트:** `python test_api_server.py` 및 `python test_async_trading_loop.py` 100% ALL PASS

---

## 📅 [2026-09-08] 키움 포털 URL(/kiwoom) 404 및 API/WebSocket 서브패스 라우팅 결함 해결

### 1. 작업 개요 및 목적
- `https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom` 접근 시 404 오류 및 서브패스(`/kiwoom/`) 환경에서 REST API 및 WebSocket 통신 단절 결함 완벽 해결.
- Nginx Ingress와 FastAPI StaticFiles 간 트레일링 슬래시 누락 보정 및 로컬/서브패스 듀얼 라우팅 구조 확립.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `/kiwoom` GET 요청 시 `/kiwoom/`으로 302 리다이렉트하는 보정 라우터 추가
  - `APIRouter`를 도입하여 `/api` 및 `/kiwoom/api` 두 접두사를 모두 처리하도록 등록
  - `/ws/portfolio`, `/kiwoom/ws/portfolio`, `/ws/logs`, `/kiwoom/ws/logs` 듀얼 WebSocket 엔드포인트 지원
- `frontend/src/utils/apiConfig.ts` (신규):
  - 런타임 `window.location.pathname`에 따라 `/kiwoom/api` 및 `/kiwoom/ws` 또는 `/api`, `/ws`를 반환하는 동적 URL 빌더
- `frontend/src/` 전역 컴포넌트:
  - `useWebSocket.ts`, `App.tsx`, `TradingViewChartBento.tsx`, `ParamsModal.tsx`, `LiveTerminalBento.tsx`, `ActivePositionsBento.tsx`에 `getApiUrl` 및 `getWsUrl` 적용
- `frontend/dist/`:
  - `npm run build`를 통해 Vite 프로덕션 번들 갱신 완료

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 성공 (0 errors)
- **백엔드 테스트:** `python test_api_server.py` 13개 테스트 스위트 100% 통과

---

## 📅 [2026-09-07] 장 마감/유휴 상태 계좌 잔고 및 포지션 영속 캐싱 보존 (0원 노출 방지)

### 1. 작업 개요 및 목적
- 장 마감(15:30 이후), 주말, 또는 API 세션 종료 상태에서 대시보드 접근 시 '총 평가자산', '예수금', '보유종목'이 0원으로 초기화되던 현상 해결.
- `api_server.py` 및 `main_rest_async.py` 기동 시 MariaDB(`balance`, `portfolio`)로부터 직전 정산 잔고와 보유 종목을 자동 복원하도록 라이프사이클 및 WebSocket 브로드캐스트 로직 보강.
- 프론트엔드 KPI 카드에 직전 동기화 기준 일시(`last_synced_at`) 표출 및 일시적 수신 공백 시 0원 덮어쓰기 방지 적용.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `lifespan`: 서버 부팅 시 MariaDB `balance` 및 `portfolio` 테이블에서 최신 계좌 잔고 및 종목 즉시 복원
  - `portfolio_broadcast_loop`: 메모리가 비어있더라도 DB에 저장된 직전 잔고/포지션 스냅샷을 지속적으로 WebSocket 브로드캐스팅
- `main_rest_async.py`:
  - `initialize`: 데몬 시작 시 DB 계좌 복원 로직 추가 및 장외시간 API 응답 실패 시 기존 DB 잔고 안전 유지
- `frontend/src/types.ts`: `PortfolioSnapshot`에 `last_synced_at?: string` 필드 추가
- `frontend/src/hooks/useWebSocket.ts`: REST/WebSocket 수신 시 유효 자산이 0으로 덮어써지지 않도록 방어 로직 추가
- `frontend/src/components/KpiMetricsRow.tsx`: 상단 KPI 카드에 `기준: YYYY-MM-DD` 동기화 일시 배지 표출
- `GSD_MASTERPLAN.md`: Phase 7 장 마감 계좌 상태 유지 마스터플랜 완료 반영

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 errors)
- **백엔드 테스트:** `pytest test_api_server.py` 및 25개 테스트 스위트 100% 통과

---

## 📅 [2026-09-07] 차세대 대시보드 실데이터 연동, 하드코딩 제거 및 TradingView OHLCV/피보나치 바인딩

### 1. 작업 개요 및 목적
- 차세대 대시보드에서 '삼성전자(005930)' 초기 하드코딩 및 난수 캔들 생성기(`Math.random()`)를 제거.
- 백엔드(`/api/chart/{code}`, `/api/portfolio`, `/api/watchlist`)의 DB/인메모리 연동을 완료하여 실제 잔고, 보유 포지션, 30개 주도주 감시 유니버스, 실제 OHLCV 캔들 및 피보나치 3대 지지선(38.2%, 50.0%, 61.8%)을 실시간 바인딩.
- 차트 상단에 보유종목 및 감시종목을 한눈에 보고 즉시 전환할 수 있는 '동적 빠른 종목 선택기(Quick Select UI)' 구현.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `GET /api/chart/{code}?period=1m|5m|D`: 인메모리 링버퍼, DB `minute_ohlcv`/`daily_ohlcv`, 키움 REST API 연계 3단계 폴백으로 실 캔들 및 피보나치 레벨 반환
  - `GET /api/portfolio`: 인메모리 + MariaDB(`portfolio`, `balance` 테이블) 폴백으로 실계좌 자산 및 포지션 반환
  - `GET /api/watchlist`: 감시종목 리스트/딕셔너리 정규화 반환
- `database.py`: `get_latest_balance`, `get_portfolio_positions`, `get_watchlist_items`, `get_candles_by_code` 비동기 조회 메서드 추가
- `frontend/src/App.tsx`:
  - 하드코딩 제거: 초기 선택값을 비우고, 실제 보유종목(1순위) 또는 감시종목(2순위)으로 자동 초기화
  - 감시종목 API 응답(배열/객체/딕셔너리) 파싱 방어 로직 강화
- `frontend/src/components/TradingViewChartBento.tsx`:
  - 더미 난수 루프 제거 및 `/api/chart/{code}` 실데이터 바인딩
  - 피보나치 3대 지지선 및 매수평단가 라인 실시간 오버레이
  - 헤더에 `<select>` 기반 빠른 종목 전환(보유/감시 그룹화) 드롭다운 UI 구현
- `frontend/src/types.ts`: `ChartResponse` 타입 추가
- `test_api_server.py`: `/api/chart/{code}` 실시간 캔들 및 피보나치 조회 테스트 케이스 추가 (100% 통과)
- `GSD_MASTERPLAN.md`: Phase 6 실데이터 연동 마스터플랜 추가

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 errors)
- **백엔드 테스트:** `pytest test_api_server.py` 및 전체 25개 단위 테스트 100% ALL PASS

---

## 📅 [2026-09-07] 차세대 React Bento Grid 트레이딩 콕핏 UI/UX 개편 및 멀티스테이지 배포 파이프라인 구축

### 1. 작업 개요 및 목적
- 기존 Streamlit 기반 대시보드의 8개 탭 분할 및 폴링 딜레이(15초)로 인한 높은 맥락 전환 피로도와 실시간성 부재를 해결.
- 1920x1080 단일 화면에서 스크롤 없이 모든 지표, 캔들 차트, 주도주 유니버스, 체결 로그를 실시간 관제할 수 있는 **차세대 React 18 + Vite + Tailwind CSS + TradingView Lightweight Charts + WebSocket Bento Grid Cockpit** 구축.
- ACR/AKS 및 로컬 환경에서 프론트엔드가 누락 없이 완벽 빌드/서빙되도록 **Dockerfile Node.js 20 멀티스테이지 빌드 파이프라인** 및 FastAPI (`/`, `/kiwoom`) 라우팅 서빙 구현.

### 2. 주요 변경 및 신규 파일 목록
- `frontend/`: React 18 + Vite + TypeScript + Tailwind CSS 벤토 그리드 프론트엔드 프로젝트
  - `src/App.tsx`: 4개 핵심 영역(차트, 포지션, 30개 주도주 감시, 실시간 터미널) 비대칭 벤토 그리드 레이아웃
  - `src/components/TradingViewChartBento.tsx`: 60FPS TradingView Lightweight Charts (MA5/20, 피보나치 지지선 오버레이)
  - `src/components/ActivePositionsBento.tsx`: 보유 포지션 실시간 PnL 카드 및 원클릭 분할매도/청산
  - `src/components/WatchlistBento.tsx`: 당일 거래대금 상위 30 주도주 & 눌림목 상태 테이블
  - `src/components/LiveTerminalBento.tsx`: WebSocket 실시간 터미널 로그 및 $k$/Kelly 계수 무중단 튜닝
  - `src/components/KpiMetricsRow.tsx`: 4대 핵심 지표 (평가자산, 실현손익, 예수금비중, 당일승률)
  - `src/hooks/useWebSocket.ts`: 초저지연 WebSocket (`/ws/portfolio`, `/ws/logs`) 스트리머
  - `vite.config.ts`: 상대경로(`base: './'`) 빌드 지원으로 Ingress 서브패스 호환성 확보
- `Dockerfile`: Node.js 20 멀티스테이지 프론트엔드 빌드 + Python 3.11 런타임 이미지 통합
- `.dockerignore`: `node_modules`, `dist`, `.env`, `*.log` 등 불필요한 빌드 아티팩트 제외
- `start.py`: AKS Ingress 타겟 포트(8501)에 맞춰 차세대 콕핏(FastAPI)을 8501로 승격, 스트림릿을 8000으로 배치
- `dashboard.py`: FastAPI 호출 포트(8501) 정합성 조정
- `api_server.py`: FastAPI에서 `/` 및 `/kiwoom` 양쪽 경로로 React 콕핏 정적 서빙 마운트
- `AKS_DEPLOYMENT.md`: 업로드 필수 파일 목록에 `frontend/` 추가 및 콕핏 접속 URL 가이드 갱신
- `DASHBOARD_UX_PLAN.md`: UI/UX 고도화 마스터플랜 문서

### 3. 자동 코드 리뷰 요약
- **우수한 점:** 
  - 1920x1080 기준 스크롤 제로 벤토 그리드 아키텍처로 트레이더의 시선 이동 최소화.
  - TradingView 캔들스틱과 피보나치 레벨(38.2%, 50%, 61.8%) 시각화로 기술적 진입/청산 타점 직관적 확인 가능.
  - Dockerfile 멀티스테이지 도입으로 클라우드 배포 시 별도 로컬 번들링 없이 `az acr build`에서 자동 빌드 보장.
- **방어 로직 & 엣지 케이스:**
  - WebSocket 끊김 발생 시 3초 주기 자동 재연결(`connectSockets`) 및 REST API 3초 폴백 동기화 구현.
  - Ingress 경로(`/kiwoom`) 접속 시 정적 에셋(`assets/...`) 404를 방지하기 위해 Vite `base: './'` 설정 및 FastAPI 이중 마운트 적용.

### 4. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 error, 11s)
- **백엔드 API 서버 테스트:** `pytest test_api_server.py` 1 passed
- **비동기 코어 테스트:** `test_async_core.py` 3/3 passed (PriorityQueue, TokenBucket, AsyncPortfolio)

### 5. 후속 안내
- 로컬 실행 시: `python start.py` 실행 후 `http://localhost:8000` 접속
- AKS 배포 시: `frontend/` 소스코드를 포함하여 `az acr build` 수행 후 `kubectl rollout restart deployment/kiwoom-bot -n mzc-apps`
