"""
하이킨아시 · 롭 호프만 IRB 5분봉 탐색 (research/ha_irb_plan.md 2026-10-10 사전 고정) — 계산만 (DB 접속 없음)

실행: PYTHONIOENCODING=utf-8 python research/ha_irb_explore.py --cache <pullback_explore.py 캐시 폴더>

설계 문서에 없던 세부(결과 보기 전 고정):
- 5분봉 키 = 날짜 × (HHMM에서 분을 5분 단위로 내림). 봉 시각 = 그 키의 시작 시각.
- EMA20: α = 2/21, 종목 첫 5분봉 종가로 시작. "3봉/5봉 전"은 날짜 경계 무시한 5분봉 index 기준.
- 신호 봉 다음 5분봉이 같은 날이 아니면 진입 없음. 무작위 기준선 후보 봉 = 같은 날 시각 09:30~14:30(포함) 5분봉.
- HX1 청산: 진입 봉부터 HA 음봉이 나온 봉의 다음 5분봉 시가. 다음 봉 시각이 15:15를 넘거나 없으면 타임컷(15:15 규칙).
- 시간 순서상 HX1 청산 판정은 5분봉 종가 시점, 손절·목표는 1분봉. 타임컷은 1분봉 15:15 종가(없으면 15:15 이전 마지막 1분봉).
- 탐색/확인 경계 = 20260522 (되밀림 1차 탐색과 같은 경계, 같은 거래일 집합).
"""
import argparse
import os
import random
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pullback_explore import build_universe, net_ret, tick, stats, fmt  # noqa: E402

CUT = 20260522
SEEDS = 5
EXITS = {"H": ("HX1", "HX2"), "I": ("IX1", "IX2")}


def five_min(d, t, ohlc):
    """1분봉 → 5분봉 (시작 index, 날짜, 시각, OHLC)"""
    key = d.astype(np.int64) * 10000 + (t - (t % 100) % 5)
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    o = ohlc[starts, 0]
    h = np.maximum.reduceat(ohlc[:, 1], starts)
    l = np.minimum.reduceat(ohlc[:, 2], starts)
    ends = np.r_[starts[1:], len(d)] - 1
    c = ohlc[ends, 3]
    return starts, d[starts], (t - (t % 100) % 5)[starts], o, h, l, c


def ema(x, n=20):
    out, a = np.empty_like(x), 2 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def heikin(o, h, l, c):
    hc = (o + h + l + c) / 4
    ho = np.empty_like(o)
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, np.maximum.reduce([h, ho, hc]), np.minimum.reduce([l, ho, hc]), hc


def time_exit(t1, ohlc1, a, b):
    """[a, b) 1분봉 구간에서 15:15 종가(없으면 15:15 이전 마지막 봉 종가)"""
    idx = np.flatnonzero(t1[a:b] <= 1515)
    return ohlc1[a + idx[-1], 3] if len(idx) else None


def exit_fixed(t1, ohlc1, k, day_end, stop, target, fill_low=None):
    """1분봉 k부터 손절·목표·타임컷. fill_low: 체결 봉 저가(그 봉에서 손절 판정)"""
    if fill_low is not None and fill_low <= stop:
        return stop, "stop"
    for j in range(k, day_end):
        if t1[j] > 1515:
            break
        o, h, l, c = ohlc1[j]
        if o <= stop:
            return o, "stop"
        if l <= stop:
            return stop, "stop"
        if o >= target:
            return o, "target"
        if h >= target:
            return target, "target"
        if t1[j] == 1515:
            return c, "time"
    x = time_exit(t1, ohlc1, k, day_end)
    return (x, "time") if x is not None else None


def exit_ha(bars, i_entry, day_last_bar, t1, ohlc1, day_end):
    """HX1: 진입 봉부터 HA 음봉이 나오면 다음 5분봉 시가, 아니면 타임컷"""
    starts, _, bt, o, _, _, _, hao, hac = bars
    for i in range(i_entry, day_last_bar + 1):
        if hac[i] < hao[i]:
            if i + 1 <= day_last_bar and bt[i + 1] <= 1515:
                return o[i + 1], "ha"
            break
    x = time_exit(t1, ohlc1, starts[i_entry], day_end)
    return (x, "time") if x is not None else None


