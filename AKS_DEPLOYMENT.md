# 🚢 Azure Kubernetes Service (AKS) 배포 가이드 (AKS_DEPLOYMENT.md)

본 문서는 고도화된 키움 비동기 퀀트 자동매매 시스템(`kiwoom_rest_project`)을 **Azure Container Registry(ACR)** 및 **Azure Kubernetes Service(AKS)** 환경에 24시간 365일 무중단으로 빌드하고 배포하는 전체 절차와 업로드 파일 목록을 안내합니다.

---

## 1. 📂 업로드 대상 파일 목록 (Upload File Checklist)

서버(`~/clouddrive/deploy/kiwoom_rest_project`)에 업로드해야 하는 파일 목록입니다. 

> ⚠️ **보안 주의**: 로컬의 `.env` 파일은 컨테이너에 포함되지 않아야 합니다. DB 및 증권사 키는 AKS의 `Secret`(`kiwoom-credentials`, `db-credentials`)에서 환경 변수로 자동 주입됩니다.

### ✅ 필수 업로드 파일 및 디렉터리 목록

| 구분 | 파일/디렉터리명 | 역할 및 설명 |
| :--- | :--- | :--- |
| **빌드/설정** | `Dockerfile` | Multi-stage (Node.js 20 + Python 3.11) 컨테이너 빌드 설정 |
| | `.dockerignore` | 불필요한 캐시, node_modules, 테스트 파일 빌드 제외 설정 |
| | `requirements.txt` | 런타임 의존 패키지 목록 (FastAPI, Streamlit, Pandas 등) |
| **차세대 프론트엔드**| `frontend/` | React 18 + Vite + TradingView 캔들 + 벤토 그리드 소스코드 (`src/`, `package.json` 등 포함, `node_modules` 제외) |
| **통합 실행기** | `start.py` | 24/365 슈퍼바이저: 봇 데몬 + FastAPI(8000) + Streamlit(8501) 자가치유 관리 |
| **코어 엔진** | `main_rest_async.py` | 비동기 퀀트 트레이딩 봇 데몬 (장 마감 시 익일 08:55까지 자동 휴면) |
| | `async_kiwoom_client.py` | 키움 REST API 비동기 클라이언트 (토큰 버킷 & 서킷 브레이커) |
| | `async_portfolio.py` | 프랙셔널 켈리(Fractional Kelly) 자산 배분 & 포트폴리오 매니저 |
| **전략/지표** | `strategy.py` | ATR 적응형 변동성 돌파 진입 & 샹들리에 엑시트 출구 전략 |
| | `indicators.py` | ATR, 샹들리에, 볼린저 스퀴즈 모멘텀 기술적 지표 산출 엔진 |
| | `valuation.py` | 피오트로스키 F-Score & 펀더멘털 가치평가 엔진 |
| **데이터 레이어** | `market_data_buffer.py` | 인메모리 링버퍼(Ring-Buffer) & 비동기 5초 배치 DB 영속화 |
| | `database.py` | MariaDB 커넥션 풀 및 배치 인서트 매니저 |
| | `data_collector.py` | 당일 거래대금 상위 유니버스 수집기 |
| **알림 및 관제** | `notifier.py` | 카카오톡 '나에게 보내기' 실시간 알림 엔진 (토큰 자동 갱신) |
| | `api_server.py` | FastAPI 비상 킬스위치 & WebSocket + 차세대 콕핏 정적 서빙 |
| | `dashboard.py` | [레거시] 스트림릿 대시보드 |
| **분석 도구** | `backtest.py` | 슬리피지/세금(0.21%) 반영 고충실도 백테스터 & WFO 최적화 |

### ❌ 업로드 제외 대상 (업로드하지 않아도 되는 파일)
- `frontend/node_modules/` (Dockerfile 빌드 시 자동 설치됨)
- `legacy/` 디렉터리 (구버전 동기식 파일)
- `test_*.py` 파일 (로컬 단위 테스트 파일)
- `.git/`, `__pycache__/`, `.env`, `*.log`

---

## 2. 🛡️ 24시간 365일 무중단 아키텍처 동작 원리

과거에는 장 마감(15:30) 시 봇 프로세스가 완전히 exit하면서 부모 프로세스(`start.py`)가 대시보드까지 함께 종료시키는 현상이 있었습니다. 이를 완전히 해결하기 위해 **2단계 자가 치유 및 영구 데몬 아키텍처**를 적용하였습니다:

