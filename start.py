import subprocess
import time
import sys
import argparse
import os
import signal

# 한국 표준시(KST) 강제 적용
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

_is_shutting_down = False


def sig_handler(signum, frame):
    global _is_shutting_down
    _is_shutting_down = True
    print(f"\n🛑 [Supervisor] 종료 시그널({signum}) 수신. 시스템 안전 종료 시퀀스를 시작합니다...", flush=True)


# 시그널 핸들러 등록 (Linux/Windows 호환)
signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)


def run_api_server():
    """FastAPI 킬스위치 및 WebSocket 제어 서버 + 차세대 콕핏 (Port: 8501, Ingress 메인 타겟)"""
    print("🌐 [API Server] FastAPI 백엔드 및 차세대 벤토 그리드 콕핏을 시작합니다... (Port: 8501)", flush=True)
    return subprocess.Popen([
        sys.executable, "-m", "uvicorn", "api_server:app",
        "--host", "0.0.0.0",
        "--port", "8501",
        "--log-level", "warning"
    ])


def run_dashboard():
    """[레거시] 스트림릿 대시보드 실행 (Port: 8000, BasePath: /kiwoom)"""
    print("📈 [Dashboard] [레거시] 스트림릿 대시보드를 시작합니다... (Port: 8000, BasePath: /kiwoom)", flush=True)
    return subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "dashboard.py",
        "--server.port", "8000",
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--server.baseUrlPath", "/kiwoom"
    ])


def run_trading_bot(is_real):
    """비동기 퀀트 트레이딩 봇 데몬 실행 (Background)"""
    print(f"🚀 [TradingBot] 비동기 퀀트 봇을 실행합니다... (모드: {'실전' if is_real else '모의'})", flush=True)
    python_exe = sys.executable
    cmd = [python_exe, "-u", "main_rest_async.py"]
    if is_real:
        cmd.append("--real")
    return subprocess.Popen(cmd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="키움 퀀트 시스템 통합 슈퍼바이저 (24/365 가용성 보장)")
    parser.add_argument('--real', action='store_true', help='실전투자 모드로 실행')
    args = parser.parse_args()

    # 환경변수 TRADING_MODE=real 또는 IS_REAL=true 지원 (Kubernetes 배포 호환)
    env_mode = os.getenv("TRADING_MODE", "").lower()
    env_is_real = os.getenv("IS_REAL", "false").lower() in ("true", "1", "yes")
    is_real = args.real or env_mode == "real" or env_is_real

    # 1. API 서버 (8000) 실행
    api_proc = run_api_server()
    time.sleep(2)

    # 2. 대시보드 (8501) 실행
    dashboard_proc = run_dashboard()
    time.sleep(3)

    # 3. 비동기 매매 봇 실행
    bot_proc = run_trading_bot(is_real)

    print("\n" + "="*60)
    print("🎉 키움 퀀트 24/365 무중단 자동매매 시스템이 가동되었습니다.")
    print("• 🚀 [메인] 차세대 벤토 그리드 콕핏: http://[서버IP]:8501 (또는 /kiwoom)")
    print("• 📈 [레거시] 스트림릿 대시보드: http://[서버IP]:8000/kiwoom")
    print(f"• 운영 모드: {'실전투자 (REAL)' if is_real else '모의투자 (MOCK)'}")
    print("="*60 + "\n", flush=True)

    try:
        # 슈퍼바이저 무한 감시 루프 (개별 프로세스 장애 시 자동 자가치유 재시작)
        while not _is_shutting_down:
            # 1. 대시보드 프로세스 감시
            if dashboard_proc.poll() is not None and not _is_shutting_down:
                print("⚠️ [Supervisor] 대시보드 프로세스 종료 감지 -> 3초 후 자동 재시작합니다...", flush=True)
                time.sleep(3)
                dashboard_proc = run_dashboard()

            # 2. API 서버 프로세스 감시
            if api_proc.poll() is not None and not _is_shutting_down:
                print("⚠️ [Supervisor] API 서버 프로세스 종료 감지 -> 3초 후 자동 재시작합니다...", flush=True)
                time.sleep(3)
                api_proc = run_api_server()

            # 3. 매매 봇 프로세스 감시
            if bot_proc.poll() is not None and not _is_shutting_down:
                exit_code = bot_proc.returncode
                print(f"⚠️ [Supervisor] 트레이딩 봇 프로세스 종료 감지 (ExitCode: {exit_code}) -> 5초 후 자동 재시작합니다...", flush=True)
                time.sleep(5)
                bot_proc = run_trading_bot(is_real)

            time.sleep(2)

    except KeyboardInterrupt:
        print("\n🛑 사용자 중단 요청 수신.")
    finally:
        print("🛑 모든 자식 프로세스를 안전하게 정리합니다...", flush=True)
        for p, name in [(bot_proc, "TradingBot"), (dashboard_proc, "Dashboard"), (api_proc, "API Server")]:
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except Exception:
                    p.kill()
        print("✅ 모든 자식 프로세스가 안전하게 정리되었습니다.")
