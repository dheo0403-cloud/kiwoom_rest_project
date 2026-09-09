# 🚀 키움 REST API 퀀트 자동 매매 시스템 GSD 마스터플랜 (GSD_MASTERPLAN.md)

본 문서는 `kiwoom_rest_project`를 상용 수준의 초저지연(Low-Latency), 적응형(Adaptive) 퀀트 자동 매매 시스템으로 고도화하기 위한 **Get-Shit-Done(GSD) 실행 계획서**입니다. 모든 과제는 의존성에 따라 선행되어야 할 핵심 인프라 작업부터 순차적으로 배치되어 있습니다.

---

## 📊 작업 의존성 로드맵 (Dependency Graph)

```
[Phase 1: 아키텍처 리팩토링 & 인메모리 링버퍼] (최우선 과제)
  ├─ 1.1 레거시 동기식 코드 격리 및 단일 비동기 런타임 확립
  ├─ 1.2 인메모리 링버퍼(market_data_buffer.py) 및 비동기 배치 DB 영속화 구현
  ├─ 1.3 선점형 우선순위 큐(CRITICAL/HIGH) 및 Graceful Shutdown 강화
  └─ 1.4 Phase 1 단위/통합 테스트 검증 (test_market_data_buffer.py)
                   │
                   ▼
[Phase 2: 적응형 퀀트 매매 전략 & 리스크 관리]
  ├─ 2.1 기술적 지표 모듈 확장 (indicators.py: ATR, 샹들리에, 볼린저스퀴즈)
  ├─ 2.2 ATR 적응형 변동성 돌파 진입 & 샹들리에 엑시트 엔진 (strategy.py)
  ├─ 2.3 프랙셔널 켈리 공식(Fractional Kelly) 자산 배분 (async_portfolio.py)
  └─ 2.4 Phase 2 전략 시뮬레이션 및 단위 테스트 검증
                   │
                   ▼
[Phase 3: 고충실도 백테스팅 & WFO 최적화 엔진]
  ├─ 3.1 슬리피지(0.1%) / 세금·수수료(0.20%) 정밀 마찰비용 모델링 (backtest.py)
  ├─ 3.2 분봉 바 내부 비관적 체결(Pessimistic Execution) 시뮬레이션
  └─ 3.3 Walk-Forward Optimization (WFO) 및 몬테카를로 분석 스크립트
                   │
                   ▼
[Phase 4: 실시간 알림 봇 & 원격 킬스위치 관제]
  ├─ 4.1 비동기 텔레그램 실시간 체결/경보 알림 봇 (notifier.py)
  ├─ 4.2 FastAPI 원격 킬스위치 (POST /api/bot/emergency-stop) 및 제어 API
  └─ 4.3 WebSocket 기반 실시간 스트리밍 대시보드 연동 (dashboard.py)
```

---

## 🛠️ 세부 작업 분할 (Task Breakdown)

### Phase 1. 아키텍처 리팩토링 및 인메모리 링버퍼 구축 (Priority 1 - 완료)

- [x] **Task 1.1: 레거시 동기식 코드 격리 및 비동기 진입점 단일화**
  - **설명:** 과거 동기식 파일(`main_rest.py`, `kiwoom_rest.py`, `portfolio.py`)을 `legacy/` 디렉터리로 안전하게 이동 격리.
  - **수정/생성 파일:**
    - `legacy/` 디렉터리 신설 및 구버전 파일 이동
    - `start.py`: 서브프로세스 실행 대상을 `main_rest_async.py`로 완전 일원화
    - `EXECUTION_GUIDE.md`: 비동기 봇 구동 단일 명령어로 최신화
  - **검증 기준:** `python start.py` 및 가상 환경에서 레거시 참조 없이 데몬 정상 부팅.

- [x] **Task 1.2: 인메모리 링버퍼(`market_data_buffer.py`) 및 비동기 배치 DB 영속화 구현**
  - **설명:** 매 루프마다 MariaDB를 직접 조회하던 `SELECT * FROM minute_ohlcv` 쿼리를 제거하고, 최근 60개 분봉 및 틱 데이터를 메모리에 상주시켜 0.1ms 내에 지표 연산 처리.
  - **수정/생성 파일:**
    - `market_data_buffer.py` (신규): `CircularCandleBuffer` 클래스 (`collections.deque(maxlen=60)` 활용)
    - `database.py`: `asyncio.Queue` 기반의 비동기 백그라운드 배치 인서트 워커 (`batch_insert_candles`) 구현
  - **검증 기준:** 10개 종목 동시 틱 유입 시 DB 쿼리 수 90% 이상 절감 및 메모리 버퍼 슬라이싱 테스트 통과.

