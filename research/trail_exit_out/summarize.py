# trail_exit 백테스트 결과 집계 — 계획서(research/trail_exit_plan.md) 합격 기준 대조
import ast, os, re

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CODES = ["086520", "042510", "396300", "011200", "006800", "010140"]
VARS = ["base", "early_trail", "hold_trail"]


def parse(code, var):
    txt = open(os.path.join(OUT, f"{code}_{var}.txt"), encoding="utf-8").read()
    oos = ast.literal_eval(re.search(r"\[OOS 합산\] (\{.*\})", txt).group(1))
    folds = [float(m.group(1)) for m in re.finditer(r"^\S+~\S+\s+\S+~\S+\s+\S+\s+\S+\s+(-?[\d.]+)\s+\d+\s+[\d.]+$", txt, re.M)]
    # 청산 사유 표: 건수 × 평균수익% → 거래당 평균 (비용 후)
    rows = re.findall(r"^(\S+)\s+(\d+)\s+(-?[\d.]+)\s+(-?[\d,]+)$", txt.split("청산 사유", 1)[1], re.M)
    n = sum(int(r[1]) for r in rows)
    avg = sum(int(r[1]) * float(r[2]) for r in rows) / n if n else 0.0
    return oos, folds, avg


res = {(c, v): parse(c, v) for c in CODES for v in VARS}
print("종목 | " + " | ".join(f"{v} 수익%/거래/PF/거래당%" for v in VARS))
for c in CODES:
    print(c, " | ".join(f"{res[c, v][0]['total_return_pct']:.2f}/{res[c, v][0]['total_trades']}/{res[c, v][0]['profit_factor']:.3f}/{res[c, v][2]:.3f}" for v in VARS))
for v in VARS[1:]:
    better = sum(res[c, v][0]['total_return_pct'] > res[c, 'base'][0]['total_return_pct'] for c in CODES)
    avg_b = sum(res[c, 'base'][2] for c in CODES) / len(CODES)
    avg_v = sum(res[c, v][2] for c in CODES) / len(CODES)
    worst = min(res[c, v][0]['total_return_pct'] - res[c, 'base'][0]['total_return_pct'] for c in CODES)
    pf_better = sum(res[c, v][0]['profit_factor'] > res[c, 'base'][0]['profit_factor'] for c in CODES)
    fb = fw = 0
    for c in CODES:
        for a, b in zip(res[c, v][1], res[c, 'base'][1]):
            fb += a > b
            fw += a < b
    print(f"\n[{v}] ① 수익 개선 종목 {better}/6 (기준 ≥4) "
          f"② 거래당 평균 {avg_v:.3f}% vs base {avg_b:.3f}% (차 {avg_v - avg_b:+.3f}%p, 기준 ≥+0.05) "
          f"③ 최악 종목 차 {worst:+.2f}%p (기준 > −5) ④ PF 개선 종목 {pf_better}/6 "
          f"| 폴드 개선 {fb} / 악화 {fw}")
