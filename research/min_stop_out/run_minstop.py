# 하드스탑 최소 폭 백테스트 일괄 실행 (동시 2개, 결과는 out/ 아래 파일)
import os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

PROJ = r"C:\Users\MZC01-MICHAEL\Desktop\허동진\claude_project\kiwoom_rest_project"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
CODES = ["086520", "042510", "396300", "011200", "006800", "010140"]
jobs = [(c, 5, p) for c in CODES for p in ("0", "0.005", "0.01")]
jobs += [(c, 0, "0") for c in ("011200", "006800", "010140")]

def run(job):
    code, conf, pct = job
    path = os.path.join(OUT, f"{code}_c{conf}_p{pct}.txt")
    if os.path.exists(path) and "[OOS 합산]" in open(path, encoding="utf-8").read():
        return path, 0
    env = dict(os.environ, PYTHONUTF8="1")
    t = time.time()
    with open(path, "w", encoding="utf-8") as f:
        rc = subprocess.call([sys.executable, "-X", "utf8", "backtest.py", code, "--confirm-minutes", str(conf),
                              "--set", f"min_hard_stop_pct={pct}"], cwd=PROJ, stdout=f, stderr=subprocess.STDOUT, env=env)
    print(f"{code} c{conf} p{pct} rc={rc} {time.time() - t:.0f}s", flush=True)
    return path, rc

with ThreadPoolExecutor(1) as ex:
    list(ex.map(run, jobs))
print("ALL DONE", flush=True)