- [x] **Task 1.3: 비동기 봇 코어(`main_rest_async.py`)에 링버퍼 연동 및 Graceful Shutdown 완성**
  - **설명:** `AsyncTradingBot`의 시세 수신 루프를 링버퍼 구조로 전환하고, `SIGINT`/`SIGTERM` 수신 시 메모리 잔여 데이터 DB Flush 및 안전 종료 보장.
  - **수정/생성 파일:**
    - `main_rest_async.py`: `MarketDataBuffer` 주입 및 셧다운 훅 연결
  - **검증 기준:** 비정상 강제 종료 시그널 전송 시 버퍼 데이터의 정상 DB 반영 및 소켓/풀 안전 해제.

- [x] **Task 1.4: Phase 1 단위/통합 테스트 스위트 작성 및 검증**
  - **설명:** 신규 링버퍼와 비동기 봇의 정상 동작을 검증하는 테스트 코드 작성.
  - **수정/생성 파일:**
    - `test_market_data_buffer.py` (신규)
    - `test_async_trading_loop.py` (최신화)
  - **검증 기준:** `python -m unittest test_market_data_buffer.py` 및 `test_async_core.py` 100% 통과.

---

### Phase 2. 적응형 퀀트 매매 전략 및 리스크 관리 고도화 (Priority 2 - 완료)

- [x] **Task 2.1: 기술적 지표 모듈 확장 (`indicators.py`)**
  - **설명:** ATR(Average True Range, 14), 샹들리에 엑시트 스탑선, 볼린저 밴드 + 켈트너 채널 스퀴즈 지표 수식 추가.
  - **수정 파일:** `indicators.py`, `test_indicators.py`
  - **검증 기준:** TA-Lib 표준 수식 대비 오차율 0.01% 미만 단위 테스트 통과.

- [x] **Task 2.2: ATR 변동성 돌파 진입 및 샹들리에 트레일링 스탑 적용 (`strategy.py`)**
  - **설명:** 고정 -4% 손절 / 2.5% 트레일링을 종목별 ATR 기반 동적 샹들리에 엑시트($\text{Peak} - 2.5 \times \text{ATR}$)로 전면 개편.
  - **수정 파일:** `strategy.py`
  - **검증 기준:** 변동성 구간별(저변동/고변동) 동적 손절선 자동 조정 검증.

- [x] **Task 2.3: 프랙셔널 켈리(Fractional Kelly) 자산 배분 알고리즘 (`async_portfolio.py`)**
  - **설명:** 최근 $N$회 승률과 손익비를 추적하여 1회 주문 수량을 가변적으로 배정 ($0.05 \le \text{Alloc} \le 0.25$).
  - **수정 파일:** `async_portfolio.py`
  - **검증 기준:** 연패 시 베팅 비중 자동 축소, 승률 상승 시 점진적 비중 확대 검증.

---

### Phase 3. 고충실도 백테스팅 및 WFO 최적화 엔진 구축 (Priority 3 - 완료)

- [x] **Task 3.1: 마찰비용(슬리피지 0.1%, 세금·수수료 0.20%) 정밀 백테스터 (`backtest.py`)**
  - **설명:** 단순 일봉 벡터화에서 분봉 바 내부 체결 및 현실적 거래 비용 모델이 탑재된 이벤트 기반 백테스터로 개편.
  - **수정 파일:** `backtest.py`
  - **검증 기준:** 실전 체결 손익률과의 괴리율(Discrepancy) 5% 이내 수렴.

- [x] **Task 3.2: Walk-Forward Optimization (WFO) 파라미터 튜닝 엔진**
  - **설명:** 인샘플(70%) / 아웃오브샘플(30%) 롤링 윈도우 검증으로 과최적화(Overfitting) 차단.
  - **검증 기준:** $k$ 돌파 계수($0.4 \sim 0.7$) 및 ATR 승수 최적값 도출.

---

### Phase 4. 실시간 알림 봇 및 관제 대시보드/킬스위치 (Priority 4 - 완료)

