"""
국내 상장 ETF 당일 돌파 단타 탐색 (research/etf_intraday_plan.md, 2026-10-13 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/etf_intraday_explore.py

설계 문서에 없던 세부(결과 보기 전 고정):
- RSI14·TR·ATR 계산은 swing_hold_explore.py와 같다.
- 계좌 시뮬레이션의 value_prev는 D 거래대금, KODEX200은 첫 진입일~마지막 날 종가.
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
BUY, SELL = 0.00095, 0.00095  # ETF: 증권거래세 없음
CANDS = {"E1": (0.5, False), "E2": (0.5, True), "E3": (0.3, False), "E4": (0.3, True)}  # k, MA20·RSI 조건
DERIV = ("레버리지", "인버스", "선물", "2X")
TH = 0.15  # 기대값·U 대비 기준(%)


def simulate(S, o, h, l, c, atr, J, k):
    """당일 청산 → [(진입 인덱스, 수익률, 사유, 진입가)]"""
    out = []
    for t in range(len(o) - 1):
        e = t + 1
        if not (S[t] and atr[t] > 0 and o[e] > 0 and h[e] > 0) or J[e]:
            continue
        trig = o[e] + k * atr[t]
        if o[e] >= trig:
            px, gap = o[e], True
        elif h[e] >= trig:
            px, gap = trig, False
        else:
            continue
        stop = max(px - 1.5 * atr[t], px * 0.97)
        if (gap or h[e] - o[e] <= o[e] - l[e]) and l[e] <= stop:
            xp, why = stop, "손절"
        else:
            xp, why = c[e], "종가"
        out.append((e, xp * (1 - SELL) / (px * (1 + BUY)) - 1, why, px))
    return out


def st(r):
    r = np.asarray(r, dtype=float)
    g, ls = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ev=r.mean() * 100 if len(r) else np.nan, pf=g / ls if ls > 0 else np.inf,
                win=(r > 0).mean() * 100 if len(r) else np.nan)


def run(px, codes_ok):
    """대상 ETF로 후보·기준선 거래 목록 생성"""
    px = px[px["code"].isin(codes_ok)]
    dates = sorted(px["date"].unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, lo, cl, val = (piv(k) for k in ("open", "high", "low", "close", "value"))
    pc = cl.shift(1)
    tr = np.fmax(hi - lo, np.fmax((hi - pc).abs().fillna(0), (lo - pc).abs().fillna(0)))
    atr = tr.rolling(14).mean()
    dlt = cl.diff()
    up, dn = dlt.clip(lower=0).rolling(14).mean(), (-dlt.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn)).where(dn > 0, 100.0)
    rows_ok = (cl.notna().cumsum() >= 21) & (hi > lo)
    filt = rows_ok & (cl >= cl.rolling(20).mean() * 0.99) & (rsi < 80)
    J = (cl.pct_change(fill_method=None).abs() > 0.30).to_numpy()
    O, Hh, Ll, C, A, V = (x.to_numpy() for x in (op, hi, lo, cl, atr, val))
    S0, S1 = rows_ok.to_numpy(), filt.to_numpy()
    rows = []
    for j, code in enumerate(op.columns):
        for cand, (k, use_f) in CANDS.items():
            S = S1 if use_f else S0
            for g, kb in (("sig", k), ("U", 0.0)):
                for e, r, why, p in simulate(S[:, j], O[:, j], Hh[:, j], Ll[:, j], C[:, j], A[:, j], J[:, j], kb):
                    rows.append((dates[e], dates[e], cand, g, r, why, p, V[e - 1, j], code))
    return pd.DataFrame(rows, columns=["date", "exit_date", "cand", "g", "ret", "why", "entry_px", "value_prev", "code"])


def main():
    cn = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                         password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    etf = pd.read_sql("SELECT code, name FROM stock_master WHERE is_etf = 1", cn)
    px = pd.read_sql("SELECT code, date, open, high, low, close, value FROM daily_ohlcv WHERE code IN (%s)"
                     % ",".join(f"'{c}'" for c in etf["code"]), cn)
    cn.close()
    names = dict(zip(etf["code"], etf["name"]))
    px = px[px["code"].isin(names)]
    plain = {c for c in names if not any(w in names[c] for w in DERIV)}
    kodex = px[px["code"] == "069500"].sort_values("date")

    df = run(px, plain)
    tdays = sorted(df[df["g"] == "sig"]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "탐색", "확인")
    raw = px[["code", "date", "close"]]
    k_rng = kodex[kodex["date"] >= tdays[0]]
    kstat = equity_stats(k_rng["close"].reset_index(drop=True), k_rng["date"].reset_index(drop=True))
    print(f"[ETF 단타] 판정 대상 {len(plain)}개 / 전체 ETF {len(names)}개, 진입일 {tdays[0]}~{tdays[-1]} "
          f"({len(tdays)}일), 탐색 < {cut} ≤ 확인")
    print(f"  KODEX200 보유 같은 기간: 누적 {kstat['누적%']}% MDD {kstat['MDD%']}% 2024 {kstat.get('2024%')}%")

    res = {}
    for cand in CANDS:
        d = df[df["cand"] == cand]
        sig, u = d[d["g"] == "sig"], d[d["g"] == "U"]
        ex = st(sig[sig["part"] == "탐색"]["ret"])
        cf, cu = st(sig[sig["part"] == "확인"]["ret"]), st(u[u["part"] == "확인"]["ret"])
        y24, u24 = st(sig[sig["date"].str[:4] == "2024"]["ret"]), st(u[u["date"].str[:4] == "2024"]["ret"])
        mon = sig[sig["part"] == "확인"].groupby(sig["date"].str[:6])["ret"].mean()
        curve = simulate_portfolio(sig[["code", "date", "exit_date", "ret", "entry_px", "value_prev"]], raw, 100_000, 5)
        ps = equity_stats(curve["equity"], curve["date"])
        res[cand] = dict(ex=ex, cf=cf, cu=cu, y24=y24, u24=u24, mon=mon, ps=ps)
        k, f = CANDS[cand]
        print(f"\n  [{cand}] k={k} / 조건 {'MA20·RSI' if f else '없음'}")
        print(f"    탐색 n={ex['n']:,} EV={ex['ev']:+.3f}% PF={ex['pf']:.2f} / U {st(u[u['part'] == '탐색']['ret'])['ev']:+.3f}%")
        print(f"    확인 n={cf['n']:,} EV={cf['ev']:+.3f}% PF={cf['pf']:.2f} 승률={cf['win']:.1f}% / U {cu['ev']:+.3f}% "
              f"/ 양수 달 {(mon > 0).sum()}/{len(mon)}")
        print(f"    2024 n={y24['n']:,} EV={y24['ev']:+.3f}% / U {u24['ev']:+.3f}%")
        print(f"    계좌: 누적 {ps['누적%']}% MDD {ps['MDD%']}% "
              + " ".join(f"{y} {ps.get(f'{y}%')}%" for y in ("2024", "2025", "2026")))
        for why, gw in sig.groupby("why"):
            print(f"    청산 {why}: {len(gw) / len(sig) * 100:.0f}% 평균 {gw['ret'].mean() * 100:+.3f}%")

    cand = max(CANDS, key=lambda x: res[x]["ex"]["ev"])
    r = res[cand]
    cf, cu, y24, u24, mon, ps = r["cf"], r["cu"], r["y24"], r["u24"], r["mon"], r["ps"]
    checks = [(f"① 거래당 ≥ +{TH}%", cf["ev"] >= TH, f"{cf['ev']:+.3f}%"),
              (f"① U 대비 ≥ +{TH}%p", cf["ev"] - cu["ev"] >= TH, f"{cf['ev'] - cu['ev']:+.3f}%p"),
              ("① PF ≥ 1.1", cf["pf"] >= 1.1, f"{cf['pf']:.2f}"),
              ("① 양수 달 ≥ 60%", (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("① 거래 ≥ 300", cf["n"] >= 300, f"{cf['n']:,}"),
              ("② 2024 거래당 ≥ 0%", y24["ev"] >= 0, f"{y24['ev']:+.3f}%"),
              (f"② 2024 U 대비 ≥ +{TH}%p", y24["ev"] - u24["ev"] >= TH, f"{y24['ev'] - u24['ev']:+.3f}%p"),
              ("③ 계좌 MDD < KODEX200 MDD", abs(ps["MDD%"]) < abs(kstat["MDD%"]), f"{ps['MDD%']}% vs {kstat['MDD%']}%"),
              ("③ 계좌 2024 ≥ KODEX200 2024", (ps.get("2024%") or -999) >= (kstat.get("2024%") or 999),
               f"{ps.get('2024%')}% vs {kstat.get('2024%')}%")]
    print(f"\n  [판정] 탐색 최고 = {cand}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))

    # 참고(판정 제외): 파생 ETF 포함 시 선택 후보
    dd = run(px, set(names))
    dd = dd[dd["cand"] == cand]
    dd["part"] = np.where(dd["date"] < cut, "탐색", "확인")
    s, u = dd[dd["g"] == "sig"], dd[dd["g"] == "U"]
    a, b = st(s[s["part"] == "확인"]["ret"]), st(u[u["part"] == "확인"]["ret"])
    y, yu = st(s[s["date"].str[:4] == "2024"]["ret"]), st(u[u["date"].str[:4] == "2024"]["ret"])
    print(f"\n  [참고] 파생 ETF 포함 {cand}: 확인 n={a['n']:,} EV={a['ev']:+.3f}% PF={a['pf']:.2f} / U {b['ev']:+.3f}% "
          f"| 2024 EV={y['ev']:+.3f}% / U {yu['ev']:+.3f}%")


if __name__ == "__main__":
    main()
