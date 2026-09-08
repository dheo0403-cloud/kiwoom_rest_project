# ==========================================
# Stage 1: Frontend Build (React 18 + Vite Bento Grid)
# ==========================================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline --no-audit || npm install
COPY frontend/ .
RUN npm run build

# ==========================================
# Stage 2: Python Build & Dependencies
# ==========================================
FROM python:3.11-slim AS python-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ==========================================
# Stage 3: Final Lightweight Runtime
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

# 타임존 설정 (Asia/Seoul KST) 및 경량 유틸리티 설치
ENV TZ=Asia/Seoul
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH=/root/.local/bin:$PATH
ENV IS_REAL=true
ENV KIWOOM_MODE=REAL
ENV TRADING_MODE=real

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Builder 스테이지에서 컴파일된 Python 패키지만 복사 (이미지 경량화)
COPY --from=python-builder /root/.local /root/.local

# 애플리케이션 소스코드 복사
COPY . .

# Frontend 빌드 결과물 복사 (차세대 벤토 그리드 콕핏)
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# 불필요한 레거시/테스트 파일 및 git 캐시 제거
RUN rm -rf legacy test_*.py .git __pycache__ frontend/node_modules

EXPOSE 8501 8000

# 관제 시스템 헬스체크 프로브 (FastAPI /api/health - Port 8501)
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -f http://localhost:8501/api/health || exit 1

# 비동기 트레이딩 데몬 + 관제 대시보드 통합 실행기
CMD ["python", "start.py"]
