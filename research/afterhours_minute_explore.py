"""
N3 2차 재검증 — 애프터마켓 분봉으로 체결 가능 가격 확인 (research/afterhours_minute_plan.md, 2026-10-14 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/afterhours_minute_explore.py
(nxt_minute는 봇의 NXT 분봉 백필(_NX, 15:30~20:00 봉)이 채운다)

설계 문서에 없던 세부(결과 보기 전 고정, 2026-10-15):
- 데이터: `_NX` 분봉 사용(10/15 백필 599종목 오류 0 확인). 일반 코드 대체 경로는 쓰지 않음.
- 봉 시각 = nxt_minute.datetime의 HHMM. "체결 있음" = volume > 0 봉.
- 대상·제외는 afterhours_explore.py와 같은 코드(상위 300·종가 ≥ 1,000·±30%·D+1 존재), A' 절대값 ≤ 30%.
- KODEX200 하락 달·MDD는 2차 신호일 범위에서 계산.
- 2차 보완(10/15, 첫 실행 뒤 추가): 분봉-일봉 수정주가 불일치 종목 제외 (plan §2차 보완). 첫 실행 결과는 무효.
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
CUT1 = "20260219"  # 1차 확인 구간 시작 (참고용 분할)

def load_minute(cn) -> pd.DataFrame:
    """종목별 기본키 범위 조회(전체 GROUP BY는 30분 넘게 걸려 연결이 끊김) → 종목-일 집계
    pt = 15:40~19:50 마지막 체결 봉 종가, m1 = 19:51~20:00 VWAP 근사, m2 = 같은 구간 최고가, aval = 15:40~20:00 거래대금"""
    codes = pd.read_sql("SELECT DISTINCT code FROM nxt_daily", cn)["code"]
    out = []
    for i, c in enumerate(codes):
        r = pd.read_sql("SELECT datetime, close, high, volume FROM nxt_minute WHERE code = %s AND volume > 0", cn, params=[c])
        if r.empty:
            continue
        r["date"], t = r["datetime"].str[:8], r["datetime"].str[8:12]
        r["cv"] = r["close"] * r["volume"]
        pre, post = r[(t >= "1540") & (t <= "1950")], r[(t >= "1951") & (t <= "2000")]
        a = pre.sort_values("datetime").groupby("date")["close"].last().rename("pt").to_frame()
        p = post.groupby("date").agg(cv=("cv", "sum"), v=("volume", "sum"), m2=("high", "max"))
        a = a.join(p["m2"], how="outer").join((p["cv"] / p["v"]).rename("m1"), how="outer")
        a = a.join(r[t >= "1540"].groupby("date")["cv"].sum().rename("aval"), how="outer")
        a = a.join(r.sort_values("datetime").groupby("date")["close"].last().rename("lastc"), how="outer")
        out.append(a.reset_index().assign(code=c))
        if (i + 1) % 100 == 0:
            print(f"  분봉 로드 {i + 1}/{len(codes)}", flush=True)
    return pd.concat(out, ignore_index=True)


def st(r):
    r = np.asarray(r, dtype=float)
    g, ls = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ev=r.mean() * 100 if len(r) else np.nan, pf=g / ls if ls > 0 else np.inf)


def fmt(s):
    return f"n={s['n']:,} EV={s['ev']:+.3f}% PF={s['pf']:.2f}"


def main():
    cn = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                         password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=1800)
    mn = load_minute(cn)
    nxt = pd.read_sql("SELECT code, date, close AS nclose, volume AS nvol FROM nxt_daily", cn)
    allpx = pd.read_sql("SELECT code, date, open, close, value FROM daily_ohlcv WHERE code REGEXP '^[0-9]{6}$' "
                        f"AND date >= '{mn['date'].min()}'", cn)
    common = set(pd.read_sql("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                                AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""", cn)["code"])
    cn.close()

    for c in ("m1", "m2", "aval", "pt"):
        mn[c] = pd.to_numeric(mn[c], errors="coerce")
    kodex = allpx[allpx["code"] == "069500"].sort_values("date")
    px = allpx[allpx["code"].isin(common)].sort_values(["code", "date"]).copy()
    g = px.groupby("code")
    px["o1"], px["d1"] = g["open"].shift(-1), g["date"].shift(-1)
    px["c1"] = g["close"].shift(-1)
    px["jump"] = (g["close"].pct_change(fill_method=None).abs() > 0.30) | (px["c1"] / px["close"] - 1).abs().gt(0.30)
    px["rank"] = px.where(px["close"] >= 1000)["value"].groupby(px["date"]).rank(ascending=False, method="first")
    base = px[(px["rank"] <= 300) & px["d1"].notna() & ~px["jump"]]

    d = base.merge(mn, on=["code", "date"], how="inner").merge(nxt, on=["code", "date"], how="left")
    # 2차 보완(10/15): 분봉·일봉 수정주가 불일치 종목 제외 — 하루 마지막 분봉 종가 vs NXT 일봉 종가 중앙 괴리 > 1%
    mn["lastc"] = pd.to_numeric(mn["lastc"], errors="coerce")
    chk = mn.merge(nxt, on=["code", "date"])
    dev = (chk["lastc"] / chk["nclose"] - 1).abs().groupby(chk["code"]).median()
    bad = set(dev[dev > 0.01].index)
    print(f"[정합성] 분봉-일봉 종가 중앙 괴리 > 1% 제외 {len(bad)}/{len(dev)}종목: "
          + ", ".join(f"{c}({dev[c] * 100:.1f}%)" for c in sorted(bad)))
    d = d[~d["code"].isin(bad)]
    d = d[d["pt"] > 0].copy()  # T 시점 체결 없음 → 신호 없음
    d["A2"] = d["pt"] / d["close"] - 1
    d = d[d["A2"].abs() <= 0.30]
    d["filled"] = d["m1"] > 0
    for m in ("m1", "m2"):
        d[f"r_{m}"] = d["o1"] * (1 - SELL) / (d[m] * (1 + BUY)) - 1
    d["A1"] = d["nclose"] / d["close"] - 1
    d["r_n"] = d["o1"] * (1 - SELL) / (d["nclose"] * (1 + BUY)) - 1

    days = sorted(d["date"].unique())
    km = kodex.set_index("date")["close"]
    km = km[km.index >= days[0]]
    mlast = km.groupby(km.index.str[:6]).last()
    kmon = mlast.pct_change()
    kmon.iloc[0] = mlast.iloc[0] / km.iloc[0] - 1
    down = set(kmon[kmon < 0].index)
    k_rng = kodex[kodex["date"] >= days[0]]
    kstat = equity_stats(k_rng["close"].reset_index(drop=True), k_rng["date"].reset_index(drop=True))

    sig_all = d[d["A2"] <= -0.03]
    sig = sig_all[sig_all["filled"]]
    u = d[d["filled"]]
    print(f"[N3 2차] 데이터 `_NX` 분봉, 신호일 {days[0]}~{days[-1]} ({len(days)}일), T 체결 있는 종목-일 {len(d):,}, "
          f"19:51~20:00 체결 있음 {d['filled'].mean() * 100:.1f}%")
    print(f"  KODEX200 같은 기간: 누적 {kstat['누적%']}% MDD {kstat['MDD%']}%, 하락 달 {sorted(down)}")
    print(f"  신호(A' ≤ −3%) {len(sig_all):,}건 중 19:51~20:00 체결 없어 제외 {(~sig_all['filled']).sum():,}건 "
          f"({(~sig_all['filled']).mean() * 100:.1f}%)")

    m1, m2, us = st(sig["r_m1"]), st(sig["r_m2"]), st(u["r_m1"])
    dn = st(sig[sig["date"].str[:6].isin(down)]["r_m1"])
    mon = sig.groupby(sig["date"].str[:6])["r_m1"].mean()
    print(f"\n  M1(19:51~20:00 VWAP) {fmt(m1)} / U {fmt(us)}")
    print(f"  M2(19:51~20:00 최고가) {fmt(m2)}")
    print(f"  하락 달 M1 {fmt(dn)}")
    print("  월별 M1: " + ", ".join(f"{k} {v * 100:+.2f}%({(sig['date'].str[:6] == k).sum()})" for k, v in mon.items()))

    trades = sig.rename(columns={"d1": "exit_date", "r_m1": "ret", "m1": "entry_px", "value": "value_prev"})
    curve = simulate_portfolio(trades[["code", "date", "exit_date", "ret", "entry_px", "value_prev"]],
                               px[["code", "date", "close"]], 100_000, 5)
    ps = equity_stats(curve["equity"], curve["date"])
    print(f"  계좌(10만 원·5종목, M1 진입): 누적 {ps['누적%']}% MDD {ps['MDD%']}%")

    checks = [("① M1 거래당 ≥ +0.30%", m1["ev"] >= 0.30, f"{m1['ev']:+.3f}%"),
              ("① M1 U 대비 ≥ +0.30%p", m1["ev"] - us["ev"] >= 0.30, f"{m1['ev'] - us['ev']:+.3f}%p"),
              ("① M1 PF ≥ 1.1", m1["pf"] >= 1.1, f"{m1['pf']:.2f}"),
              ("① M1 양수 달 ≥ 60%", len(mon) > 0 and (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("① M1 거래 ≥ 150", m1["n"] >= 150, f"{m1['n']:,}"),
              ("② M2 거래당 > 0%", m2["ev"] > 0, f"{m2['ev']:+.3f}%"),
              ("③ 하락 달 M1 거래당 ≥ 0%", dn["n"] > 0 and dn["ev"] >= 0, f"{dn['ev']:+.3f}% (n={dn['n']})"),
              ("④ 계좌 누적 > 0", ps["누적%"] > 0, f"{ps['누적%']}%"),
              ("④ 계좌 MDD < KODEX200 MDD", abs(ps["MDD%"]) < abs(kstat["MDD%"]), f"{ps['MDD%']}% vs {kstat['MDD%']}%")]
    print("\n  [판정]")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    m1_ok = all(x[1] for x in checks[:5])
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'} (M1 {'합격' if m1_ok else '실패'})")

    # 참고 (판정에 안 씀)
    s1 = d[d["A1"] <= -0.03]
    late = s1[s1["A2"] > -0.03]
    print("\n  [참고]")
    print(f"    1차 신호(NXT 종가 기준) {len(s1):,}건 중 2차 신호에도 해당 {(s1['A2'] <= -0.03).mean() * 100:.1f}%")
    print(f"    마지막 10분에 처음 −3% 넘은 1차 신호 {len(late):,}건 ({len(late) / max(len(s1), 1) * 100:.1f}%), "
          f"1차 방식(NXT 종가 진입) 성과 {fmt(st(late['r_n']))}")
    print(f"    1차 방식으로 같은 기간 1차 신호 성과 {fmt(st(s1['r_n']))}")
    q = sig["aval"].quantile([0.1, 0.25, 0.5, 0.75, 0.9]) / 1e6
    print("    신호 종목 애프터(15:40~20:00) 거래대금 백만 원: " + ", ".join(f"p{int(k * 100)} {v:,.1f}" for k, v in q.items()))
    for name, part in (("1차 확인 구간 이전", sig[sig["date"] < CUT1]), ("1차 확인 구간(2026-02-19~)", sig[sig["date"] >= CUT1])):
        print(f"    {name} M1 {fmt(st(part['r_m1']))}")


if __name__ == "__main__":
    main()
