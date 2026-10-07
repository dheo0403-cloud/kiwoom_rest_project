"""되밀림 매수 1차 탐색 (research/pullback_plan.md 사전 고정 설계 그대로, DB 조회 전용)

실행:
  PYTHONIOENCODING=utf-8 python research/pullback_explore.py --cache <폴더> [--workers 6]
1) 전 종목 분봉에서 일별 거래대금·OHLC 집계(SQL) → 매일 전일 거래대금 상위 300종목
2) 한 번이라도 상위 300에 든 종목의 정규장 분봉을 내려받아 캐시(npz)
3) B0·B1·P1~P3 × X1·X2 거래 생성 → 앞 60% 탐색 / 뒤 40% 확인 판정

설계 문서에 없는 세부 해석(데이터를 보기 전 2026-10-07에 정함):
- ATR14 = 전일까지 14일 True Range 단순 평균 (분봉으로 만든 일봉, 15일 미만이면 제외)
- 돌파: 09:15 ≤ 봉 시각 < 14:30 이고 종가 ≥ 돌파선인 첫 봉. 지정가 진입도 14:30 전 봉까지
- 지정가는 호가단위로 내림. VWAP·되돌림 기준가는 직전 봉까지의 값(미래 정보 차단), VWAP = Σ(종가×거래량)/Σ거래량
- 지정가 체결 봉의 저가가 손절가 이하이면 그 봉에서 손절(보수적). 종가 매수(B0·B1)는 다음 봉부터 청산 판정
- X2: 진입일을 1일째로 5거래일째 15:15 종가. 5거래일이 데이터 끝을 넘는 거래는 제외
"""
import argparse
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pymysql
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

TOP_N, MIN_PRICE = 300, 1000
SLIP, BUY_FEE, SELL_FEE, TAX = 0.0008, 0.00015, 0.00015, 0.0018
STRATS = ["P1", "P2", "P3"]
EXITS = ["X1", "X2"]
B1_SEEDS = 5


def connect():
    return pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                           password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=900)


def tick(p):
    """KRX 호가단위 (2023년 이후 코스피·코스닥 공통)"""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            return t
    return 1000


def floor_tick(p):
    t = tick(p)
    return float(int(p // t) * t)


def net_ret(entry, exit_):
    return exit_ * (1 - SLIP) * (1 - SELL_FEE - TAX) / (entry * (1 + SLIP) * (1 + BUY_FEE)) - 1


# ---------- 1) 데이터 ----------
def universe_codes():
    with connect() as c, c.cursor() as cur:
        cur.execute("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                       AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%' ORDER BY code""")
        return [r[0] for r in cur.fetchall()]


AGG_SQL = """SELECT LEFT(datetime,8) d, SUM(close*volume), MAX(high), MIN(low),
  SUBSTRING_INDEX(GROUP_CONCAT(open ORDER BY datetime),',',1), SUBSTRING_INDEX(GROUP_CONCAT(close ORDER BY datetime DESC),',',1)
  FROM minute_ohlcv WHERE code=%s AND LENGTH(datetime)=14 AND volume>0 AND RIGHT(datetime,6) BETWEEN '090000' AND '153000'
  GROUP BY d"""


def fetch_daily(codes, cache, workers):
    path = os.path.join(cache, "daily.npz")
    if os.path.exists(path):
        z = np.load(path, allow_pickle=True)
        return z["rows"].item()

    def one(code):
        with connect() as c, c.cursor() as cur:
            cur.execute(AGG_SQL, (code,))
            return code, [(int(d), float(v), float(h), float(l), float(o), float(cl)) for d, v, h, l, o, cl in cur.fetchall()]
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for i, (code, rows) in enumerate(ex.map(one, codes), 1):
            out[code] = rows
            if i % 200 == 0:
                print(f"  일봉 집계 {i}/{len(codes)}", flush=True)
    np.savez(path, rows=np.array(out, dtype=object))
    return out


def fetch_minutes(codes, cache, workers):
    mdir = os.path.join(cache, "min")
    os.makedirs(mdir, exist_ok=True)
    todo = [c for c in codes if not os.path.exists(os.path.join(mdir, f"{c}.npz"))]

    def one(code):
        with connect() as c, c.cursor() as cur:
            cur.execute("""SELECT datetime, open, high, low, close, volume FROM minute_ohlcv
                           WHERE code=%s AND LENGTH(datetime)=14 AND volume>0
                             AND RIGHT(datetime,6) BETWEEN '090000' AND '153000' ORDER BY datetime""", (code,))
            r = cur.fetchall()
        dt = np.array([x[0] for x in r])
        np.savez_compressed(os.path.join(mdir, f"{code}.npz"),
                            d=np.array([int(s[:8]) for s in dt], dtype=np.int32),
                            t=np.array([int(s[8:12]) for s in dt], dtype=np.int16),
                            ohlc=np.array([x[1:5] for x in r], dtype=np.float64).reshape(-1, 4),
                            v=np.array([x[5] for x in r], dtype=np.float64))
        return code
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, todo), 1):
            if i % 100 == 0:
                print(f"  분봉 다운로드 {i}/{len(todo)}", flush=True)
    return mdir


