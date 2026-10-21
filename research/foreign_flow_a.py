"""
외국인 순매수 결합 탐색 A (research/foreign_flow_plan.md, 2026-10-07 사전 고정) — DB 조회 전용

실행:
  PYTHONIOENCODING=utf-8 python research/foreign_flow_a.py --cache <pullback_explore.py 캐시 폴더>
  (캐시의 trades.npy·daily.npz = 되밀림 1차 탐색과 같은 규칙·같은 기간의 B0·B1 거래 기록. 분봉을 다시 받지 않는다)

설계 문서에 없던 세부는 결과를 보기 전(2026-10-10)에 여기 고정한다:
- 대상·거래: 되밀림 탐색의 거래 기록 그대로(전일 거래대금 상위 300 = 분봉 일봉 기준). B0(돌파 봉 종가), B1(무작위 시각, 시드 0~4) × X1·X2.
- F5: foreign_flow_b.py와 같은 계산 — daily_ohlcv 달력에서 D-1까지 5거래일 외국인 순매수 합(백만원) ÷ 같은 5일 거래대금 합
  (daily_ohlcv.value 원 → 백만원). 5일 중 하나라도 행이 없거나 분모 0이면 그 종목은 그날 제외.
- 강함: 그날 되밀림 유니버스(300) 중 F5를 계산할 수 있는 종목에서 F5 상위 20% (개수 = floor(그 수 × 0.2)). 약함 = 하위 20%(참고).
- 탐색/확인: 거래 기록 전체의 거래일을 시간순 앞 60% / 뒤 40% (되밀림 탐색과 같은 경계).
- X 선택(탐색): B0·강함의 거래당 기대값이 큰 X 하나(되밀림 탐색과 같은 기준). 확인 구간은 그 X만 평가.
- 합격(확인, 모두): B0·강함 거래당 ≥ +0.10%, PF ≥ 1.1, B1·강함(5시드 평균) 대비 ≥ +0.2%p, 월별 기대값 양수 달 ≥ 2/3, 거래 ≥ 300.
"""
import argparse
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from foreign_flow_b import load  # noqa: E402  (같은 DB 로더·보통주 필터)
from pullback_explore import build_universe, stats, fmt  # noqa: E402

PCT, SEEDS = 0.2, range(5)


def f5_table(px, inv):
    """date(str) 행 × code 열: 그 날짜까지 5거래일 F5 (D의 신호는 D-1 행을 본다)"""
    dates = sorted(px["date"].unique())
    val = px.pivot(index="date", columns="code", values="value").reindex(dates) / 1e6
    fr = inv[inv["code"].isin(val.columns)].pivot(index="date", columns="code", values="frgn").reindex(dates)
    f5 = fr.rolling(5, min_periods=5).sum() / val.rolling(5, min_periods=5).sum().replace(0, np.nan)
    return dates, f5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    trades = list(np.load(os.path.join(a.cache, "trades.npy"), allow_pickle=True))
    daily = np.load(os.path.join(a.cache, "daily.npz"), allow_pickle=True)["rows"].item()
    _, uni = build_universe(daily)

    px, inv, _ = load()
    dates, f5 = f5_table(px, inv)
    prev = {d: dates[i - 1] for i, d in enumerate(dates) if i > 0}

    # 날짜별 강함·약함 집합
    group = {}  # (day:int, code) -> "strong" | "weak"
    for day, codes in uni.items():
        p = prev.get(str(day))
        if p is None:
            continue
        f = f5.loc[p].reindex(list(codes)).dropna().sort_values(ascending=False)
        n = int(len(f) * PCT)
        if n == 0:
            continue
        for c in f.index[:n]:
            group[(day, c)] = "strong"
        for c in f.index[-n:]:
            group[(day, c)] = "weak"

    trade_days = sorted({int(t[2]) for t in trades})
    cut = trade_days[int(len(trade_days) * 0.6)]
    explore, confirm = (lambda d: d < cut), (lambda d: d >= cut)

    def sel(name, x, keep, g=None):
        return [r for (n, e, d, c, r, _) in trades
                if n == name and e == x and keep(d) and (g is None or group.get((int(d), c)) == g)]

    def b1(x, keep, g=None):
        ss = [stats(sel(f"B1s{s}", x, keep, g)) for s in SEEDS]
        return dict(n=int(np.mean([s["n"] for s in ss])), ev=np.mean([s["ev"] for s in ss]),
                    pf=np.mean([s["pf"] for s in ss]), win=np.mean([s["win"] for s in ss]))

    print(f"거래 기록 {trade_days[0]}~{trade_days[-1]} ({len(trade_days)}거래일), 탐색 < {cut} ≤ 확인")
    print(f"외국인 데이터 {inv['date'].min()}~{inv['date'].max()}, 강함·약함 지정 {len(group):,}건")
    for label, keep in (("탐색(앞 60%)", explore), ("확인(뒤 40%)", confirm)):
        print(f"\n[{label}]")
        for x in ("X1", "X2"):
            print(f"  B0-{x} 전체   {fmt(stats(sel('B0', x, keep)))}")
            print(f"  B0-{x} 강함   {fmt(stats(sel('B0', x, keep, 'strong')))}")
            print(f"  B0-{x} 약함   {fmt(stats(sel('B0', x, keep, 'weak')))}")
            print(f"  B1-{x} 전체   {fmt(b1(x, keep))}")
            print(f"  B1-{x} 강함   {fmt(b1(x, keep, 'strong'))}")

    x = max(("X1", "X2"), key=lambda xx: stats(sel("B0", xx, explore, "strong"))["ev"])
    s = stats(sel("B0", x, confirm, "strong"))
    r = b1(x, confirm, "strong")
    monthly = defaultdict(list)
    for (n, e, d, c, ret, _) in trades:
        if n == "B0" and e == x and confirm(d) and group.get((int(d), c)) == "strong":
            monthly[int(d) // 100].append(ret)
    pos = sum(1 for v in monthly.values() if np.mean(v) > 0)
    checks = [
        ("거래당 기대값 ≥ +0.10%", s["ev"] >= 0.10, f"{s['ev']:+.3f}%"),
        ("PF ≥ 1.1", s["pf"] >= 1.1, f"{s['pf']:.2f}"),
        ("B1·강함 대비 +0.2%p 이상", s["ev"] - r["ev"] >= 0.2, f"{s['ev'] - r['ev']:+.3f}%p (B1·강함 {r['ev']:+.3f}%)"),
        ("양수 달 ≥ 2/3", pos >= len(monthly) * 2 / 3, f"{pos}/{len(monthly)}"),
        ("거래 수 ≥ 300", s["n"] >= 300, f"{s['n']:,}"),
    ]
    print(f"\n[판정] 탐색 구간 B0·강함 최고 = {x} → 확인 구간 평가")
    for name, ok, val in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {val}")
    print(f"  결과: {'합격' if all(c[1] for c in checks) else '불합격'}")
    print(f"\n[확인 구간 B0-{x}·강함 월별]")
    for m in sorted(monthly):
        v = np.array(monthly[m])
        print(f"  {m}  n={len(v):>5}  EV={v.mean() * 100:+.3f}%")


if __name__ == "__main__":
    sys.exit(main())