1. **봇 데몬 영구 가용성 (`main_rest_async.py`)**:
   - 15:30 장 마감 시 프로세스가 종료되지 않고, 당일 정산 완료 후 **다음 거래일 08:55까지 비동기 수면(`asyncio.sleep`) 상태로 안전하게 대기**합니다.
   - 주말(토/일)에는 다음 주 월요일 08:55까지 대기하며, 아침 08:55가 되면 자동으로 계좌 동기화 및 09:00 정규장 매매를 재개합니다.
2. **슈퍼바이저 자가 치유 관리 (`start.py`)**:
   - 대시보드(8501)와 API 서버(8000)는 24시간 내내 상시 서빙됩니다.
   - 만약 특정 프로세스가 예기치 않게 비정상 종료되더라도 컨테이너 전체를 죽이지 않고, 해당 프로세스만 3~5초 후 **자동으로 재시작(Self-Healing Restart)**합니다.

---

## 3. 🚀 ACR 빌드 및 AKS 배포 명령어

클라우드 쉘(Azure Cloud Shell) 또는 배포 서버 터미널에서 아래 명령어를 순서대로 실행합니다.

### 1단계: 업로드 디렉터리 이동
```bash
cd ~/clouddrive/deploy/kiwoom_rest_project
```

### 2단계: Azure Container Registry (ACR) 클라우드 이미지 빌드
`Dockerfile`의 Multi-stage 빌드를 통해 `kiwoom-bot:latest` 이미지를 생성합니다.
```bash
az acr build --registry portalregistries --image kiwoom-bot:latest .
```

### 3단계: AKS Deployment 롤아웃 재시작 (무중단 배포)
생성된 최신 이미지를 pull하여 단일 Pod를 새 버전으로 교체합니다.
```bash
kubectl rollout restart deployment/kiwoom-bot -n mzc-apps
```

### 4단계: 배포 상태 및 런타임 로그 실시간 확인
```bash
# 배포 완료 대기
kubectl rollout status deployment/kiwoom-bot -n mzc-apps

# 실시간 봇 및 대시보드 로그 확인
kubectl logs -f deployment/kiwoom-bot -n mzc-apps
```

---

## 4. 🌐 서비스 접속 및 24시간 운영 확인

배포 완료 후 장 마감 이후나 야간/주말에도 대시보드가 상시 정상 접속됩니다:

- **대시보드 접속 URL**: `https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom`
- **확인 사항**:
  - 장 마감 후에도 과거 매매 내역, 실현 손익 분석, 일별 자산 추이 정상 조회 가능.
  - 최상단 `🚨 긴급 전량 청산 (Kill-Switch)` 버튼 및 `🔔 알림 센터` 실시간 피드 상시 활성화.

---

## 5. 🏗️ (대안 아키텍처) K8s 파드 완전 분리 구성안 (Pod Separation)

현재 적용된 **해결안 A(통합 슈퍼바이저 + 24시간 데몬 휴면)** 외에, 인프라 관점에서 대시보드와 트레이딩 봇을 물리적으로 완전히 다른 파드로 격리하고 싶으실 경우의 매니페스트 구성안입니다:

### (1) 대시보드 전용 Pod (`kiwoom-dashboard-deployment.yaml`)
- 실행 명령: `streamlit run dashboard.py --server.port 8501 --server.baseUrlPath /kiwoom`
- 24시간 365일 상시 가동 (웹 트래픽 전담).

### (2) 트레이딩 봇 전용 Pod (`kiwoom-bot-deployment.yaml` 또는 CronJob)
- 실행 명령: `python main_rest_async.py --real`
- **스케줄러 선택 가이드**:
  - **파이썬 내부 데몬 방식 (현재 적용됨 - 추천)**: 키움 웹소켓 및 인메모리 버퍼 세션을 유지하며 08:55 자동 활성화 (파드 재시작 오버헤드 0초).
  - **K8s CronJob 방식**: 매일 평일 08:55에 파드가 새로 생성되고 15:30에 정상 종료(Completed). 단, 매일 컨테이너 기동 시 이미지 풀 및 세션 초기화 시간이 소요됨.