- [x] **Task 4.1: 비동기 텔레그램 실시간 알림 엔진 (`notifier.py`)**
  - **설명:** 매수/매도 체결, 스탑로스, MDD -5% 서킷브레이커, 일일 정산 리포트 즉시 푸시 알림.
  - **수정/생성 파일:** `notifier.py` (신규)
  - **검증 기준:** 비동기 논블로킹 메시지 큐 전송 및 텔레그램 봇 응답 확인.

- [x] **Task 4.2: FastAPI 비상 킬스위치 및 무중단 제어 API (`api_server.py`)**
  - **설명:** `POST /api/bot/emergency-stop` 호출 시 즉시 전 포지션 시장가 청산 및 신규 주문 락.
  - **수정 파일:** `api_server.py`
  - **검증 기준:** 비상 호출 시 1초 이내 전량 청산 주문 큐 등록 및 상태 전이 확인.

- [x] **Task 4.3: WebSocket 기반 실시간 스트리밍 대시보드 (`dashboard.py`)**
  - **설명:** DB 폴링 제거, WebSocket 브로드캐스터 기반 실시간 체결 로그/포트폴리오 스트리밍 시각화.
  - **수정 파일:** `dashboard.py`

---

### Phase 5. 거래대금 상위 스캔 및 감시종목(Watchlist) 파싱 엔진 강인화 & 상세 디버깅 관제 (Priority 5 - 완료)

- [x] **Task 5.1: 거래대금 상위(`ka10032`) 및 일봉차트(`ka10081`) 키움 REST 다중 스키마 키 100% 매핑 대응**
  - **설명:** 키움 REST API 응답 키 규격(`trde_prica_upper`, `stk_dt_pole_chart_qry`, `high_pric`, `low_pric`, `open_pric`, `cur_prc`)과 모의/영문 규격(`output`, `output2`, `hgpr`, `lwpr`, `prpr`)을 모두 포괄하는 다중 폴백 추출 엔진 구현.
  - **수정 파일:** `main_rest_async.py`, `async_kiwoom_client.py`
  - **검증 기준:** 키움 실전 REST 스키마 및 모의/테스트 스키마에서 0건 누락 없이 종목 수집 및 피보나치 레벨 계산 완료.

- [x] **Task 5.2: 필터링 및 캔들 연산 단계별 탈락 사유 실시간 집계 상세 디버그 로그(Debug Log) 추가**
  - **설명:** API 수신 원본 개수, 종목코드 오류, 일봉 데이터 부재, 캔들 수 부족, 고저차 0, 연산 오류 등 단계별 탈락 사유를 카운팅하여 콘솔에 요약 디버그 리포트(`[Watchlist Debug]`) 출력.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 감시종목 스캔 시 수신 원본 건수 및 단계별 탈락 집계가 실시간으로 완벽 출력.

- [x] **Task 5.3: 모의투자/장외시간 거래대금 상위 미제공 시 우량주(삼성전자 등 5종목) 자동 Fallback 안전망 구축**
  - **설명:** 장외 시간 또는 모의투자 환경에서 거래대금 상위 TR이 빈 배열을 반환할 때 대표 우량주 5종목을 자동 주입하여 봇 전략 루프가 중단 없이 가동되도록 보장.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 빈 API 응답 시 Fallback 5종목 정상 피보나치 분석 및 감시 등록 검증.

- [x] **Task 5.4: 전략 3단계 분할 익절(전량 청산) 정합성 보완 및 비동기 퀀트 테스트 스위트 100% 통과**
  - **설명:** 3차 ATR R3 분할 익절(+8% 이상) 로직 추가 및 `test_async_trading_loop.py`에 거래대금 상위 다중 스키마/Fallback 통합 검증 테스트 신설.
  - **수정 파일:** `strategy.py`, `market_data_buffer.py`, `test_async_trading_loop.py`
  - **검증 기준:** `test_async_trading_loop.py` 및 전체 9개 테스트 스위트 100% ALL PASS.

---

### Phase 6. 차세대 트레이딩 콕핏 실데이터 바인딩 및 동적 인터랙션 고도화 (Priority 6 - 진행 중)

