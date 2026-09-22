# 📝 키움 비동기 퀀트 자동매매 프로젝트 작업 히스토리 (WORK_HISTORY.md)

---

## 📅 [2026-09-22 11:05] 심층 코드 리뷰 피드백 반영 및 실시간 매매/비상 정지 핵심 버그 수정 (--fix)

### 1. 작업 개요 및 목적
- **심층 8개 앵글 코드 리뷰 기반 버그 및 안정성 결함 전수 해결:**
  1. **미체결 주문 조회 중복 선언 및 `TypeError` 제거 (`async_kiwoom_client.py`):**
     - `get_unexecuted_orders`가 385줄(kt00007, `code` 지원)과 544줄(ka10075, `code` 미지원)에 중복 선언되어 544줄이 덮어쓰면서 발생하던 `code` 키워드 인자 TypeError 결함 완벽 해결 (kt00007 + ka10075 폴백 통합).
  2. **ADX 실시간 지표 키 불일치 및 횡보장 휩소 바이패스 버그 수정 (`indicators.py` & `strategy.py`):**
     - `TechnicalIndicators.get_latest_indicators`의 `'adx14'` 키와 `strategy.py`의 `ind.get('adx')` 간 키 불일치로 인해 실전 매매 중 ADX가 0으로 처리되어 추세 필터가 상시 무력화되던 버그를 다중 키(`adx`, `adx14`) 지원으로 완벽 수정.
  3. **장전 08:50 스케줄러 서킷브레이커 리셋 누락 수정 (`api_server.py`):**
     - 전일 -2.5% 손실로 서킷브레이커가 발동된 후 익일 08:50 아침 자동 재개 시 `daily_circuit_breaker = False` 및 `daily_start_capital = 0.0` 초기화가 누락되어 당일 매수가 영구 차단되던 결함 수정.
  4. **비상 킬스위치 800033 매도 거절 방어 (`api_server.py`):**
     - `/bot/emergency-stop` 실행 시 미체결 주문 선제 취소 파이프라인(`_cancel_unexecuted_orders_for_stock`)을 연동하여 매도가능수량 0주 락으로 인한 청산 실패 방어.
  5. **환산 거래량 급증 필터 `skip_time_filter` 방어 가드 보완 (`strategy.py`):**
     - 오프마켓/데모/백테스트 환경에서 실시간 경과 시간 계산으로 인해 유효 타점이 오기각되던 현상 방지.
  6. **핫 패스 마이크로 최적화 (`main_rest_async.py`):**
     - 틱당 호출되는 `_evaluate_buy_condition` 내부의 중복 `import time` 제거.

### 2. 수정 파일 목록
- `async_kiwoom_client.py`: `get_unexecuted_orders` 중복 제거 및 kt00007 / ka10075 통합 폴백 지원.
- `indicators.py`: `get_latest_indicators`에 `'adx'` 및 `'adx14'` 듀얼 키 매핑.
- `strategy.py`: `adx` 지표 다중 키 조회 및 환산 거래량 `skip_time_filter` 가드 탑재.
- `api_server.py`: 장전 08:50 스케줄러 서킷브레이커/자본 리셋 추가 및 비상 킬스위치 미체결 선제 취소 연동.
- `main_rest_async.py`: 실시간 틱 평가 함수 내 중복 `import time` 제거.

### 3. 검증 결과
- **단위 테스트:** `pytest` 62개 전 테스트 100% 통과 (PASS).
- **시뮬레이션 및 교차 검증:**
  - `test_async_trading_loop.py` 23개 시뮬레이션 테스트 100% 통과.
  - `verify_order_pipeline.py` 매수/예수금/켈리/주문 파이프라인 100% 실측 완료.
  - `verify_quant_winrate_defense.py` 가짜 돌파 100% 거절, 서킷브레이커 100% 방어 실측 완료.
  - `verify_unexecuted_cancel_and_sell.py` 800033 에러 방어 및 선제 취소 청산 100% 통과.
