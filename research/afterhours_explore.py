"""
넥스트레이드 애프터마켓 등락 → 다음 날 탐색 (research/afterhours_plan.md, 2026-10-13 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/afterhours_explore.py
(nxt_daily는 봇의 NXT 일봉 동기화(20시~08시)가 채운다)

설계 문서에 없던 세부(데이터 보기 전 고정):
- 종목별 한 번에 한 거래: 청산일(D+1)이 다음 신호일 D가 될 수 있다(청산 후 같은 날 애프터 진입).
- 계좌 시뮬레이션: 같은 날 진입·청산이 아니므로 date = D, exit_date = D+1, entry_px = NXT 종가.
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
BUY, SELL = 0.00195, 0.00275
CANDS = {"N1": (1, "O"), "N2": (1, "C"), "N3": (-1, "O"), "N4": (-1, "C")}  # 신호 방향(+3%/−3%), 청산


def st(r):
    r = np.asarray(r, dtype=float)
    g, ls = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ev=r.mean() * 100 if len(r) else np.nan, pf=g / ls if ls > 0 else np.inf)


def main():
    cn = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                         password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    nxt = pd.read_sql("SELECT code, date, close AS nclose, volume AS nvol FROM nxt_daily", cn)
    allpx = pd.read_sql("SELECT code, date, open, close, value FROM daily_ohlcv WHERE code REGEXP '^[0-9]{6}$' "
                        f"AND date >= '{nxt['date'].min()}'", cn)
    common = set(pd.read_sql("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                                AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""", cn)["code"])
    cn.close()
    kodex = allpx[allpx["code"] == "069500"].sort_values("date")
    px = allpx[allpx["code"].isin(common)].sort_values(["code", "date"]).copy()
    g = px.groupby("code")
    px["o1"], px["c1"], px["d1"] = g["open"].shift(-1), g["close"].shift(-1), g["date"].shift(-1)
    px["jump"] = (g["close"].pct_change(fill_method=None).abs() > 0.30) | (px["c1"] / px["close"] - 1).abs().gt(0.30)
    px["rank"] = px.where(px["close"] >= 1000)["value"].groupby(px["date"]).rank(ascending=False, method="first")
    d = px.merge(nxt, on=["code", "date"], how="inner")
    d = d[(d["rank"] <= 300) & (d["nvol"] > 0) & (d["nclose"] > 0) & d["d1"].notna() & ~d["jump"]].copy()
    d["A"] = d["nclose"] / d["close"] - 1
    d = d[d["A"].abs() <= 0.30]
    for x in ("O", "C"):
        d[f"r{x}"] = d["o1" if x == "O" else "c1"] * (1 - SELL) / (d["nclose"] * (1 + BUY)) - 1

    days = sorted(d["date"].unique())
    cut = days[int(len(days) * 0.6)]
    d["part"] = np.where(d["date"] < cut, "탐색", "확인")
    km = kodex.set_index("date")["close"]
    kmon = km.groupby(km.index.str[:6]).last().pct_change()
    kmon.iloc[0] = km.groupby(km.index.str[:6]).last().iloc[0] / km.iloc[0] - 1
    down = set(kmon[kmon < 0].index)
    k_rng = kodex[kodex["date"] >= days[0]]
    kstat = equity_stats(k_rng["close"].reset_index(drop=True), k_rng["date"].reset_index(drop=True))
    print(f"[애프터마켓] 신호일 {days[0]}~{days[-1]} ({len(days)}일), 탐색 < {cut} ≤ 확인, "
          f"대상 종목-일 {len(d):,}, 하루 평균 NXT 대상 {len(d) / len(days):.0f}종목, KODEX200 하락 달 {sorted(down)}")
    print(f"  A 분포: ≥+3% {(d['A'] >= 0.03).mean() * 100:.2f}%, ≤−3% {(d['A'] <= -0.03).mean() * 100:.2f}%, "
          f"중앙값 {d['A'].median() * 100:+.3f}%")
    print(f"  KODEX200 보유 같은 기간: 누적 {kstat['누적%']}% MDD {kstat['MDD%']}%")

    raw = px[["code", "date", "close"]]
    res = {}
    for cand, (sgn, x) in CANDS.items():
        col = f"r{x}"
        sig, u = d[(d["A"] * sgn >= 0.03)], d
        ex, exu = st(sig[sig["part"] == "탐색"][col]), st(u[u["part"] == "탐색"][col])
        cf, cu = st(sig[sig["part"] == "확인"][col]), st(u[u["part"] == "확인"][col])
        dn, dnu = st(sig[sig["date"].str[:6].isin(down)][col]), st(u[u["date"].str[:6].isin(down)][col])
        mon = sig[sig["part"] == "확인"].groupby(sig["date"].str[:6])[col].mean()
        trades = sig.rename(columns={"d1": "exit_date", col: "ret", "nclose": "entry_px", "value": "value_prev"})
        curve = simulate_portfolio(trades[["code", "date", "exit_date", "ret", "entry_px", "value_prev"]], raw, 100_000, 5)
        ps = equity_stats(curve["equity"], curve["date"])
        res[cand] = dict(ex=ex, cf=cf, cu=cu, dn=dn, dnu=dnu, mon=mon, ps=ps)
        print(f"\n  [{cand}] A {'≥ +3%' if sgn > 0 else '≤ −3%'} / 청산 D+1 {'시가' if x == 'O' else '종가'}")
        print(f"    탐색 n={ex['n']:,} EV={ex['ev']:+.3f}% PF={ex['pf']:.2f} / U {exu['ev']:+.3f}%")
        print(f"    확인 n={cf['n']:,} EV={cf['ev']:+.3f}% PF={cf['pf']:.2f} / U {cu['ev']:+.3f}% / 양수 달 {(mon > 0).sum()}/{len(mon)}")
        print(f"    하락 달 n={dn['n']:,} EV={dn['ev']:+.3f}% / U {dnu['ev']:+.3f}%")
        print(f"    계좌: 누적 {ps['누적%']}% MDD {ps['MDD%']}%")

    cand = max(CANDS, key=lambda c: res[c]["ex"]["ev"] if res[c]["ex"]["n"] else -999)
    r = res[cand]
    cf, cu, dn, dnu, mon, ps = r["cf"], r["cu"], r["dn"], r["dnu"], r["mon"], r["ps"]
    checks = [("① 거래당 ≥ +0.30%", cf["ev"] >= 0.30, f"{cf['ev']:+.3f}%"),
              ("① U 대비 ≥ +0.30%p", cf["ev"] - cu["ev"] >= 0.30, f"{cf['ev'] - cu['ev']:+.3f}%p"),
              ("① PF ≥ 1.1", cf["pf"] >= 1.1, f"{cf['pf']:.2f}"),
              ("① 양수 달 ≥ 60%", len(mon) > 0 and (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("① 거래 ≥ 200", cf["n"] >= 200, f"{cf['n']:,}"),
              ("② 하락 달 거래당 ≥ 0%", dn["ev"] >= 0, f"{dn['ev']:+.3f}%"),
              ("② 하락 달 U 대비 ≥ +0.30%p", dn["ev"] - dnu["ev"] >= 0.30, f"{dn['ev'] - dnu['ev']:+.3f}%p"),
              ("③ 계좌 누적 > 0", ps["누적%"] > 0, f"{ps['누적%']}%"),
              ("③ 계좌 MDD < KODEX200 MDD", abs(ps["MDD%"]) < abs(kstat["MDD%"]), f"{ps['MDD%']}% vs {kstat['MDD%']}%")]
    print(f"\n  [판정] 탐색 최고 = {cand}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))


if __name__ == "__main__":
    main()
