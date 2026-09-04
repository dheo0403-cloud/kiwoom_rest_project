# 🚢 Azure Kubernetes Service (AKS) 배포 가이드 (AKS_DEPLOYMENT.md)

본 문서는 고도화된 키움 비동기 퀀트 자동매매 시스템(`kiwoom_rest_project`)을 **Azure Container Registry(ACR)** 및 **Azure Kubernetes Service(AKS)** 환경에 무중단으로 빌드하고 배포하는 전체 절차와 업로드 파일 목록을 안내합니다.

---

## 1. 📂 업로드 대상 파일 목록 (Upload File Checklist)

서버(`~/clouddrive/deploy/kiwoom_rest_project`)에 업로드해야 하는 파일 목록입니다. 

> ⚠️ **보안 주의**: 로컬의 `.env` 파일은 컨테이너에 포함되지 않아야 합니다. DB 및 증권사 키는 AKS의 `Secret`(`kiwoom-credentials`, `db-credentials`)에서 환경 변수로 자동 주입됩니다.

### ✅ 필수 업로드 파일 목록 (총 17개 파일)

| 구분 | 파일명 | 역할 및 설명 |
| :--- | :--- | :--- |
| **빌드/설정** | `Dockerfile` | Multi-stage 경량화 빌드 및 KST 타임존 설정 |
| | `.dockerignore` | 불필요한 캐시 및 테스트 파일 빌드 제외 설정 |
| | `requirements.txt` | 런타임 의존 패키지 목록 (FastAPI, Streamlit, Pandas 등) |
| **통합 실행기** | `start.py` | 봇 데몬 + FastAPI(8000) + Streamlit(8501) 통합 구동기 |
| **코어 엔진** | `main_rest_async.py` | 비동기 퀀트 트레이딩 봇 메인 데몬 |
| | `async_kiwoom_client.py` | 키움 REST API 비동기 클라이언트 (토큰 버킷 & 서킷 브레이커) |
| | `async_portfolio.py` | 프랙셔널 켈리(Fractional Kelly) 자산 배분 & 포트폴리오 매니저 |
| **전략/지표** | `strategy.py` | ATR 적응형 변동성 돌파 진입 & 샹들리에 엑시트 출구 전략 |
| | `indicators.py` | ATR, 샹들리에, 볼린저 스퀴즈 모멘텀 기술적 지표 산출 엔진 |
| | `valuation.py` | 피오트로스키 F-Score & 펀더멘털 가치평가 엔진 |
| **데이터 레이어** | `market_data_buffer.py` | 인메모리 링버퍼(Ring-Buffer) & 비동기 5초 배치 DB 영속화 |
| | `database.py` | MariaDB 커넥션 풀 및 배치 인서트 매니저 |
| | `data_collector.py` | 당일 거래대금 상위 유니버스 수집기 |
| **알림 및 관제** | `notifier.py` | 카카오톡 '나에게 보내기' 실시간 알림 엔진 (토큰 자동 갱신) |
| | `api_server.py` | FastAPI 비상 킬스위치(`POST /api/bot/emergency-stop`) & WebSocket |
| | `dashboard.py` | 킬스위치 버튼 & 실시간 알림 센터(Notification Center) 대시보드 |
| **분석 도구** | `backtest.py` | 슬리피지/세금(0.21%) 반영 고충실도 백테스터 & WFO 최적화 |

### ❌ 업로드 제외 대상 (업로드하지 않아도 되는 파일)
- `legacy/` 디렉터리 (구버전 동기식 파일)
- `test_*.py` 파일 (로컬 단위 테스트 파일)
- `.git/`, `__pycache__/`, `.env`, `*.log`

---

## 2. 🚀 ACR 빌드 및 AKS 배포 명령어

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

## 3. 🌐 서비스 접속 및 운영 확인

배포 완료 후 기존 인그레스(Ingress) 도메인으로 정상 접속되는지 확인합니다:

- **대시보드 접속 URL**: `https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom`
- **주요 기능 점검**:
  1. 상단 `🚨 긴급 전량 청산 (Kill-Switch)` 버튼 노출 확인
  2. `🔔 알림 센터` 탭에서 실시간 체결 및 시스템 이벤트 피드 렌더링 확인
  3. `⚙️ 퀀트 파라미터` 탭에서 실시간 $k$ 돌파 계수 및 켈리 비중 슬라이더 튜닝 확인

---

## 4. 📱 (선택 사항) 카카오톡 실시간 알림 연동 방법

카카오톡 '나에게 보내기' 푸시 알림을 활성화하려면 AKS의 `kiwoom-bot` Deployment에 환경 변수를 추가하거나 Secret을 갱신해 주시면 됩니다.

```bash
# 카카오톡 REST API 키 및 토큰 주입 (선택 사항)
kubectl set env deployment/kiwoom-bot -n mzc-apps \
  KAKAO_REST_API_KEY="your_kakao_rest_api_key" \
  KAKAO_ACCESS_TOKEN="your_access_token" \
  KAKAO_REFRESH_TOKEN="your_refresh_token"
```
*(카카오 토큰을 등록하지 않아도 대시보드의 '알림 센터' 피드 및 로그로 안전하게 자동 수신됩니다.)*