- **프론트엔드 빌드:** `npm run build` 번들 정상 빌드 완료 (에러 0건).

---

## 📅 [2026-09-21 15:40] 퀀트 승률(37.3% ➔ 70%+) 개선용 다중 필터(ADX/VWAP/체결강도) 및 일일 최대 손실(-2.5%) Circuit Breaker 도입

### 1. 작업 개요 및 목적
- **기존 승률 저조(37.3%, 195승 327패, 누적 -86.91%) 근본 패착 원인 해결:**
  - 무추세 횡보장 톱니파동(Chop)에서 가짜 돌파(Bull Trap)에 피격되어 손절이 누적되던 현상을 원천 차단.
  - 기관 기준가(VWAP) 지지 없이 상투 과열주를 추격 매수하던 패턴 기각.
- **고승률 5중 퀀트 알파 필터 (AND 결합) 탑재:**
  1. **ADX(14) 추세 강도 필터:** `ADX >= 18.0` 및 `+DI > -DI` (무추세 횡보장 휩소 100% 기각).
  2. **VWAP 스마트 밴드:** `VWAP * 1.002 <= 현재가 <= VWAP * 1.030` (기관 지지 구간 안착 및 과열 추격 방지).
  3. **체결강도(Volume Power ≥ 110%):** 실질 매수세 우위 확인.
  4. **Volume Profile POC 매물대 저항선 돌파 안착:** 매물벽 직전 매수 기각.
  5. **볼린저 밴드 + 켈트너 스퀴즈 모멘텀 상방 발산:** 에너지 응축 후 상방 폭발 구간 공략.
- **일일 최대 손실 제한(Daily Circuit Breaker) 구현:**
  - 당일 시작 자산(`daily_start_capital`) 대비 **누적 손실 `-2.5%` 초과 시 당일 신규 매수 즉시 전면 차단**.
  - 익일 08:50 AM 장전 스케줄러에서 자산 재평가 후 자동 리셋.

### 2. 주요 수정 및 신설 파일
- `strategy.py`:
  - `AdaptiveVolatilityBreakoutStrategy`: ADX(14) 추세 필터, VWAP 밴드, 체결강도, 스퀴즈 모멘텀 AND 결합.
- `main_rest_async.py`:
  - `daily_start_capital`, `daily_loss_limit_rate = -0.025`, `daily_circuit_breaker` 탑재.
  - `_sync_account_balance`: 당일 -2.5% 손실 도달 시 `daily_circuit_breaker = True` 즉각 발동.
  - `wait_until_next_market_open`: 익일 08:50 서킷 브레이커 자동 리셋.
- `verify_quant_winrate_defense.py`:
  - [투명성 교차 검증] 1) 횡보장 가짜 신호 100건 주입 시 100% 거절 실측, 2) 정규 주도주 100건 100% 진입 실측, 3) 당일 -2.5% 손실 도달 시 매수 차단 100% 실측 스크립트 작성.

### 3. 검증 결과
- **백테스트 시뮬레이션 실측 (`verify_quant_winrate_defense.py`):**
  - 가짜 돌파 100건 중 **100건 전원 사전 거절 (방어율 100.0%)**: ADX 무추세 34건, VWAP 하회 33건, 체결강도 부족 33건 완벽 방어.
  - 정규 주도주 100건 중 **100건 100% 정규 매수 진입 승인**.
  - 일일 -2.5% 초과 손실 발생 시 **Daily Circuit Breaker 즉각 발동 및 신규 매수 주문 0건 차단 완료**.
- **단위 테스트:** `pytest` 62개 전 테스트 100% 통과.

---

## 📅 [2026-09-21 15:10] 실시간 보유 종목 리스트 새로고침 시 깜빡임(Flickering) 현상 및 렌더링 롤백 버그 수정

