# run_trail.py 감시: 18건이 모두 끝날 때까지 실행, 중단·실패하면 5분 뒤 남은 건부터 다시 (최대 12회)
import os, subprocess, sys, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
LOG = os.path.join(HERE, "supervise.log")
TOTAL = 18  # 6종목 × 3설정 (run_trail.py)

def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")

def done_count():
    if not os.path.isdir(OUT):
        return 0
    return sum("[OOS 합산]" in open(os.path.join(OUT, n), encoding="utf-8").read()
               for n in os.listdir(OUT) if n.endswith(".txt"))

for attempt in range(1, 13):
    log(f"시도 {attempt} 시작 (완료 {done_count()}/{TOTAL})")
    rc = subprocess.call([sys.executable, "-X", "utf8", os.path.join(HERE, "run_trail.py")],
                         stdout=open(os.path.join(HERE, "run.log"), "a", encoding="utf-8"), stderr=subprocess.STDOUT)
    n = done_count()
    log(f"시도 {attempt} 종료 rc={rc} (완료 {n}/{TOTAL})")
    if n >= TOTAL:
        log("ALL DONE")
        break
    time.sleep(300)
else:
    log("12회 시도 후에도 미완료 — 중단")
