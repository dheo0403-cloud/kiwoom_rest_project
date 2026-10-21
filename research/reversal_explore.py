"""
단기 과매도 반등 탐색 (research/reversal_plan.md, 2026-10-13 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/reversal_explore.py

설계 문서에 없던 세부(결과 보기 전 고정):
- 대상·제외·계좌 시뮬레이션은 swing_hold_explore.py와 같다.
- 기준선 U는 청산 방식(H1·H3)별로 한 번씩 계산해 같은 청산의 후보와 비교한다.
- 청산일 k가 다음 거래의 D가 될 수 있다(진입은 k+1).
"""
import os
import sys

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from daily_backtest import equity_stats, simulate_portfolio  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
BUY, SELL = 0.00095, 0.00275
CANDS = {"R1": ("S1", 1), "R2": ("S1", 3), "R3": ("S2", 1), "R4": ("S2", 3)}  # 신호, 보유 일수


def simulate(S, o, c, J, hold):
    """D 신호 → E 시가 진입, hold일째 종가 청산 → [(진입 인덱스, 청산 인덱스, 수익률, 진입가)]"""
    n, t, out = len(o), 0, []
    while t < n - 1:
        e = t + 1
        if not (S[t] and o[e] > 0):
            t += 1
            continue
        k, cnt = e, 1
        while cnt < hold:
            k += 1
            if k >= n:
                break
            if o[k] > 0:
                cnt += 1
        if k >= n:
            break
        if not J[max(t - 3, 0):k + 1].any():
            out.append((e, k, c[k] * (1 - SELL) / (o[e] * (1 + BUY)) - 1, o[e]))
        t = k
    return out


def st(r):
    r = np.asarray(r, dtype=float)
    g, ls = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ev=r.mean() * 100 if len(r) else np.nan, pf=g / ls if ls > 0 else np.inf,
                win=(r > 0).mean() * 100 if len(r) else np.nan)