- [x] **Task 6.1: 백엔드 실데이터 엔드포인트(`GET /api/chart/{code}`) 신설 및 DB/인메모리 연동 강화**
  - **설명:** 키움 API 일봉/분봉 조회 및 인메모리 링버퍼/DB로부터 실제 OHLCV 캔들과 피보나치 3대 지지선(38.2%, 50.0%, 61.8%)을 반환하는 엔드포인트 구현. `/api/portfolio`, `/api/watchlist`의 DB 테이블 연동 강화로 실계좌 잔고 및 종목 정보 실시간 동기화.
  - **수정 파일:** `api_server.py`, `database.py`
  - **검증 기준:** `/api/chart/005930`, `/api/portfolio`, `/api/watchlist` 호출 시 실제 자산/종목 데이터 정확히 반환.

- [x] **Task 6.2: 프론트엔드 하드코딩 제거 및 보유/감시 종목 기반 동적 선택(Auto-Selection & Quick Select) 구현**
  - **설명:** 하드코딩된 '005930'/'삼성전자' 기본값을 제거하고, 실제 보유종목(1순위) 또는 감시종목 최상위(2순위)를 자동 로드. 차트 헤더에 빠른 종목 전환 드롭다운/칩스 UI 추가.
  - **수정 파일:** `frontend/src/App.tsx`, `frontend/src/components/TradingViewChartBento.tsx`, `frontend/src/components/Header.tsx`
  - **검증 기준:** 페이지 로드 시 실제 계좌/감시 종목이 차트에 기본 렌더링되며, 클릭 또는 선택 시 즉시 전환.

- [x] **Task 6.3: TradingView Lightweight Charts 실데이터 OHLCV 및 피보나치 레벨 실시간 바인딩**
  - **설명:** 더미 `Math.random()` 캔들 생성기를 완전 제거하고, 백엔드 `/api/chart/{code}`로부터 수신한 실제 캔들 및 피보나치 지지선, 매수평단가 라인을 60FPS 하드웨어 가속 캔버스에 정확히 렌더링.
  - **수정 파일:** `frontend/src/components/TradingViewChartBento.tsx`, `frontend/src/hooks/useWebSocket.ts`
  - **검증 기준:** 캔들스틱, 거래량 바, 피보나치 3대 지지선, 매수평단가 라인이 실데이터로 오버레이 렌더링.

- [x] **Task 6.4: 빌드 검증, E2E 통합 테스트 및 형상 관리(`feature/dashboard-real-data-binding`)**
  - **설명:** Vite 프로덕션 빌드, 백엔드 테스트 스위트 검증 완료 후 전용 브랜치에 원자적 커밋 생성.
  - **수정 파일:** `frontend/dist/`, `WORK_HISTORY.md`
  - **검증 기준:** `npm run build` 성공 및 `pytest` 통과.

---

### Phase 7. 장 마감/유휴 상태 계좌 데이터 영속화 및 대시보드 상태 보존 (Priority 7 - 완료)

- [x] **Task 7.1: api_server.py 기동 시 MariaDB 계좌/포지션 즉시 복원 및 WebSocket 브로드캐스트 보강**
  - **설명:** 서버 기동 시 `ctx.db`에서 마지막 잔고(`balance`)와 보유 포지션(`portfolio`)을 즉시 읽어와 `ctx.portfolio`에 복원. 장 마감 또는 API 세션 종료 시에도 직전 계좌 상태가 0원으로 초기화되지 않도록 보장.
  - **수정 파일:** `api_server.py`, `database.py`
  - **검증 기준:** 장 마감/휴일 상태에서 api_server 재기동 시 0원이 아닌 직전 정산 잔고와 보유 종목이 즉시 조회됨.

- [x] **Task 7.2: main_rest_async.py 장 마감 정산 데이터 영속화 및 API 빈 응답 방어**
  - **설명:** 15:30 장 마감 시 최종 포트폴리오 스냅샷을 DB에 즉시 영속화하고, 야간/주말 키움 API가 빈 값을 반환하더라도 이전 DB 잔고를 0으로 덮어쓰지 않도록 방어 로직 강화.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 장 마감 후 휴면 루프 진입 시 DB에 최종 잔고와 보유종목이 안전하게 보존됨.

- [x] **Task 7.3: 프론트엔드 대시보드 마지막 동기화 일시 표출 및 0원 덮어쓰기 방지**
  - **설명:** 상단 4대 KPI 카드 및 헤더에 "마지막 동기화: YYYY-MM-DD HH:MM (장마감)" 배지 표출, WebSocket 일시 수신 공백 시 기존 유효 자산 데이터 유지.
  - **수정 파일:** `frontend/src/components/KpiMetricsRow.tsx`, `frontend/src/components/Header.tsx`, `frontend/src/hooks/useWebSocket.ts`
  - **검증 기준:** 장 마감 상태에서도 직전 평가자산/예수금/손익이 정상 노출되며 기준 일시가 명확히 표출됨.

