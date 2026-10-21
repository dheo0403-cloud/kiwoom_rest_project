"""
안드레아 웅거 '조용한 날 다음 날 전일 고가 돌파' 탐색 (research/quiet_breakout_plan.md, 2026-10-13 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/quiet_breakout_explore.py

설계 문서에 없던 세부(결과 보기 전 고정):
- 거래정지일(시가 없음)은 보유 일수에 세지 않는다.
- 청산일 k가 다음 신호의 조용한 날 D가 될 수 있다(진입은 k+1).
- B2의 5일째 종가가 데이터 끝을 넘으면 그 거래의 B2는 빠진다.
- 평균 R의 위험폭은 실제 진입가 − L_D(갭 진입이면 H_D − L_D보다 큼).
"""
import os

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
BUY, SELL = 0.00095, 0.00275
HOLD = 5
CAPITAL, RISK = 100_000, 0.01


def simulate(S, o, h, l, c, J):
    """신호 배열 S(조용한 날 D) → [(진입일, 수익률, 사유, R, 진입가, 위험폭, B2 수익률 또는 None)]"""
    n, t, out = len(o), 0, []
    while t < n - 1:
        if not S[t]:
            t += 1
            continue
        e, H, L = t + 1, h[t], l[t]
        if not (o[e] > 0 and h[e] > 0):
            t += 1
            continue
        if o[e] > H:
            px, gap = o[e], True
        elif h[e] >= H:
            px, gap = H, False
        else:
            t += 1
            continue
        ex = None
        if (gap or h[e] - o[e] <= o[e] - l[e]) and l[e] <= L:
            ex = (e, L, "손절")
        else:
            cnt, k = 1, e
            while ex is None:
                k += 1
                if k >= n:
                    break
                if not o[k] > 0:
                    continue
                cnt += 1
                if o[k] <= L:
                    ex = (k, o[k], "손절")
                elif l[k] <= L:
                    ex = (k, L, "손절")
                elif cnt == HOLD:
                    ex = (k, c[k], "시간")
        if ex is None:
            break  # 데이터 끝까지 미청산 → 제외
        k, xp, why = ex
        if not J[e:k + 1].any():
            # B2: 진입일 시가 매수 → 5번째 유효일 종가
            b2, cnt, m = None, 1, e
            while cnt < HOLD and m < n - 1:
                m += 1
                if o[m] > 0:
                    cnt += 1
            if cnt == HOLD and c[m] > 0 and not J[e:m + 1].any():
                b2 = c[m] * (1 - SELL) / (o[e] * (1 + BUY)) - 1
            out.append((e, xp * (1 - SELL) / (px * (1 + BUY)) - 1, why, (xp - px) / (px - L), px, px - L, b2))
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

    enough = cl.notna().cumsum() >= 21
    rng = hi - lo
    q1 = (rng <= rng.rolling(7).min()) & rng.notna()
    body = (cl - op).abs() / cl
    q2 = body <= 0.5 * body.shift(1).rolling(20).mean()
    base = enough & (hi > lo) & (val.where(cl >= 1000).rank(axis=1, ascending=False, method="first") <= 300)
    groups = {"Q1": (q1 & base).to_numpy(), "Q2": (q2 & base).to_numpy(), "B1": base.to_numpy()}
    J = (cl.pct_change(fill_method=None).abs() > 0.30).to_numpy()
    O, Hh, Ll, C = (x.to_numpy() for x in (op, hi, lo, cl))

    rows = []
    for j in range(O.shape[1]):
        for g, S in groups.items():
            for e, r, why, R, p, risk, b2 in simulate(S[:, j], O[:, j], Hh[:, j], Ll[:, j], C[:, j], J[:, j]):
                rows.append((dates[e], g, r, why, R, p, risk, b2))
    df = pd.DataFrame(rows, columns=["date", "g", "ret", "why", "R", "px", "risk", "b2"])
    tdays = sorted(df[df["g"] != "B1"]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "탐색", "확인")

    print(f"[조용한 날 돌파] 진입일 {tdays[0]}~{tdays[-1]} ({len(tdays)}일), 탐색 < {cut} ≤ 확인, 종목 {O.shape[1]:,}")
    for part in ("탐색", "확인"):
        b1 = st(df[(df["part"] == part) & (df["g"] == "B1")]["ret"])
        print(f"  [{part}] B1(필터 없음): n={b1['n']:,} EV={b1['ev']:+.3f}% PF={b1['pf']:.2f} 승률={b1['win']:.1f}%")
        for q in ("Q1", "Q2"):
            d = df[(df["part"] == part) & (df["g"] == q)]
            s = st(d["ret"])
            print(f"  [{part}] {q}: n={s['n']:,} EV={s['ev']:+.3f}% PF={s['pf']:.2f} 승률={s['win']:.1f}% "
                  f"/ B2 {d['b2'].dropna().mean() * 100:+.3f}%")

    q = max(("Q1", "Q2"), key=lambda x: st(df[(df["part"] == "탐색") & (df["g"] == x)]["ret"])["ev"])
    d = df[(df["part"] == "확인") & (df["g"] == q)]
    s = st(d["ret"])
    b1 = st(df[(df["part"] == "확인") & (df["g"] == "B1")]["ret"])["ev"]
    mon = d.groupby(d["date"].str[:6])["ret"].mean()
    checks = [("거래당 ≥ +0.30%", s["ev"] >= 0.30, f"{s['ev']:+.3f}%"),
              ("B1 대비 ≥ +0.30%p", s["ev"] - b1 >= 0.30, f"{s['ev'] - b1:+.3f}%p (B1 {b1:+.3f}%)"),
              ("PF ≥ 1.1", s["pf"] >= 1.1, f"{s['pf']:.2f}"),
              ("양수 달 ≥ 60%", (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("거래 ≥ 300", s["n"] >= 300, f"{s['n']:,}")]
    print(f"\n  [판정] 탐색 최고 = {q}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))

    # 참고 지표 (판정에 쓰지 않음) — 선택된 정의, 전 기간
    a = df[df["g"] == q]
    print(f"\n  [참고] {q} 전 기간")
    for y, gy in a.groupby(a["date"].str[:4]):
        b1y = df[(df["g"] == "B1") & (df["date"].str[:4] == y)]["ret"].mean() * 100
        print(f"    {y}: n={len(gy):,} EV={gy['ret'].mean() * 100:+.3f}% / B1 {b1y:+.3f}%")
    for why, gw in a.groupby("why"):
        print(f"    청산 {why}: {len(gw):,}건 ({len(gw) / len(a) * 100:.0f}%) 평균 {gw['ret'].mean() * 100:+.3f}%")
    print(f"    평균 R {a['R'].mean():+.3f} / B2 {a['b2'].dropna().mean() * 100:+.3f}%")
    feas = (np.floor(CAPITAL * RISK / a["risk"]) >= 1) & (a["px"] <= CAPITAL)
    print(f"    10만 원·1% 위험으로 1주 이상 가능한 신호: {feas.mean() * 100:.1f}% "
          f"(그 신호만 EV {a[feas]['ret'].mean() * 100:+.3f}%, n={int(feas.sum()):,})")


if __name__ == "__main__":
    main()
