# ==========================================
# Stage 1: Build & Dependencies (빌드 스테이지)
# ==========================================
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ==========================================
# Stage 2: Final Lightweight Runtime (실행 스테이지)
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

# 타임존 설정 (Asia/Seoul KST) 및 경량 유틸리티 설치
ENV TZ=Asia/Seoul
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH=/root/.local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Builder 스테이지에서 컴파일된 Python 패키지만 복사 (이미지 경량화)
COPY --from=builder /root/.local /root/.local

# 애플리케이션 소스코드 복사
COPY . .

# 불필요한 레거시/테스트 파일 및 git 캐시 제거
RUN rm -rf legacy test_*.py .git __pycache__

EXPOSE 8501 8000

# 스트림릿 대시보드 헬스체크 프로브
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -f http://localhost:8501/kiwoom/_stcore/health || exit 1

# 비동기 트레이딩 데몬 + 관제 대시보드 통합 실행기
CMD ["python", "start.py"]