def sim_code(code, mdir, uni_days):
    z = np.load(os.path.join(mdir, f"{code}.npz"))
    d1, t1, ohlc1 = z["d"], z["t"].astype(np.int32), z["ohlc"]
    if len(d1) < 200:
        return []
    starts, bd, bt, o, h, l, c = five_min(d1, t1, ohlc1)
    e20 = ema(c)
    hao, hah, hal, hac = heikin(o, h, l, c)
    bars = (starts, bd, bt, o, h, l, c, hao, hac)
    out = []  # (전략, 청산, 날짜, 코드, 수익률, 사유, 그룹)
    day_first = {}
    for i, dd in enumerate(bd):
        day_first.setdefault(int(dd), i)
    days = sorted(day_first)
    for n_day, day in enumerate(days):
        if day not in uni_days:
            continue
        f = day_first[day]
        last = (day_first[days[n_day + 1]] - 1) if n_day + 1 < len(days) else len(bd) - 1
        day_end = starts[last + 1] if last + 1 < len(starts) else len(d1)
        pool = [i for i in range(max(f, 30), last + 1) if 930 <= bt[i] <= 1430]
        if not pool:
            continue

        def randoms(tag, x, stop_pct, r_mult):
            for s in range(SEEDS):
                i = random.Random(f"{code}-{day}-{tag}-{s}").choice(pool)
                e = o[i]
                if x == "HX1":
                    r = exit_ha(bars, i, last, t1, ohlc1, day_end)
                else:
                    r = exit_fixed(t1, ohlc1, starts[i], day_end, e * (1 - stop_pct), e * (1 + r_mult * stop_pct))
                if r:
                    out.append((tag, x, day, code, net_ret(e, r[0]), r[1], f"B1s{s}"))

        # ---- H. 하이킨아시 ----
        for i in range(max(f, 30), last):
            if not (930 <= bt[i] < 1430):
                continue
            if c[i] > e20[i] and e20[i] > e20[i - 3] and hac[i - 1] < hao[i - 1] and hac[i] > hao[i] and hal[i] >= hao[i]:
                e = o[i + 1]
                for x in EXITS["H"]:
                    if x == "HX1":
                        r = exit_ha(bars, i + 1, last, t1, ohlc1, day_end)
                        if r:
                            out.append(("H", x, day, code, net_ret(e, r[0]), r[1], "sig"))
                            randoms("H", x, None, None)
                    else:
                        stop = l[i]
                        if e <= stop:
                            continue
                        r = exit_fixed(t1, ohlc1, starts[i + 1], day_end, stop, e + 2 * (e - stop))
                        if r:
                            out.append(("H", x, day, code, net_ret(e, r[0]), r[1], "sig"))
                            randoms("H", x, (e - stop) / e, 2)
                break

        # ---- I. IRB ----
        for i in range(max(f, 30), last):
            if not (930 <= bt[i] < 1430):
                continue
            rng = h[i] - l[i]
            if rng > 0 and l[i] > e20[i] and e20[i] > e20[i - 5] and max(o[i], c[i]) <= h[i] - 0.45 * rng:
                level = h[i] + tick(h[i])
                stop = l[i] - tick(l[i])
                a = starts[i + 1]
                b = starts[min(i + 7, last + 1)] if i + 7 <= last + 1 and i + 7 < len(starts) else day_end
                hit = np.flatnonzero(ohlc1[a:b, 1] >= level)
                if len(hit):
                    k = a + hit[0]
                    e = max(level, ohlc1[k, 0])
                    for x, rm in (("IX1", 1), ("IX2", 2)):
                        r = exit_fixed(t1, ohlc1, k + 1, day_end, stop, e + rm * (e - stop), fill_low=ohlc1[k, 2])
                        if r:
                            out.append(("I", x, day, code, net_ret(e, r[0]), r[1], "sig"))
                            randoms("I", x, (e - stop) / e, rm)
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    daily = np.load(os.path.join(a.cache, "daily.npz"), allow_pickle=True)["rows"].item()
    _, uni = build_universe(daily)
    code_days = defaultdict(set)
    for dd, s in uni.items():
        for cc in s:
            code_days[cc].add(dd)
    mdir = os.path.join(a.cache, "min")
    codes = sorted(cc for cc in code_days if os.path.exists(os.path.join(mdir, f"{cc}.npz")))
    rows = []
    for n, cc in enumerate(codes, 1):
        rows += sim_code(cc, mdir, code_days[cc])
        if n % 300 == 0:
            print(f"  {n}/{len(codes)} (기록 {len(rows):,})", flush=True)

    explore, confirm = (lambda d: d < CUT), (lambda d: d >= CUT)
    sel = lambda s, x, keep, g="sig": [r for (ss, xx, d, c, r, _, gg) in rows if ss == s and xx == x and keep(d) and gg == g]

    def b1(s, x, keep):
        ss = [stats(sel(s, x, keep, f"B1s{k}")) for k in range(SEEDS)]
        return dict(n=int(np.mean([q["n"] for q in ss])), ev=np.mean([q["ev"] for q in ss]),
                    pf=np.mean([q["pf"] for q in ss]), win=np.mean([q["win"] for q in ss]))

    days = sorted({r[2] for r in rows})
    print(f"\n종목 {len(codes):,}, 거래일 {days[0]}~{days[-1]} ({len(days)}일), 탐색 < {CUT} ≤ 확인")
    for label, keep in (("탐색(앞 60%)", explore), ("확인(뒤 40%)", confirm)):
        print(f"\n[{label}]")
        for s, xs in EXITS.items():
            for x in xs:
                print(f"  {x}  신호 {fmt(stats(sel(s, x, keep)))}")
                print(f"  {x}  B1   {fmt(b1(s, x, keep))}")

    for s, xs in EXITS.items():
        x = max(xs, key=lambda xx: stats(sel(s, xx, explore))["ev"])
        st, rb = stats(sel(s, x, confirm)), b1(s, x, confirm)
        monthly = defaultdict(list)
        for (ss, xx, d, c, r, _, gg) in rows:
            if ss == s and xx == x and gg == "sig" and confirm(d):
                monthly[d // 100].append(r)
        pos = sum(1 for v in monthly.values() if np.mean(v) > 0)
        checks = [("거래당 기대값 ≥ +0.10%", st["ev"] >= 0.10, f"{st['ev']:+.3f}%"),
                  ("PF ≥ 1.1", st["pf"] >= 1.1, f"{st['pf']:.2f}"),
                  ("B1 대비 +0.2%p 이상", st["ev"] - rb["ev"] >= 0.2, f"{st['ev'] - rb['ev']:+.3f}%p (B1 {rb['ev']:+.3f}%)"),
                  ("양수 달 ≥ 2/3", pos >= len(monthly) * 2 / 3, f"{pos}/{len(monthly)}"),
                  ("거래 수 ≥ 300", st["n"] >= 300, f"{st['n']:,}")]
        print(f"\n[판정 {'하이킨아시' if s == 'H' else 'IRB'}] 탐색 최고 = {x} → 확인 구간")
        for name, ok, val in checks:
            print(f"  {'✅' if ok else '❌'} {name}: {val}")
        print(f"  결과: {'합격' if all(q[1] for q in checks) else '불합격'}")
        print("  월별: " + ", ".join(f"{m} {np.mean(v) * 100:+.2f}%({len(v)})" for m, v in sorted(monthly.items())))
        why = defaultdict(list)
        for (ss, xx, d, c, r, w, gg) in rows:
            if ss == s and xx == x and gg == "sig":
                why[w].append(r)
        print("  청산 사유(전체): " + ", ".join(f"{k} {len(v):,}건 {np.mean(v) * 100:+.2f}%" for k, v in sorted(why.items())))


if __name__ == "__main__":
    sys.exit(main())
