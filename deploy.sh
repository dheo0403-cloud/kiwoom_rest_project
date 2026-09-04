#!/bin/bash

echo "🚀 키움증권 REST API 자동매매 시스템 배포를 시작합니다..."

# 1. 파이썬 가상환경 생성
if [ ! -d "venv" ]; then
    echo "📦 가상환경(venv) 생성 중..."
    python3 -m venv venv
fi

# 2. 가상환경 활성화 및 라이브러리 설치
echo "📥 필수 라이브러리 설치 중..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. .env 파일 체크
if [ ! -f ".env" ]; then
    echo "⚠️  .env 파일이 없습니다. .env.example을 복사하여 생성합니다."
    cp .env.example .env
    echo "📢 중요: .env 파일을 열어 실제 API 키와 계좌번호를 입력해주세요!"
fi

echo "✅ 배포 준비 완료!"
echo "--------------------------------------------------"
echo "실행 방법:"
echo "1. source venv/bin/activate"
echo "2. python3 start.py (모의투자)"
echo "3. python3 start.py --real (실전투자)"
echo "--------------------------------------------------"
