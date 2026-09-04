import subprocess
import time
import sys
import argparse
import os

# 한국 표준시(KST) 강제 적용
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

def run_trading_bot(is_real):
    """매매 봇 실행 (Background)"""
    print(f"🚀 매매 봇을 실행합니다... (모드: {'실전' if is_real else '모의'})", flush=True)
    
    python_exe = sys.executable
    # -u 옵션으로 실시간 파이썬 로그 출력 보장 (비동기 퀀트 봇 실행)
    cmd = [python_exe, "-u", "main_rest_async.py"]
    if is_real:
        cmd.append("--real")
        
    return subprocess.Popen(cmd)

def run_dashboard():
    """스트림릿 대시보드 실행 (Background)"""
    print("📈 대시보드 서버를 시작합니다... (Port: 8501)", flush=True)
    return subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "dashboard.py",
        "--server.port", "8501",
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--server.baseUrlPath", "/kiwoom"
    ])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true', help='실전투자 모드로 실행')
    args = parser.parse_args()

    # 환경변수 TRADING_MODE=real 또는 IS_REAL=true 지원
    env_mode = os.getenv("TRADING_MODE", "").lower()
    env_is_real = os.getenv("IS_REAL", "false").lower() in ("true", "1", "yes")
    is_real = args.real or env_mode == "real" or env_is_real

    # 1. 대시보드 실행
    dashboard_proc = run_dashboard()
    time.sleep(5) # 서버 부팅 대기

    # 2. 매매 봇 실행
    bot_proc = run_trading_bot(is_real)

    print("\n" + "="*50)
    print("시스템이 정상적으로 시작되었습니다.")
    print(f"대시보드 접속 주소: http://[서버IP]:8501")
    print("="*50 + "\n")

    try:
        # 두 프로세스가 종료될 때까지 대기
        while True:
            if bot_proc.poll() is not None:
                print("⚠️ 매매 봇이 종료되었습니다.")
                break
            if dashboard_proc.poll() is not None:
                print("⚠️ 대시보드가 종료되었습니다.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n정지 요청 수신. 모든 프로세스를 종료합니다...")
    finally:
        # 프로그램이 어떤 이유로든 종료될 때, 살아있는 자식 프로세스들을 확실하게 청소(Kill)
        if bot_proc.poll() is None:
            bot_proc.terminate()
        if dashboard_proc.poll() is None:
            dashboard_proc.terminate()
        print("모든 백그라운드 프로세스가 완전히 정리되었습니다.")