- [x] **Task 7.4: 로컬/오프마켓 시뮬레이션 검증 및 Git 형상 관리(`fix/account-balance-after-market-close`)**
  - **설명:** 장 마감 오프마켓 상태 테스트 통과 후 브랜치 커밋 완료.
  - **수정 파일:** `test_api_server.py`, `WORK_HISTORY.md`
  - **검증 기준:** 모든 단위 테스트 통과 및 브랜치 커밋.

---

### Phase 8. 실시간 매매 실행 파이프라인 심층 디버깅 및 매수 판단 가시화 (Priority 8 - 완료)

- [x] **Task 8.1: 실시간 가격 수신 및 감시종목 지표 메타데이터 보강 (`main_rest_async.py`, `strategy.py`)**
  - **설명:** 감시종목 갱신 시 `avg_volume`, `period_high`, `period_low`, `fib_382`, `fib_500`, `fib_618`를 전략 지표 딕셔너리(`ind`)에 정확히 주입하고, 시가 추출 키(`oprc`, `stck_oprc`, `open_pric`, `open`) 다중화 지원.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 30개 감시종목의 피보나치 레벨 및 시세 지표가 누락 없이 전략 엔진으로 전달됨.

- [x] **Task 8.2: 매수 조건 판단부 상세 디버깅 로깅 및 원인별 상태 가시화 (`main_rest_async.py`, `strategy.py`)**
  - **설명:** 봇이 매수를 안/못하는 상태를 즉시 파악할 수 있도록 3단계 상세 실시간 로그 출력:
    1) 타점 대기: `⏱ [실시간 감시] 종목명 - 현재가: X원 / 목표 타점(Fib 38.2%): Y원 ➔ 대기 중 (괴리율: +Z%)`
    2) 예수금 부족: `⚠️ [매수 실패/자금부족] 종목명 - 타점 도달했으나 예수금 부족 (현재 예수금: A원 / 필요: B원)`
    3) 전략 필터/제한: `⚠️ [매수 스킵] 종목명 - 20일선 역배열 / 거래대금 부족 / 시간외 매수 제한`
  - **수정 파일:** `main_rest_async.py`, `strategy.py`
  - **검증 기준:** 정규 매매 루프 실행 시 각 감시종목의 상태와 매수 미실행 원인이 실시간으로 투명하게 로깅됨.

- [x] **Task 8.3: 켈리 수량 계산 및 소액 예수금(11만 원대) 1주 매수 안전 처리 (`async_portfolio.py`)**
  - **설명:** 총 자산 대비 켈리 비중(20%) 계산 시 소액 계좌에서 수량이 0이 되는 현상을 방지하기 위해, 가용 현금이 1주 가격 이상일 경우 최소 1주 매수를 허용하도록 보강.
  - **수정 파일:** `async_portfolio.py`
  - **검증 기준:** 114,922원 예수금 기준 73,000원 주식(삼성전자)은 1주 매수 가능, 150,000원 주식(SK하이닉스)은 자금 부족 로그 출력 확인.

- [x] **Task 8.4: 단위/통합 테스트 검증 및 Git 형상 관리 (`feature/trading-pipeline-debug`)**
  - **설명:** `test_async_trading_loop.py`에 상세 디버그 로깅 및 소액 예수금 매수 시뮬레이션 테스트를 추가하고 통과 확인 후 커밋.
  - **수정 파일:** `test_async_trading_loop.py`, `WORK_HISTORY.md`
  - **검증 기준:** 모든 단위 테스트 100% PASS.

---

### Phase 9. 서킷 브레이커 is_open AttributeError 해결 및 상태 조회 방어 로직 강화 (Priority 9 - 완료)

- [x] **Task 9.1: CircuitBreaker 클래스에 `is_open` 프로퍼티 및 헬퍼 메서드 추가 (`async_kiwoom_client.py`)**
  - **설명:** `CircuitBreaker` 객체에 `state == "OPEN"`을 반환하는 `@property is_open`을 추가하여 호출 규격을 일원화.
  - **수정 파일:** `async_kiwoom_client.py`
  - **검증 기준:** `cb.is_open` 프로퍼티 접근 시 서킷 상태(True/False) 정확히 반환.