# ---------- 2) 유니버스·ATR ----------
def build_universe(daily):
    cal = sorted({r[0] for rows in daily.values() for r in rows})
    prev = {d: cal[i - 1] for i, d in enumerate(cal) if i > 0}
    by_day = defaultdict(dict)  # day -> code -> (value, close)
    for code, rows in daily.items():
        for d, v, h, l, o, c in rows:
            by_day[d][code] = (v, c)
    uni = {}
    for d in cal[1:]:
        p = prev[d]
        cands = [(v, code) for code, (v, c) in by_day[p].items() if c >= MIN_PRICE and code in by_day[d]]
        uni[d] = {code for _, code in sorted(cands, reverse=True)[:TOP_N]}
    return cal, uni


def atr_map(rows):
    """code 일봉 rows → {day: 전일까지 ATR14} (15일 이상 쌓인 날만)"""
    rows = sorted(rows)
    trs, out = [], {}
    for i, (d, v, h, l, o, c) in enumerate(rows):
        if len(trs) >= 14:
            out[d] = float(np.mean(trs[-14:]))
        pc = rows[i - 1][5] if i else None
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
    return out


# ---------- 3) 시뮬레이션 ----------
def mins(t):
    return (t // 100) * 60 + t % 100


def exit_trade(days_bars, k0, entry, stop, mode, check_entry_bar_low=None):
    """days_bars: [(t, ohlc)] 진입일부터 최대 5일. k0: 진입일 첫 판정 봉 index. → (청산가, 사유) 또는 None"""
    if check_entry_bar_low is not None and check_entry_bar_low <= stop:
        return stop, "stop"
    last_day = 0 if mode == "X1" else 4
    if mode == "X2" and len(days_bars) < 5:
        return None
    for di in range(last_day + 1):
        t, ohlc = days_bars[di]
        start = k0 if di == 0 else 0
        for k in range(start, len(t)):
            if di == last_day and t[k] > 1515:
                break
            o, h, l, c = ohlc[k]
            if o <= stop:
                return o, ("gap" if (di > 0 and k == 0) else "stop")
            if l <= stop:
                return stop, "stop"
            if di == last_day and t[k] == 1515:
                return c, "time"
        if di == last_day:  # 15:15 봉이 없으면 15:15 이전 마지막 봉 종가
            idx = np.where(t <= 1515)[0]
            return (ohlc[idx[-1]][3], "time") if len(idx) else None
    return None


def sim_code(code, mdir, uni_days, atrs):
    z = np.load(os.path.join(mdir, f"{code}.npz"))
    d_all, t_all, ohlc_all, v_all = z["d"], z["t"], z["ohlc"], z["v"]
    days = np.unique(d_all)
    bounds = {d: (np.searchsorted(d_all, d, "left"), np.searchsorted(d_all, d, "right")) for d in days}
    day_list = list(days)
    trades = []  # (strategy, exit, day, code, ret, reason)
    for di, day in enumerate(day_list):
        if day not in uni_days or day not in atrs:
            continue
        atr = atrs[day]
        if atr <= 0:
            continue
        a, b = bounds[day]
        t, ohlc, vol = t_all[a:b], ohlc_all[a:b], v_all[a:b]
        if len(t) < 30:
            continue
        fut = [(t_all[slice(*bounds[x])], ohlc_all[slice(*bounds[x])]) for x in day_list[di:di + 5]]
        day_open = ohlc[0][0]
        line = day_open + 0.5 * atr
        ok = (t >= 915) & (t < 1430) & (ohlc[:, 3] >= line)
        hits = np.where(ok)[0]
        if not len(hits):
            continue
        ib = int(hits[0])

        def add(name, entry, k_next, fill_low=None):
            stop = entry - 1.5 * atr
            for x in EXITS:
                r = exit_trade(fut, k_next, entry, stop, x, fill_low)
                if r:
                    trades.append((name, x, int(day), code, net_ret(entry, r[0]), r[1]))

        add("B0", ohlc[ib][3], ib + 1)
        # B1 무작위: 09:15~14:30 봉 중 무작위 시각 종가 (시드 고정 5회)
        pool = np.where((t >= 915) & (t <= 1430))[0]
        for s in range(B1_SEEDS):
            k = int(random.Random(f"{code}-{day}-{s}").choice(list(pool)))
            add(f"B1s{s}", ohlc[k][3], k + 1)
        # 되밀림 지정가 (돌파 봉 다음 봉부터 60분 이내, 14:30 전)
        cum_pv = np.cumsum(ohlc[:, 3] * vol)
        cum_v = np.cumsum(vol)
        tb = mins(int(t[ib]))
        done = set()
        for j in range(ib + 1, len(t)):
            if mins(int(t[j])) - tb > 60 or t[j] >= 1430 or len(done) == 3:
                break
            low = ohlc[j][2]
            levels = {"P1": line}
            vwap = cum_pv[j - 1] / cum_v[j - 1] if cum_v[j - 1] > 0 else None
            if vwap is not None and vwap >= day_open:
                levels["P2"] = vwap
            hh = ohlc[ib:j, 1].max()
            levels["P3"] = day_open + 0.5 * (hh - day_open)
            for name, lv in levels.items():
                if name in done:
                    continue
                lim = floor_tick(lv)
                if lim > 0 and low <= lim - tick(lim):
                    done.add(name)
                    add(name, lim, j + 1, fill_low=low)
    return trades


# ---------- 4) 판정 ----------
def stats(rets):
    r = np.array(rets)
    if not len(r):
        return dict(n=0, ev=float("nan"), pf=float("nan"), win=float("nan"))
    g, l = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ev=r.mean() * 100, pf=(g / l if l > 0 else float("inf")), win=(r > 0).mean() * 100)


