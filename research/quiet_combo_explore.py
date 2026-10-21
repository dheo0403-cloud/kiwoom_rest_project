"""
조용한 날(NR7) + 현재 봇 규칙 결합 탐색 (research/quiet_combo_plan.md, 2026-10-13 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/quiet_combo_explore.py

설계 문서에 없던 세부(결과 보기 전 고정):
- RSI14: 일간 상승폭·하락폭의 14일 단순평균, 하락 평균 0이면 100.
- TR = max(H−L, |H−전일C|, |L−전일C|), 전일 종가가 없으면 H−L.
- 무작위 기준선 R의 보유 길이는 신호 거래의 (청산 인덱스 − 진입 인덱스), 그 날 종가에 청산.
- 청산일 k가 다음 신호의 D가 될 수 있다(진입은 k+1).
"""
import os

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
BUY, SELL = 0.00095, 0.00275
K_BREAKOUT, STOP_ATR, STOP_PCT = 0.5, 1.5, 0.03
SEEDS = 5
CAPITAL = 100_000


def simulate(S, o, h, l, c, atr, J, hold):
    """→ [(진입 인덱스, 청산 인덱스, 수익률, 사유, 진입가)]"""
    n, t, out = len(o), 0, []
    while t < n - 1:
        if not (S[t] and atr[t] > 0):
            t += 1
            continue
        e = t + 1
        if not (o[e] > 0 and h[e] > 0):
            t += 1
            continue
        trig = max(h[t], o[e] + K_BREAKOUT * atr[t])
        if o[e] >= trig:
            px, gap = o[e], True
        elif h[e] >= trig:
            px, gap = trig, False
        else:
            t += 1
            continue
        stop = max(px - STOP_ATR * atr[t], px * (1 - STOP_PCT))
        if (gap or h[e] - o[e] <= o[e] - l[e]) and l[e] <= stop:
            ex = (e, stop, "손절")
        elif hold == 1:
            ex = (e, c[e], "시간")
        else:
            ex, cnt, k = None, 1, e
            while ex is None:
                k += 1
                if k >= n:
                    break
                if not o[k] > 0:
                    continue
                cnt += 1
                if o[k] <= stop:
                    ex = (k, o[k], "손절")
                elif l[k] <= stop:
                    ex = (k, stop, "손절")
                elif cnt == hold:
                    ex = (k, c[k], "시간")
            if ex is None:
                break
        k, xp, why = ex
        if not J[e:k + 1].any():
            out.append((e, k, xp * (1 - SELL) / (px * (1 + BUY)) - 1, why, px))
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
    px = pd.read_sql("SELECT code, date, open, high, low, close, value FROM daily_ohlcv", cn)
    common = set(pd.read_sql("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                                AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""", cn)["code"])
    cn.close()
    px = px[px["code"].isin(common)]
    dates = sorted(px["date"].unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, lo, cl, val = (piv(k) for k in ("open", "high", "low", "close", "value"))

    pc = cl.shift(1)
    tr = np.fmax(hi - lo, np.fmax((hi - pc).abs().fillna(0), (lo - pc).abs().fillna(0)))
    atr = tr.rolling(14).mean()
    dlt = cl.diff()
    up, dn = dlt.clip(lower=0).rolling(14).mean(), (-dlt.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn)).where(dn > 0, 100.0)
    rng = hi - lo
    nr7 = (rng <= rng.rolling(7).min()) & rng.notna()
    base = ((cl.notna().cumsum() >= 21) & (hi > lo) & (cl >= cl.rolling(20).mean() * 0.99) & (rsi < 80)
            & (val.where(cl >= 1000).rank(axis=1, ascending=False, method="first") <= 300))
    sig = {"NR7": (nr7 & base).to_numpy(), "B1": base.to_numpy()}
    uni = (val.where(cl >= 1000).rank(axis=1, ascending=False, method="first") <= 300).to_numpy()
    J = (cl.pct_change(fill_method=None).abs() > 0.30).to_numpy()
    O, Hh, Ll, C, A = (x.to_numpy() for x in (op, hi, lo, cl, atr))
    n = len(dates)

    rows = []  # (진입일, 후보, 그룹, 수익률, 사유, 진입가)
    for j in range(O.shape[1]):
        o, c, U, Jj = O[:, j], C[:, j], uni[:, j], J[:, j]
        valid_r = None
        for cand, hold in (("C1", 1), ("C2", 5)):
            for g, S in sig.items():
                for e, k, r, why, p in simulate(S[:, j], o, Hh[:, j], Ll[:, j], c, A[:, j], Jj, hold):
                    rows.append((dates[e], cand, g, r, why, p))
                    if g != "NR7":
                        continue
                    if valid_r is None:
                        valid_r = [s for s in range(n - 1) if U[s] and o[s + 1] > 0]
                    L = k - e
                    pool = [s for s in valid_r if s + 1 + L < n and c[s + 1 + L] > 0 and not Jj[s + 1:s + 2 + L].any()]
                    for sd in range(SEEDS if pool else 0):
                        s = pool[np.random.default_rng([sd, j, e, hold]).integers(len(pool))]
                        rows.append((dates[e], cand, f"Rs{sd}", c[s + 1 + L] * (1 - SELL) / (o[s + 1] * (1 + BUY)) - 1, "", 0.0))
    df = pd.DataFrame(rows, columns=["date", "cand", "g", "ret", "why", "px"])
    tdays = sorted(df[df["g"] == "NR7"]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "탐색", "확인")

    def view(part, cand):
        d = df[(df["part"] == part) & (df["cand"] == cand)]
        s = st(d[d["g"] == "NR7"]["ret"])
        b1 = d[d["g"] == "B1"]["ret"].mean() * 100
        rr = np.mean([d[d["g"] == f"Rs{k}"]["ret"].mean() for k in range(SEEDS)]) * 100
        return s, b1, rr, d[d["g"] == "NR7"]

    print(f"[NR7 + 봇 규칙] 진입일 {tdays[0]}~{tdays[-1]} ({len(tdays)}일), 탐색 < {cut} ≤ 확인, 종목 {O.shape[1]:,}")
    for part in ("탐색", "확인"):
        for cand in ("C1", "C2"):
            s, b1, rr, _ = view(part, cand)
            print(f"  [{part}] {cand}: n={s['n']:,} EV={s['ev']:+.3f}% PF={s['pf']:.2f} 승률={s['win']:.1f}% "
                  f"/ B1 {b1:+.3f}% / R {rr:+.3f}%")

    cand = max(("C1", "C2"), key=lambda x: view("탐색", x)[0]["ev"])
    s, b1, rr, d = view("확인", cand)
    mon = d.groupby(d["date"].str[:6])["ret"].mean()
    checks = [("거래당 ≥ +0.30%", s["ev"] >= 0.30, f"{s['ev']:+.3f}%"),
              ("B1 대비 ≥ +0.30%p", s["ev"] - b1 >= 0.30, f"{s['ev'] - b1:+.3f}%p (B1 {b1:+.3f}%)"),
              ("R 대비 ≥ +0.30%p", s["ev"] - rr >= 0.30, f"{s['ev'] - rr:+.3f}%p (R {rr:+.3f}%)"),
              ("PF ≥ 1.1", s["pf"] >= 1.1, f"{s['pf']:.2f}"),
              ("양수 달 ≥ 60%", (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("거래 ≥ 300", s["n"] >= 300, f"{s['n']:,}")]
    print(f"\n  [판정] 탐색 최고 = {cand}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))

    for cand in ("C1", "C2"):
        a = df[(df["cand"] == cand) & (df["g"] == "NR7")]
        print(f"\n  [참고] {cand} 전 기간")
        for y, gy in a.groupby(a["date"].str[:4]):
            ry = np.mean([df[(df["cand"] == cand) & (df["g"] == f"Rs{k}") & (df["date"].str[:4] == y)]["ret"].mean()
                          for k in range(SEEDS)]) * 100
            print(f"    {y}: n={len(gy):,} EV={gy['ret'].mean() * 100:+.3f}% / R {ry:+.3f}%")
        for why, gw in a.groupby("why"):
            print(f"    청산 {why}: {len(gw):,}건 ({len(gw) / len(a) * 100:.0f}%) 평균 {gw['ret'].mean() * 100:+.3f}%")
        feas = a["px"] <= CAPITAL
        print(f"    10만 원으로 1주 이상 가능: {feas.mean() * 100:.1f}%")


if __name__ == "__main__":
    main()