- [x] **Task 9.2: api_server.py의 `get_bot_status` 서킷 브레이커 상태 확인부 다중 방어 로직 적용 (`api_server.py`)**
  - **설명:** `is_open()` 메서드 직접 호출 대신 `cb.state == "OPEN"`, `cb.is_open`, `can_proceed()` 순차 Fallback 및 예외 방어 로직 적용하여 AttributeError 및 로그 도배 원천 차단.
  - **수정 파일:** `api_server.py`
  - **검증 기준:** `GET /api/status` 및 `GET /kiwoom/api/status` 호출 시 콘솔 에러 발생 제로.

- [x] **Task 9.3: 로컬 테스트 및 Git 형상 관리 (`fix/circuit-breaker-is-open-error`)**
  - **설명:** `test_api_server.py` 단위 테스트 검증 및 전용 브랜치 커밋.
  - **수정 파일:** `test_api_server.py`, `WORK_HISTORY.md`
  - **검증 기준:** `test_api_server.py` 100% ALL PASS.

---

### Phase 10. 대시보드 실시간 터미널 LogViewer 개편, D+2 예수금 단일화 및 실시간 감시 쓰로틀링 루프 부활 (Priority 10 - 완료)

- [x] **Task 10.1: 예수금 기준 D+2 주문가능금액 단일화 및 미체결 주문 추적 (`main_rest_async.py`, `async_kiwoom_client.py`, `database.py`)**
  - **설명:** 당일 예수금(`entr`)과 D+2 추정예수금(`dnca_tot_amt`/`ord_psbl_cash`) 간의 혼용으로 인한 유령 차감 오해를 해결하고, 기준을 'D+2 실제 주문가능금액'으로 전면 통일. 미체결 주문 현황(`미체결: N건`) 로깅 및 정산 내역 표출.
  - **수정 파일:** `main_rest_async.py`, `async_kiwoom_client.py`, `database.py`
  - **검증 기준:** 계좌 싱크 시 D+2 주문가능금액이 정확히 계산되고 미체결 건수가 로그에 명시됨.

- [x] **Task 10.2: 실시간 틱 수신 루프 버그 수정 및 감시 로그 쓰로틀링(Throttling) 적용 (`main_rest_async.py`)**
  - **설명:** `trading_loop` 내 `try-finally` 배치 오류로 첫 틱 수신 후 워커 태스크가 즉각 취소되던 버그를 해결하고 자동 복구 Watchdog 추가. 매 틱마다 매수 평가는 무조건 실행하되, `⏱ [실시간 감시]` 로그는 종목당 5초 주기 또는 괴리율 0.5% 이상 변동 시에만 출력하도록 쓰로틀링 적용.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 실시간 시세 스트림이 멈추지 않고 지속 동작하며, 터미널 로그가 적절한 주기로 매끄럽게 출력됨.

- [x] **Task 10.3: 프론트엔드 미작동 차트 제거 및 터미널 감성 <LogViewer/> 컴포넌트 & WebSocket 로그 스트리밍 구현 (`frontend/src/`, `api_server.py`)**
  - **설명:** 미작동하던 차트 컴포넌트를 완전히 걷어내고, 검은색 배경의 터미널 스타일 `<LogViewer/>` 컴포넌트 신규 구현. 최근 100줄 로그, 자동 스크롤(Auto-scroll), 로그 레벨 필터(전체/감시/체결/경보/시스템), REST `/api/logs` 및 WebSocket `/ws/logs` 듀얼 연동.
  - **수정 파일:** `frontend/src/components/LogViewer.tsx`, `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/hooks/useWebSocket.ts`, `api_server.py`
  - **검증 기준:** 프론트엔드 대시보드에서 봇의 실시간 매매/감시 로그가 텍스트 스트리밍 방식으로 쏟아지며 자동 스크롤 동작.

- [x] **Task 10.4: 로컬 통합 검증 및 Git 형상 관리 (`feat/dashboard-log-viewer-and-core-fixes`) & AKS 배포 파일 목록 추출**
  - **설명:** 단위 테스트, 프론트엔드 빌드 검증, 신규 브랜치 커밋 및 AKS 배포 대상 파일 목록 추출.
  - **수정 파일:** `test_api_server.py`, `test_async_trading_loop.py`, `WORK_HISTORY.md`
  - **검증 기준:** 백엔드/프론트엔드 테스트 100% PASS, 브랜치 커밋 완료, AKS 배포 파일 목록 보고.