def fmt(s):
    return f"n={s['n']:>6,}  EV={s['ev']:+.3f}%  PF={s['pf']:.2f}  승률={s['win']:.1f}%"


def b1_stats(trades, x, keep):
    """B1은 시드별 통계를 평균"""
    ss = [stats([r for (n, e, d, c, r, _) in trades if n == f"B1s{s}" and e == x and keep(d)]) for s in range(B1_SEEDS)]
    return dict(n=int(np.mean([s["n"] for s in ss])), ev=np.mean([s["ev"] for s in ss]),
                pf=np.mean([s["pf"] for s in ss]), win=np.mean([s["win"] for s in ss]))


def report(trades, cal_days):
    trade_days = sorted({t[2] for t in trades})
    cut = trade_days[int(len(trade_days) * 0.6)]
    explore, confirm = (lambda d: d < cut), (lambda d: d >= cut)
    print(f"\n기간 {trade_days[0]}~{trade_days[-1]} ({len(trade_days)}거래일), 탐색 < {cut} ≤ 확인, 종목 {len({t[3] for t in trades}):,}")
    sel = lambda n, x, keep: [r for (nn, e, d, c, r, _) in trades if nn == n and e == x and keep(d)]

    for label, keep in (("탐색(앞 60%)", explore), ("확인(뒤 40%)", confirm)):
        print(f"\n[{label}]")
        for x in EXITS:
            print(f"  B0-{x}  {fmt(stats(sel('B0', x, keep)))}")
            print(f"  B1-{x}  {fmt(b1_stats(trades, x, keep))}  (시드 5회 평균)")
            for p in STRATS:
                print(f"  {p}-{x}  {fmt(stats(sel(p, x, keep)))}")

    best = max(((p, x) for p in STRATS for x in EXITS), key=lambda px: stats(sel(*px, explore))["ev"])
    p, x = best
    s = stats(sel(p, x, confirm))
    b1 = b1_stats(trades, x, confirm)
    monthly = defaultdict(list)
    for (n, e, d, c, r, _) in trades:
        if n == p and e == x and confirm(d):
            monthly[d // 100].append(r)
    pos_months = sum(1 for v in monthly.values() if np.mean(v) > 0)
    checks = [
        ("거래당 기대값 ≥ +0.10%", s["ev"] >= 0.10, f"{s['ev']:+.3f}%"),
        ("PF ≥ 1.1", s["pf"] >= 1.1, f"{s['pf']:.2f}"),
        ("B1 대비 +0.2%p 이상", s["ev"] - b1["ev"] >= 0.2, f"{s['ev'] - b1['ev']:+.3f}%p (B1 {b1['ev']:+.3f}%)"),
        ("양수 달 ≥ 2/3", pos_months >= len(monthly) * 2 / 3, f"{pos_months}/{len(monthly)}"),
        ("거래 수 ≥ 300", s["n"] >= 300, f"{s['n']:,}"),
    ]
    print(f"\n[판정] 탐색 구간 최고 = {p}-{x} → 확인 구간 평가")
    for name, ok, val in checks:
        print(f"  {'통과' if ok else '미달'}  {name}: {val}")
    print(f"  결과: {'합격' if all(c[1] for c in checks) else '불합격'}")

    print(f"\n[{p}-{x} 월별 (전체 기간)]")
    allm = defaultdict(list)
    for (n, e, d, c, r, _) in trades:
        if n == p and e == x:
            allm[d // 100].append(r)
    for m in sorted(allm):
        v = np.array(allm[m])
        print(f"  {m}  n={len(v):>5}  EV={v.mean() * 100:+.3f}%  {'확인' if m >= cut // 100 else '탐색'}")

    print("\n[청산 사유 (전체 기간)]")
    for n in ["B0"] + STRATS:
        for x in EXITS:
            rs = defaultdict(list)
            for (nn, e, d, c, r, why) in trades:
                if nn == n and e == x:
                    rs[why].append(r)
            print(f"  {n}-{x}: " + ", ".join(f"{k} {len(v):,}건 평균 {np.mean(v) * 100:+.2f}%" for k, v in sorted(rs.items())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    codes = universe_codes()
    print(f"대상 보통주 {len(codes):,}종목", flush=True)
    daily = fetch_daily(codes, a.cache, a.workers)
    cal, uni = build_universe(daily)
    need = sorted({c for s in uni.values() for c in s})
    print(f"일봉 집계 완료: 달력 {len(cal)}일 ({cal[0]}~{cal[-1]}), 상위 {TOP_N}에 든 종목 {len(need):,}", flush=True)
    mdir = fetch_minutes(need, a.cache, a.workers)
    print("분봉 캐시 완료, 시뮬레이션 시작", flush=True)
    code_days = defaultdict(set)
    for d, s in uni.items():
        for c in s:
            code_days[c].add(d)
    trades = []
    for i, code in enumerate(need, 1):
        trades += sim_code(code, mdir, code_days[code], atr_map(daily[code]))
        if i % 200 == 0:
            print(f"  시뮬레이션 {i}/{len(need)} (거래 {len(trades):,})", flush=True)
    np.save(os.path.join(a.cache, "trades.npy"), np.array(trades, dtype=object))
    report(trades, cal)


if __name__ == "__main__":
    sys.exit(main())
