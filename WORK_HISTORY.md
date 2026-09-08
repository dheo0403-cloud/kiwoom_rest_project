# 📝 키움 비동기 퀀트 자동매매 프로젝트 작업 히스토리 (WORK_HISTORY.md)

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