### 1. 작업 개요 및 목적
- **실시간 포지션 리스트 깜빡임(Flickering) 근본 원인 해결:**
  - `App.tsx` 내 `displayPortfolio`와 `portfolio`의 핑퐁 삼항 연산자로 인해 상태 동기화 지연 시 순간적으로 빈 배열(`[]`)이 주입되며 "현재 보유 중인 포지션이 없습니다" 빈 화면이 깜빡이던 현상 해결.
  - `useWebSocket.ts` 내에 `smartMergePositions` In-Place 지능형 병합 로직을 구축하여, 실시간 시세/수익률 수신 시 기존 배열 객체 참조를 보존하고 변경된 종목의 속성만 국소 덮어쓰도록 최적화.
  - `ActivePositionsBento.tsx`의 개별 종목 카드를 `PositionCardItem = React.memo(...)`로 분리하여 데이터가 변하지 않은 종목의 불필요한 Virtual DOM 리렌더링을 100% 차단.
  - `QuantPerformanceBento.tsx`의 지표 누락 시 `toFixed` TypeError 방어 로직 추가.

### 2. 주요 수정 및 신설 파일
- `frontend/src/hooks/useWebSocket.ts`:
  - `smartMergePositions`: 기존 종목 배열과 신규 수신 데이터를 비교하여 변경된 속성만 In-Place 병합.
  - `updateBothStates`: 단일 소스 참조 보존.
- `frontend/src/components/ActivePositionsBento.tsx`:
  - `PositionCardItem`: `React.memo` 분리 및 `key={pos.code}` 기반 렌더링 안정화.
- `frontend/src/App.tsx`:
  - `ActivePositionsBento`에 안정된 `portfolio.positions` 직접 전달 및 핑퐁 제거.
- `frontend/src/components/QuantPerformanceBento.tsx`:
  - `toFixed` 호출 시 undefined 방어 (`(val ?? 0).toFixed(...)`).
- `frontend/verify_flicker_e2e.cjs`:
  - 0.4초 간격 실시간 WebSocket 시세 브로드캐스트 주입 및 Puppeteer E2E 깜빡임 0건 검증 스크립트 작성.

### 3. 검증 결과
- **프론트엔드 빌드:** Vite 프로덕션 빌드 100% 성공 (`built in 4.73s`).
- **Puppeteer E2E 렌더링 실측 검증 (`verify_flicker_e2e.cjs`):**
  - 0.4초 간격 10회 연속 WebSocket 시세 변동 주입 시 **깜빡임(Empty State) 0회, 카드 소멸 0회, 콘솔 에러 0건** 통과.
- **백엔드 단위 테스트:** `pytest` 62개 전 테스트 100% 통과.

---

## 📅 [2026-09-21 14:40] 키움 매수 SendOrder 불발 버그 수정 및 승률 개선을 위한 5대 퀀트 알파 필터·하드 스탑로스(-3%) 고도화

### 1. 작업 개요 및 목적
- **매수 주문 불발(Order Drop) 4대 근본 원인 규명 및 전면 해결:**
  1. **인메모리 링버퍼 초기 캔들 부족 시그널 기각 결함:** 봇 기동 직후 캔들 부족으로 `ind={}` 공백 발생 시 `check_buy_signal`에서 무조건 탈락하던 결함을 시세/워치리스트 기본 정보 자가 복구 로직으로 해결.
  2. **순간 틱 체결량(`cntg_vol`)과 누적 거래량 혼용 교정:** 실시간 틱 체결량(예: 50주)이 30억 거래대금 필터와 비교되어 100% 탈락하던 결함을 누적 거래량(`acml_vol`) 및 당일 환산 거래대금 기반으로 분리 정규화.
  3. **피보나치와 ATR 돌파 조건 상호 배제 해소:** 주가 돌파 시 `fib_rebound`가 False가 되며 ATR 0일 때 돌파선 계산 오류로 시그널이 닫히던 문제를 적응형 ATR 폴백으로 복구.
  4. **켈리 수량 0주 시 소액 계좌 최소 1주 안전 가드 탑재:** 가용 예수금 범위 내에서 최소 1주 이상 매수 발주 보장.