---

### Phase 11. 총 평가자산 및 D+2 주문가능 예수금 필드 독립 분리 & 정산 리포트 기말자산 모순 해결 (Priority 11 - 완료)

- [x] **Task 11.1: 총 평가자산 및 D+2 주문가능금액 독립 분리 파싱 (`main_rest_async.py`, `async_portfolio.py`)**
  - **설명:** 키움 API 계좌 조회 시 `tot_evlu_amt`/`aset_evlt_amt`(총 평가자산: 114,922원)과 `dnca_tot_amt`/`ord_psbl_cash`(주문가능 예수금: 100,842원)를 독립된 필드로 추출하여 포트폴리오 스냅샷에 보존.
  - **수정 파일:** `main_rest_async.py`, `async_portfolio.py`, `api_server.py`
  - **검증 기준:** 계좌 싱크 로그 및 KPI 카드에 총자산과 예수금이 별개로 정확히 구분되어 표출됨.

- [x] **Task 11.2: 일일 장 마감 정산 리포트 기말자산 계산 로직 교정 (`notifier.py`, `async_portfolio.py`)**
  - **설명:** 기초자산(114,922원) 대비 당일 손익이 0원일 때 기말자산이 총 평가자산(114,922원)으로 정확히 일치하도록 수정하고, D+2 주문가능 현금(100,842원)을 별도 항목으로 리포팅.
  - **수정 파일:** `notifier.py`, `test_notifier.py`
  - **검증 기준:** `python test_notifier.py` 및 정산 리포트 검증 통과.

- [x] **Task 11.3: 단위 테스트 및 Git 형상 관리 (`fix/account-balance-parsing-and-report`)**
  - **설명:** 단위 테스트 통과 후 전용 브랜치 커밋.
  - **수정 파일:** `test_async_trading_loop.py`, `WORK_HISTORY.md`, `GSD_MASTERPLAN.md`
  - **검증 기준:** 100% ALL PASS 및 브랜치 커밋.

---

### Phase 12. 실전투자(LIVE) 전환, KST 타임존 고정 및 헤더 잔고/포지션 드롭다운 UI 구현 (Priority 12 - 완료)

- [x] **Task 12.1: 키움 API 클라이언트 및 시스템 실전투자(REAL/LIVE) 모드 전면 전환 (`async_kiwoom_client.py`, `start.py`, `api_server.py`, `main_rest_async.py`)**
  - **설명:** 기본 접속 모드를 실전투자(REAL)로 전환하고 `LIVE (실전투자)` 에메랄드 배지 연동.
  - **수정 파일:** `async_kiwoom_client.py`, `start.py`, `api_server.py`, `main_rest_async.py`, `frontend/src/components/Header.tsx`
  - **검증 기준:** API 서버 및 봇 기동 시 REAL 모드로 가동되고 프론트엔드 헤더에 LIVE 뱃지 표출.

- [x] **Task 12.2: 백엔드 및 웹소켓 로그 타임존 KST(Asia/Seoul, UTC+9) 고정 (`database.py`, `api_server.py`, `frontend/src/hooks/useWebSocket.ts`)**
  - **설명:** MariaDB 로그 적재/조회 및 실시간 스트리밍 시 UTC로 표기되던 시간을 한국 표준시(KST)로 일괄 변환.
  - **수정 파일:** `database.py`, `api_server.py`, `frontend/src/hooks/useWebSocket.ts`
  - **검증 기준:** 터미널 및 로그 뷰어에 현재 한국 시간 기준 타임스탬프 표출.

- [x] **Task 12.3: 상단 헤더 실시간 잔고 & 보유 포지션 드롭다운 UI 및 다중 스키마 포지션 파싱 구현 (`Header.tsx`, `LogViewer.tsx`, `App.tsx`, `async_portfolio.py`)**
  - **설명:** 상단 헤더 중앙에 `[총자산: X원 | D+2 예수금: Y원 | 보유 종목: Z개 ▾]` 뱃지 바를 배치하고 클릭 시 보유 종목 상세(종목명, 코드, 수량, 매입가, 현재가, 평가손익, 수익률) 드롭다운 팝오버 렌더링. `kt00005` 내 다중 스키마 키 전수 파싱.
  - **수정 파일:** `frontend/src/components/Header.tsx`, `frontend/src/components/LogViewer.tsx`, `frontend/src/App.tsx`, `async_portfolio.py`
  - **검증 기준:** 헤더 및 로그 뷰어 상단에 실시간 계좌 잔고가 상시 노출되며, 드롭다운 클릭 시 보유 포지션 목록이 정상 표출됨.

