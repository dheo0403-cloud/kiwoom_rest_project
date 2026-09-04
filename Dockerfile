FROM python:3.11-slim

WORKDIR /app

# Set timezone to Asia/Seoul
ENV TZ=Asia/Seoul
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# Run both trading bot daemon and Streamlit dashboard
CMD ["python", "start.py"]
