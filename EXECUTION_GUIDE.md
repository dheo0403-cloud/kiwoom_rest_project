# 🚀 Kiwoom REST API 비동기 자동매매 시스템 실행 가이드

본 가이드는 서버 환경(Linux/Windows)에서 24시간 365일 구동되는 데몬(Daemon) 형태로 자동매매 시스템을 배포하고 실행/종료하는 방법을 안내합니다.

## 1. 사전 준비 (배포 환경 세팅)

### 1) 파이썬 라이브러리 설치
서버에 프로젝트 파일을 업로드한 후, 터미널에서 다음 명령어로 필수 패키지를 설치합니다.
```bash
pip install -r requirements.txt
```

### 2) 환경 변수 파일 세팅
프로젝트 최상단 경로에 `.env` 파일을 생성하고 아래의 정보(데이터베이스 정보 및 키움 API Key)를 기입합니다.
```env
# 데이터베이스 설정 (MariaDB)
DB_HOST=localhost
DB_USER=azure
DB_PASSWORD=your_password
DB_NAME=kiwoom_db

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
```

---

## 2. 시스템 실행 방법 (24시간 데몬)

이 시스템은 코드가 **매일 아침 08:55분에 자동으로 시스템을 초기화하고 동기화**하도록 설계되어 있으므로, 장 시작 전에 **딱 한 번만 백그라운드로 실행**해 두시면 됩니다.

### [Linux / Ubuntu 서버의 경우]
서버 터미널을 종료해도 봇이 계속 돌아가도록 `nohup` 또는 `pm2`를 사용합니다.

**방법 A. `nohup` 사용 (추천, 간단함)**
```bash
# 모의투자 실행
nohup python3 main_rest.py > trading.log 2>&1 &

# 실전투자 실행
nohup python3 main_rest.py --real > trading.log 2>&1 &
```
- 실행 후 `tail -f trading.log` 명령어로 봇이 잘 돌아가는지 로그를 실시간으로 확인할 수 있습니다.

**방법 B. `PM2` 사용 (안정성 및 모니터링 강점)**
Node.js 패키지 관리자인 pm2가 설치되어 있다면 권장합니다.
가상환경(`venv`)의 파이썬을 사용하도록 `--interpreter` 옵션을 필수로 넣어주세요.
```bash
# 1. 봇 실행 (모의투자)
pm2 start main_rest.py --name "kiwoom_bot_mock" --interpreter ./venv/bin/python

# 1. 봇 실행 (실전투자)
pm2 start main_rest.py --name "kiwoom_bot_real" --interpreter ./venv/bin/python -- --real

# 2. 대시보드(화면) 실행 (파이썬 인터프리터를 통한 안전한 모듈 호출)
pm2 start ./venv/bin/python --name "kiwoom_dashboard" -- -m streamlit run dashboard.py --server.port 8501 --server.headless true
```

### [Windows 서버의 경우]
명령 프롬프트(CMD) 또는 PowerShell에서 다음과 같이 실행합니다. 
```cmd
:: 모의투자 실행
python main_rest.py

:: 실전투자 실행
python main_rest.py --real
```

---

## 3. 시스템 종료 방법 (안전한 리소스 반환)

DB 커넥션 풀을 정상적으로 닫고 시스템을 안전하게 종료하는 방법입니다.

### [Linux / Ubuntu 서버의 경우]

**`nohup`으로 실행한 경우:**
1. 현재 돌아가고 있는 봇의 프로세스 ID(PID)를 찾습니다.
```bash
ps -ef | grep main_rest.py
```
2. 해당 PID(예: 12345)를 입력하여 프로세스를 강제 종료합니다.
```bash
kill -9 12345
```

**`PM2`로 실행한 경우:**
```bash
pm2 stop kiwoom_bot_real
pm2 delete kiwoom_bot_real
```

### [Windows 서버의 경우]
실행해 둔 터미널(CMD) 창에서 **`Ctrl + C`** 키를 누릅니다.
그러면 화면에 `사용자에 의한 종료` 메세지와 함께 리소스(세션 및 DB 풀)를 정상적으로 반환하고 안전하게 스크립트가 종료됩니다.

---

## 4. 유의 사항
- 시스템은 15:30에 장 마감 정산(당일 손익)을 마친 후 다음날 08:55분까지 "대기(Sleep)" 상태로 유지됩니다.
- 모의투자와 실전투자를 동시에 실행하지 마십시오. 하나의 DB(`kiwoom_db`)를 공유하므로 데이터가 꼬일 수 있습니다. 하나의 서버에서는 한 번에 하나의 모드만 구동해야 합니다.
