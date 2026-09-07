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

### Phase 7. 장 마감/유휴 상태 계좌 데이터 영속화 및 대시보드 상태 보존 (Priority 7 - 진행 중)

- [ ] **Task 7.1: api_server.py 기동 시 MariaDB 계좌/포지션 즉시 복원 및 WebSocket 브로드캐스트 보강**
  - **설명:** 서버 기동 시 `ctx.db`에서 마지막 잔고(`balance`)와 보유 포지션(`portfolio`)을 즉시 읽어와 `ctx.portfolio`에 복원. 장 마감 또는 API 세션 종료 시에도 직전 계좌 상태가 0원으로 초기화되지 않도록 보장.
  - **수정 파일:** `api_server.py`, `database.py`
  - **검증 기준:** 장 마감/휴일 상태에서 api_server 재기동 시 0원이 아닌 직전 정산 잔고와 보유 종목이 즉시 조회됨.

- [ ] **Task 7.2: main_rest_async.py 장 마감 정산 데이터 영속화 및 API 빈 응답 방어**
  - **설명:** 15:30 장 마감 시 최종 포트폴리오 스냅샷을 DB에 즉시 영속화하고, 야간/주말 키움 API가 빈 값을 반환하더라도 이전 DB 잔고를 0으로 덮어쓰지 않도록 방어 로직 강화.
  - **수정 파일:** `main_rest_async.py`
  - **검증 기준:** 장 마감 후 휴면 루프 진입 시 DB에 최종 잔고와 보유종목이 안전하게 보존됨.

- [ ] **Task 7.3: 프론트엔드 대시보드 마지막 동기화 일시 표출 및 0원 덮어쓰기 방지**
  - **설명:** 상단 4대 KPI 카드 및 헤더에 "마지막 동기화: YYYY-MM-DD HH:MM (장마감)" 배지 표출, WebSocket 일시 수신 공백 시 기존 유효 자산 데이터 유지.
  - **수정 파일:** `frontend/src/components/KpiMetricsRow.tsx`, `frontend/src/components/Header.tsx`, `frontend/src/hooks/useWebSocket.ts`
  - **검증 기준:** 장 마감 상태에서도 직전 평가자산/예수금/손익이 정상 노출되며 기준 일시가 명확히 표출됨.

- [ ] **Task 7.4: 로컬/오프마켓 시뮬레이션 검증 및 Git 형상 관리(`fix/account-balance-after-market-close`)**
  - **설명:** 장 마감 오프마켓 상태 테스트 통과 후 브랜치 커밋 완료.
  - **수정 파일:** `test_api_server.py`, `WORK_HISTORY.md`
  - **검증 기준:** 모든 단위 테스트 통과 및 브랜치 커밋.

---

## 📈 추진 일정 및 작업 진행 룰

1. **원칙:** 선행 과제가 테스트를 완전히 통과해야만 다음 과제로 진행한다.
2. **검증:** 각 Task 완료 시 반드시 단위 테스트를 실행하여 무결성을 입증한다.
3. **기록:** 작업 완료 시 `WORK_HISTORY.md` 및 `task-observer` 로그를 지속 갱신한다.
