# 청산 규칙(상승 중 보유·꺾일 때 매도) 백테스트 일괄 실행 — 계획: research/trail_exit_plan.md (동시 6개, 결과는 out/ 아래 파일)
import os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

PROJ = r"C:\Users\MZC01-MICHAEL\Desktop\허동진\claude_project\kiwoom_rest_project"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
CODES = ["086520", "042510", "396300", "011200", "006800", "010140"]
VARIANTS = {
    "base": [],
    "early_trail": ["trailing_activation_pct=0.008", "breakeven_trigger_pct=0.008", "atr_trailing_stop_mult=1.0"],
    "hold_trail": ["breakeven_trigger_pct=0.03"],
}
jobs = [(c, v) for c in CODES for v in VARIANTS]

def run(job):
    code, var = job
    path = os.path.join(OUT, f"{code}_{var}.txt")
    if os.path.exists(path) and "[OOS 합산]" in open(path, encoding="utf-8").read():
        return path, 0
    env = dict(os.environ, PYTHONUTF8="1")
    cmd = [sys.executable, "-X", "utf8", "backtest.py", code, "--confirm-minutes", "5"]
    for kv in VARIANTS[var]:
        cmd += ["--set", kv]
    t = time.time()
    with open(path, "w", encoding="utf-8") as f:
        rc = subprocess.call(cmd, cwd=PROJ, stdout=f, stderr=subprocess.STDOUT, env=env)
    print(f"{code} {var} rc={rc} {time.time() - t:.0f}s", flush=True)
    return path, rc

with ThreadPoolExecutor(int(os.getenv("TRAIL_WORKERS", "2"))) as ex:  # 10/26 동시 6개는 메모리 부족으로 중단됨
    list(ex.map(run, jobs))
print("ALL DONE", flush=True)
