# 📝 키움 비동기 퀀트 자동매매 프로젝트 작업 히스토리 (WORK_HISTORY.md)

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
