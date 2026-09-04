# 🚀 Kiwoom REST API 비동기 퀀트 자동 매매 시스템 실행 가이드

본 가이드는 서버 환경(Linux/Windows)에서 24시간 365일 구동되는 데몬(Daemon) 형태로 비동기 퀀트 자동 매매 시스템을 배포하고 실행/종료하는 방법을 안내합니다.

---

## 1. 사전 준비 (배포 환경 세팅)

### 1) 파이썬 라이브러리 설치
```bash
pip install -r requirements.txt
```

### 2) 환경 변수 파일 세팅 (`.env`)
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 아래의 정보를 입력합니다.
```env
# 데이터베이스 설정 (MariaDB)
DB_HOST=localhost
DB_PORT=3306
DB_USER=azure
DB_PASSWORD=your_password
DB_NAME=kiwoom_quant_db

# 모의투자(MOCK) 계정 정보
KIWOOM_MOCK_APP_KEY=mock_app_key_here
KIWOOM_MOCK_APP_SECRET=mock_app_secret_here
KIWOOM_MOCK_ACCOUNT=12345678
KIWOOM_MOCK_PASSWORD=0000

# 실전투자(REAL) 계정 정보
KIWOOM_REAL_APP_KEY=real_app_key_here
KIWOOM_REAL_APP_SECRET=real_app_secret_here
KIWOOM_REAL_ACCOUNT=87654321
KIWOOM_REAL_PASSWORD=0000

# 트레이딩 모드 (mock / real)
TRADING_MODE=mock
```

---

## 2. 시스템 실행 방법

### 방법 A. 통합 실행기 사용 (`start.py` - 봇 + 대시보드 동시 실행)
```bash
# 모의투자 모드 실행
python start.py

# 실전투자 모드 실행
python start.py --real
```

### 방법 B. 비동기 봇 데몬 독립 실행 (`main_rest_async.py`)
```bash
# Linux/Mac (nohup 백그라운드)
nohup python3 -u main_rest_async.py > trading.log 2>&1 &

# 실전투자 모드
nohup python3 -u main_rest_async.py --real > trading.log 2>&1 &

# PM2 사용 시
pm2 start main_rest_async.py --name "kiwoom_bot" --interpreter ./venv/bin/python -- -u
```

### 방법 C. 스트림릿 관제 대시보드 독립 실행 (`dashboard.py`)
```bash
streamlit run dashboard.py --server.port 8501 --server.headless true
```

---

## 3. 시스템 종료 및 안전 관리 (Graceful Shutdown)
- **터미널 실행 시**: `Ctrl + C` 입력 시 진행 중인 주문 확인 및 인메모리 버퍼 데이터를 DB에 안전하게 Flush한 후 종료됩니다.
- **PM2 / nohup 실행 시**: `pm2 stop kiwoom_bot` 또는 `kill -15 [PID]` (SIGTERM)으로 안전 종료합니다.