- [x] **Task 12.4: 단위 테스트, Vite 빌드 검증 및 Git 형상 관리 (`feat/trading-environment-and-portfolio-ui`)**
  - **설명:** 단위 테스트 및 프로덕션 빌드 통과 후 전용 브랜치 커밋.
  - **수정 파일:** `WORK_HISTORY.md`, `GSD_MASTERPLAN.md`
  - **검증 기준:** 100% ALL PASS 및 브랜치 커밋.

---

### Phase 13. 키움 계좌 잔고 TR(kt00005/OPW00001/OPW00018) 총자산 vs D+2 예수금 필드 오맵핑 교정 및 정밀 파싱 엔진 구축 (Priority 13 - 진행 중)

- [ ] **Task 13.1: 총자산(total_asset) 및 D+2 주문가능 예수금(available_cash) 키 우선순위 및 계산식 전면 교정 (`main_rest_async.py`, `async_portfolio.py`)**
  - **설명:** `tot_evlu_amt`가 D+2 예수금(10만 원)과 동일하게 내려와 단순 예수금(11만 원)을 덮어씌우던 버그를 해결. `raw_entr_keys`(`prvs_rcdl_excc_amt`, `entr`, `deposit`, `asst_tot_amt`)를 최우선 순위로 파싱하여 `total_asset = (단순예수금 or 총자산필드) + 주식평가액`으로 산출하고, `available_cash`는 `d2_deposit_keys`(`dnca_tot_amt`, `d2_deposit`, `ord_psbl_cash`)로 엄격히 분리.
  - **수정 파일:** `main_rest_async.py`, `async_portfolio.py`
  - **검증 기준:** 키움 API 응답에서 11만 원대(예수금 원금/총자산)와 10만 원대(D+2 주문가능)가 독립적으로 정확히 파싱됨.

- [ ] **Task 13.2: 계좌 동기화 시 수신된 API 원본 데이터(Raw Data) 필드별 상세 디버그 로깅 강화 (`main_rest_async.py`)**
  - **설명:** 계좌 싱크 시 `prvs_rcdl_excc_amt`, `entr`, `deposit`, `dnca_tot_amt`, `tot_evlu_amt`, `ord_psbl_cash` 등 키움 API 응답의 주요 Raw 키/값들을 포맷팅하여 콘솔 및 DB 로그에 명확히 브리핑 출력.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 봇 기동 및 계좌 싱크 시 Raw 필드 매핑 내역 및 총자산/D+2 예수금/정산 차감액이 명확히 로깅됨.

- [ ] **Task 13.3: 백엔드 API/DB 및 프론트엔드 대시보드 렌더링 검증 & 단위 테스트 스위트 보강 (`test_async_trading_loop.py`, `api_server.py`, `database.py`)**
  - **설명:** `test_async_trading_loop.py`에 총자산(114,922원)과 D+2 예수금(100,842원) 분리 파싱 테스트 케이스 추가 및 `api_server.py`, `database.py` 데이터 무결성 검증.
  - **수정 파일:** `test_async_trading_loop.py`
  - **검증 기준:** `python -m pytest` 및 단위 테스트 100% PASS, 상단 UI에 [총자산: 11X,XXX원 | 주문가능(D+2): 10X,XXX원] 독립 표출 확인.

- [ ] **Task 13.4: Git 형상 관리 (`fix/account-balance-parsing-fields`) 및 WORK_HISTORY.md 갱신**
  - **설명:** 전용 브랜치 생성 후 원자적 커밋 및 작업 히스토리 기록.
  - **수정 파일:** `WORK_HISTORY.md`, `GSD_MASTERPLAN.md`
  - **검증 기준:** Git 브랜치 클린 및 커밋 완료.

---

## 📈 추진 일정 및 작업 진행 룰

1. **원칙:** 선행 과제가 테스트를 완전히 통과해야만 다음 과제로 진행한다.
2. **검증:** 각 Task 완료 시 반드시 단위 테스트를 실행하여 무결성을 입증한다.
3. **기록:** 작업 완료 시 `WORK_HISTORY.md` 및 `task-observer` 로그를 지속 갱신한다.