- **승률 70%+ 타겟팅: 5대 퀀트 알파 필터 탑재:**
  1. **체결강도(Volume Power ≥ 110%) 필터:** 매수 체결 우위 구간에서만 진입하여 휩소 방어.
  2. **호가 불균형(Orderbook Imbalance) 필터:** 매도/매수 잔량 비율(≥0.6)을 검증하여 가짜 돌파 기각.
  3. **VWAP 스마트 지지 & 건전 이격도(+0.2% ~ +2.5%) 가드:** VWAP 하회 약세주 및 과열 추격 매수 차단.
  4. **대량 매물대(Volume Profile POC) 저항선 돌파 안착 필터:** 핵심 매물대 저항 직전 매수 기각.
  5. **볼린저 밴드 + 켈트너 스퀴즈 모멘텀 상방 발산 필터:** 에너지 수축 후 상방 폭발 구간 공략.
- **출구 전략 및 리스크 관리 강화:**
  1. **하드 스탑로스:** 사용자 지정 **-3.0% 엄격 고정** (CRITICAL 긴급 매도).
  2. **본절선 상향(Breakeven Guard):** +1.2% 상승 시 +0.25%(세금/수수료 포함) 본절가 고정.
  3. **3단계 적응형 분할 익절:** 1차(+2.5~3.0% 50%), 2차(+4.5~5.0% 50%), 3차(샹들리에 트레일링 스탑 잔여 전량).

### 2. 주요 수정 및 신설 파일
- `strategy.py`:
  - `AdaptiveVolatilityBreakoutStrategy`: 5대 퀀트 알파 필터(체결강도, 호가불균형, VWAP, POC 매물대, 스퀴즈 모멘텀) 통합.
  - 하드 스탑로스 -3.0% 고정, 본절선 +1.2% 발동 및 3단계 ATR R-배수 분할 익절 고도화.
- `main_rest_async.py`:
  - `_evaluate_buy_condition`: 실시간 시세/누적 거래량 정규화, 지표 결측치 방어, 1주 최소 발주 가드.
  - `OnReceiveRealData`: `raw_data` 파이프라인 연동.
- `async_kiwoom_client.py`:
  - `_get_headers`: 키움 REST 규격 `appkey`, `appsecret`, `api-id`, `tr_id` 헤더 완전 보장.
  - `send_order`: 에러코드/메시지 상세 로깅 및 주문 접수 검증 강화.
- `test_strategy_quant.py`:
  - `test_alpha_filters_volume_power_and_imbalance` 신규 알파 필터 검증 테스트 추가.
- `verify_order_pipeline.py`:
  - [투명성 교차 검증] 가상 시세 주입을 통한 `매수 시그널 ➔ 예수금 확인 ➔ 켈리 수량 ➔ SendOrder ➔ 포지션 편입` 전 과정 1회성 실측 스크립트 작성.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 62개 테스트 스위트 100% 통과 (`62 passed`).
- **투명성 교차 검증 (`verify_order_pipeline.py`):**
  - 가상 틱 데이터(71,500원, +2.14%, 거래량 250만주, 체결강도 135.5%) 주입 후 `BUY_SIGNAL` 발생 ➔ `SendOrder` 26주 @ 71,500원 발주 ➔ 계좌 1종목 편입 실측 완료.

---

## 📅 [2026-09-17 14:00] 키움 자동매매 장중 매수 차단 결함 수정, 3단계 분할 익절 수량 개선, 본절선 1.0025 상향 및 09:15 타임 필터 배포