def main():
    cn = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                         password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    allpx = pd.read_sql("SELECT code, date, open, high, low, close, value FROM daily_ohlcv WHERE code REGEXP '^[0-9]{6}$'", cn)
    common = set(pd.read_sql("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                                AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""", cn)["code"])
    cn.close()
    kodex = allpx[allpx["code"] == "069500"].sort_values("date")
    px = allpx[allpx["code"].isin(common)]
    dates = sorted(px["date"].unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, lo, cl, val = (piv(k) for k in ("open", "high", "low", "close", "value"))

    dlt = cl.diff()
    up, dn = dlt.clip(lower=0).rolling(2).mean(), (-dlt.clip(upper=0)).rolling(2).mean()
    rsi2 = (100 - 100 / (1 + up / dn)).where(dn > 0, 100.0)
    base = ((cl.notna().cumsum() >= 21) & (hi > lo)
            & (val.where(cl >= 1000).rank(axis=1, ascending=False, method="first") <= 300))
    sigs = {"S1": (base & (cl / cl.shift(3) - 1 <= -0.10)).to_numpy(), "S2": (base & (rsi2 < 5)).to_numpy(),
            "U": base.to_numpy()}
    J = (cl.pct_change(fill_method=None).abs() > 0.30).to_numpy()
    O, C, V = op.to_numpy(), cl.to_numpy(), val.to_numpy()
    codes = list(op.columns)

    rows = []  # (진입일, 청산일, 그룹, 수익률, 진입가, 전일 거래대금, 종목)
    groups = {**CANDS, "U1": ("U", 1), "U3": ("U", 3)}
    for j, code in enumerate(codes):
        for g, (s, hold) in groups.items():
            for e, k, r, p in simulate(sigs[s][:, j], O[:, j], C[:, j], J[:, j], hold):
                rows.append((dates[e], dates[k], g, r, p, V[e - 1, j], code))
    df = pd.DataFrame(rows, columns=["date", "exit_date", "g", "ret", "entry_px", "value_prev", "code"])
    tdays = sorted(df[df["g"].isin(CANDS)]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "탐색", "확인")

    raw = px[["code", "date", "close"]]
    k_rng = kodex[kodex["date"] >= tdays[0]]
    kstat = equity_stats(k_rng["close"].reset_index(drop=True), k_rng["date"].reset_index(drop=True))
    print(f"[과매도 반등] 진입일 {tdays[0]}~{tdays[-1]} ({len(tdays)}일), 탐색 < {cut} ≤ 확인, 종목 {len(codes):,}")
    print(f"  KODEX200 보유 같은 기간: 누적 {kstat['누적%']}% MDD {kstat['MDD%']}% 2024 {kstat.get('2024%')}%")

    res = {}
    for cand, (s, hold) in CANDS.items():
        sig, u = df[df["g"] == cand], df[df["g"] == f"U{hold}"]
        ex = st(sig[sig["part"] == "탐색"]["ret"])
        cf, cu = st(sig[sig["part"] == "확인"]["ret"]), st(u[u["part"] == "확인"]["ret"])
        y24, u24 = st(sig[sig["date"].str[:4] == "2024"]["ret"]), st(u[u["date"].str[:4] == "2024"]["ret"])
        mon = sig[sig["part"] == "확인"].groupby(sig["date"].str[:6])["ret"].mean()
        curve = simulate_portfolio(sig[["code", "date", "exit_date", "ret", "entry_px", "value_prev"]], raw, 100_000, 5)
        ps = equity_stats(curve["equity"], curve["date"])
        res[cand] = dict(ex=ex, cf=cf, cu=cu, y24=y24, u24=u24, mon=mon, ps=ps)
        print(f"\n  [{cand}] 신호 {s} / 보유 {hold}일")
        print(f"    탐색 n={ex['n']:,} EV={ex['ev']:+.3f}% PF={ex['pf']:.2f} / U {st(u[u['part'] == '탐색']['ret'])['ev']:+.3f}%")
        print(f"    확인 n={cf['n']:,} EV={cf['ev']:+.3f}% PF={cf['pf']:.2f} 승률={cf['win']:.1f}% / U {cu['ev']:+.3f}% "
              f"/ 양수 달 {(mon > 0).sum()}/{len(mon)}")
        print(f"    2024 n={y24['n']:,} EV={y24['ev']:+.3f}% / U {u24['ev']:+.3f}%")
        print(f"    계좌: 누적 {ps['누적%']}% MDD {ps['MDD%']}% "
              + " ".join(f"{y} {ps.get(f'{y}%')}%" for y in ("2024", "2025", "2026")))

    cand = max(CANDS, key=lambda x: res[x]["ex"]["ev"])
    r = res[cand]
    cf, cu, y24, u24, mon, ps = r["cf"], r["cu"], r["y24"], r["u24"], r["mon"], r["ps"]
    checks = [("① 거래당 ≥ +0.30%", cf["ev"] >= 0.30, f"{cf['ev']:+.3f}%"),
              ("① U 대비 ≥ +0.30%p", cf["ev"] - cu["ev"] >= 0.30, f"{cf['ev'] - cu['ev']:+.3f}%p"),
              ("① PF ≥ 1.1", cf["pf"] >= 1.1, f"{cf['pf']:.2f}"),
              ("① 양수 달 ≥ 60%", (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("① 거래 ≥ 300", cf["n"] >= 300, f"{cf['n']:,}"),
              ("② 2024 거래당 ≥ 0%", y24["ev"] >= 0, f"{y24['ev']:+.3f}%"),
              ("② 2024 U 대비 ≥ +0.30%p", y24["ev"] - u24["ev"] >= 0.30, f"{y24['ev'] - u24['ev']:+.3f}%p"),
              ("③ 계좌 MDD < KODEX200 MDD", abs(ps["MDD%"]) < abs(kstat["MDD%"]), f"{ps['MDD%']}% vs {kstat['MDD%']}%"),
              ("③ 계좌 2024 ≥ KODEX200 2024", (ps.get("2024%") or -999) >= (kstat.get("2024%") or 999),
               f"{ps.get('2024%')}% vs {kstat.get('2024%')}%")]
    print(f"\n  [판정] 탐색 최고 = {cand}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))


if __name__ == "__main__":
    main()