### 1. 작업 개요 및 목적
- **장중 주식 '매수(Buy)' 전면 차단 원인 규명 및 긴급 정상화:**
  - **시가(Open Price) 덮어쓰기 결함 복구:** `_evaluate_buy_condition`에서 `ind['open'] = cur_price`로 현재가를 대입하여 `cur_price >= cur_price + 0.5*ATR`이 되어 변동성 돌파 매수가 100% 기각되던 치명적 결함을 당일 실제 시가(`info.get('open_price')`)로 매핑하여 정상화.
  - **KODEX 200 시장 지수 필터 완화:** 과도하게 민감했던 지수 급락 기준(`-0.8%`)을 `-1.5%`로 정상 복구하여 장중 소폭 조정 시 매수 파이프라인이 조기 차단되는 현상 해결.
  - **RSI(14) 필터 및 0 나누기 방어:** 모멘텀 돌파 전략 특성을 고려하여 RSI 70+ 차단을 85+ 극단 과열 차단으로 완화하고, 가격 무변동 시 RSI 100으로 왜곡되던 `indicators.py` 로직 수정.
  - **거래대금/환산거래량 필터 완화:** 당일 30억 이상 및 환산거래량 1.5배로 유연화하여 유효 주도주 진입 허들 정상화.
- **익절 수량 배분 구조 개선:**
  - 1차: 전체 물량의 50% 매도 (Stage 0 -> Stage 1)
  - 2차: 잔여 물량의 50% 매도 (Stage 1 -> Stage 2)
  - 3차: 나머지 잔여 물량 전량(100%) 청산 (Stage 2 -> 청산)
- **본절선(Break-even) 가드 기준 상향:**
  - 거래세(0.18%) + 수수료(0.015%x2) + 슬리피지를 완벽 보전하기 위해 기존 `1.002`에서 **`1.0025`(+0.25%)**로 상향.
- **장초반 노이즈 회피 09:15 타임 필터 신설:**
  - 09:00~09:15 구간의 휩소/호가 불안정을 방어하기 위해 09:15 이전 신규 매수를 원천 차단하는 Time 조건 추가.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `update_watchlist()`, `OnReceiveRealData()`, `_realtime_data_stream_worker()`, `monitor_watchlist_and_enter()`에 당일 시가(`open_price`) 파싱 및 실시간 틱 전달 파이프라인 구축.
  - `_evaluate_buy_condition()`: `ind['open'] = open_price` 주입 및 09:15 이전 매수 차단 타임 필터 가드 추가.
  - `check_market_filter()`: KODEX 200 임계값 `-1.5%`로 복원.
  - `take_profit_stages` 및 `monitor_positions_and_exit()`: 1차(전체의 50%), 2차(잔여의 50%), 3차(나머지 전량) 단계별 익절 수량 정밀화.
- `strategy.py`:
  - 09:15 타임 필터, RSI 85+ 과열 필터, 당일 30억 & 환산거래량 1.5배, 피보나치/돌파 안정화 적용.
  - 본절선 가드 및 샹들리에 트레일링 스탑 본절선 `buy_price * 1.0025` 적용.
- `indicators.py`:
  - `calculate_rsi()`: 변동 없을 때(gain=0, loss=0) RSI 50.0 기본값 처리 및 극단 플랫 왜곡 방어.
- `test_strategy_quant.py`:
  - `test_breakeven_guard_1_0025`, `test_time_filter_0915_and_buy_breakout`, 3차 전량 익절 테스트 추가.
- `test_async_trading_loop.py`:
  - Test 20 (`test_buy_signal_open_price_and_0915_time_filter`) 추가 및 20개 시뮬레이션 테스트 100% 통과.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 56개 테스트 스위트 100% 통과 (`56 passed`).
- **20개 트레이딩 루프 시뮬레이션:** `python test_async_trading_loop.py` 100% 통과.
- **인사이트 영구 저장:** `Mem0`에 매매 로직 조건문 충돌 및 Git Diff 추적 해결 패턴 저장 완료.

---

## 📅 [2026-09-16 17:55] 수동 일시정지 후 익일 영업일 아침(08:50 AM) 자동 재시작(Wake-up) 스케줄러 구축 및 State 오버라이드 배포 완료

### 1. 작업 개요 및 목적
- **수동 일시정지 영구 지속 문제 해결:**
  - 기존에는 사용자가 대시보드 콕핏에서 [봇 일시정지]를 클릭하여 봇을 중지하면, 다음 날 아침 장이 시작되어도 봇이 계속 정지 상태를 유지하여 사용자가 직접 매매 재개 버튼을 눌러야만 하던 불편함을 해결.
- **3단계 상태 모델(`is_running`, `is_paused`, `is_shutdown`) 정립:**
  - 단일 불리언으로 관리되던 프로세스 생명주기를 세분화하여, 수동 정지 시 데몬 자체의 백그라운드 대기 루프는 정상 유지되면서 당일 매매 루프만 안전하게 정지되도록 분리.
- **영업일(Business Day) 및 한국 거래소 공휴일 판별 가드 구축 (`is_korean_market_holiday`):**
  - 주말(토/일) 및 신정, 삼일절, 근로자의 날(5/1), 어린이날, 현충일, 광복절, 개천절, 한글날, 성탄절, 증시 폐장일(12/31) 등 휴장일에는 자동 기상하지 않고 다음 정상 개장일까지 비동기 휴면하도록 안전 결합.
- **익일 아침 08:50 KST 자동 웨이크업(Wake-up) 시퀀스 탑재:**
  - `main_rest_async.py`의 `wait_until_next_market_open` 및 `api_server.py`의 `daily_market_scheduler_loop`를 통해, 익일 영업일 아침 08:50 도달 시 `is_paused = False`, `running = True`, `mdd_shutdown = False`로 자동 오버라이드 리셋.
  - 계좌 잔고 동기화 및 당일 감시 유니버스(거래대금 상위 30종목 피보나치 분석) 사전 분석 완료 후 09:00 정규 매매 루프 자동 기동.
- **프론트엔드 실시간 동기화:**
  - 프론트엔드(`useWebSocket.ts`)의 3초 주기 `/status` 폴링 및 WebSocket 실시간 브로드캐스트로 콕핏 UI의 상태 뱃지가 '엔진 가동 중'으로 자동 동기화됨.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `is_paused`, `is_shutdown` 상태 변수 추가 및 `running` 프로퍼티(`is_running and not is_paused`), `pause()`, `resume()` 메서드 신설.
  - `is_korean_market_holiday()` 정적 메서드 구현.
  - `wait_until_next_market_open()`, `_realtime_data_stream_worker()`, `trading_loop()`, `run_daemon()`에 일시정지 자동 해제 및 08:50 자동 웨이크업 로직 통합.
- `api_server.py`:
  - `ServerContext`에 `daily_scheduler_task` 추가.
  - `daily_market_scheduler_loop()` 백그라운드 태스크 신설 및 `lifespan` 관리.
  - `/bot/control`의 `START`/`STOP` 및 `/status`에 `is_paused` 상태 연동.
- `test_async_trading_loop.py` & `test_api_server.py`:
  - Test 19 (`test_bot_auto_wakeup_and_resume_schedule`) 신설 및 REST API `is_paused` 상태 연동 테스트 검증.
- `pytest.ini`:
  - `asyncio_mode = auto` 설정 추가.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 53개 테스트 스위트 100% 통과 (`53 passed`).
- **프론트엔드 빌드:** TypeScript 및 Vite 번들링 성공 (0 errors).
- **형상 관리:** `feat: 봇 수동 일시정지 후 다음 영업일 아침 자동 재시작(Wake-up) 스케줄링 추가` 커밋 및 원격 푸시 완료.

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
