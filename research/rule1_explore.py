"""
필 타운 Rule #1 '3가지 도구' 매매 시점 탐색 (research/rule1_plan.md, 2026-10-10 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/rule1_explore.py <ks11.json>

설계 문서에 없던 세부(결과 보기 전 고정):
- EMA는 pandas ewm(adjust=False). 스토캐스틱 분모 0이면 그날 %K 없음(매수 쪽 아님).
- 지표 값이 없으면 매수 쪽도 매도 쪽도 아님(진입·청산 신호 없음).
- 체결일 시가가 없으면(거래정지) 진입은 취소, 청산은 다음 유효 시가로 미룸.
- A의 확인 구간 MDD·샤프는 확인 구간만 잘라 다시 계산(앞 구간 손익 무관).
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
ETF_COST = 0.00095
BUY, SELL = 0.00095, 0.00275
SEEDS = 5


def tools(o, h, l, c):
    """→ (모두 매수 쪽, 모두 매도 쪽, 둘 이상 매도 쪽) 불리언 (DataFrame/Series 공통)"""
    macd = c.ewm(span=8, adjust=False).mean() - c.ewm(span=17, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    ll, hh = l.rolling(14, min_periods=14).min(), h.rolling(14, min_periods=14).max()
    k = (c - ll) / (hh - ll).replace(0, np.nan) * 100
    d = k.rolling(5, min_periods=5).mean()
    ma = c.rolling(10, min_periods=10).mean()
    up = [(macd > sig), (k > d), (c > ma)]
    dn = [(macd < sig), (k < d), (c < ma)]
    all_in = up[0] & up[1] & up[2]
    all_out = dn[0] & dn[1] & dn[2]
    two_out = (dn[0].astype(int) + dn[1].astype(int) + dn[2].astype(int)) >= 2
    return all_in, all_out, two_out


# ---------------- A. 코스피 지수 ----------------
def load_ks(path):
    r = json.load(open(path, encoding="utf-8"))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")},
                      index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None).normalize())
    df = df.dropna()
    return df[~df.index.duplicated(keep="last")]


def run_index(df, exit_sig, all_in):
    cash, sh, pend, eq = 1.0, 0.0, None, []
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    ai, ex = all_in.to_numpy(), exit_sig.to_numpy()
    trades = 0
    for t in range(len(df)):
        if pend == "buy" and sh == 0:
            sh, cash, trades = cash / (o[t] * (1 + ETF_COST)), 0.0, trades + 1
        elif pend == "sell" and sh > 0:
            cash, sh, trades = sh * o[t] * (1 - ETF_COST), 0.0, trades + 1
        pend = None
        if sh == 0 and ai[t]:
            pend = "buy"
        elif sh > 0 and ex[t]:
            pend = "sell"
        eq.append((cash + sh * c[t], sh > 0))
    e = pd.DataFrame(eq, columns=["eq", "held"], index=df.index)
    return e, trades


def istats(eq):
    r = eq.pct_change().dropna()
    yrs = len(eq) / 252
    return dict(연환산=round(((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 2), 샤프=round(r.mean() / r.std() * np.sqrt(252), 3),
                MDD=round(((eq / eq.cummax()).min() - 1) * 100, 1))


def part_a(path):
    df = load_ks(path)
    df = df[df.index >= "1997-01-01"]
    ai, ao, a2 = tools(df["open"], df["high"], df["low"], df["close"])
    cut = df.index[int(len(df) * 0.6)]
    hold = df["close"] / df["close"].iloc[0]
    res = {x: run_index(df, s, ai) for x, s in (("X1", ao), ("X2", a2))}
    print(f"[A. 코스피] {df.index[0].date()}~{df.index[-1].date()} ({len(df)}일), 탐색 < {cut.date()} ≤ 확인")
    for part, m in (("탐색", df.index < cut), ("확인", df.index >= cut)):
        row = {"보유": istats(hold[m])}
        for x, (e, n) in res.items():
            row[x] = {**istats(e["eq"][m]), "보유비중%": round(e["held"][m].mean() * 100), "매매/년": round(n / (len(df) / 252), 1)}
        print(f"  [{part}]\n" + pd.DataFrame(row).T.to_string())
    x = max(res, key=lambda k: istats(res[k][0]["eq"][df.index < cut])["샤프"])
    m = df.index >= cut
    s, h = istats(res[x][0]["eq"][m]), istats(hold[m])
    checks = [("샤프 > 보유", s["샤프"] > h["샤프"], f"{s['샤프']} vs {h['샤프']}"),
              ("MDD 보유보다 10%p 이상 작음", s["MDD"] - h["MDD"] >= 10, f"{s['MDD']} vs {h['MDD']}"),
              ("연환산 보유 −2%p 이내", s["연환산"] >= h["연환산"] - 2, f"{s['연환산']} vs {h['연환산']}")]
    print(f"  [판정 A] 탐색 샤프 최고 = {x}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(q[1] for q in checks) else '불합격'}")


# ---------------- B. 개별 종목 ----------------
def part_b():
    c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                        password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    px = pd.read_sql("SELECT code, date, open, high, low, close, value FROM daily_ohlcv", c)
    common = set(pd.read_sql("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                                AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""", c)["code"])
    c.close()
    px = px[px["code"].isin(common)]
    dates = sorted(px["date"].unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, lo, cl, val = (piv(k) for k in ("open", "high", "low", "close", "value"))
    ai, ao, a2 = tools(op, hi, lo, cl)
    enough = cl.notna().cumsum() >= 26
    ai, ao, a2 = ai & enough, ao & enough, a2 & enough
    uni = (val.where(cl >= 1000).rank(axis=1, ascending=False, method="first") <= 300).to_numpy()
    jump = (cl.pct_change(fill_method=None).abs() > 0.30).to_numpy()
    O = op.to_numpy()
    n = len(dates)
    rows = []  # (진입일, 청산, 그룹, 수익률)
    for j, code in enumerate(op.columns):
        o, AI, U, J = O[:, j], ai.iloc[:, j].to_numpy(), uni[:, j], jump[:, j]
        valid_r = [s for s in range(n - 1) if U[s] and o[s + 1] > 0]
        for x, EX in (("X1", ao.iloc[:, j].to_numpy()), ("X2", a2.iloc[:, j].to_numpy())):
            t, trades = 0, []
            while t < n - 1:
                if AI[t] and U[t] and o[t + 1] > 0:
                    e = t + 1
                    k = e
                    while k < n - 1 and not EX[k]:
                        k += 1
                    xk = k + 1
                    while xk < n and not o[xk] > 0:
                        xk += 1
                    if xk >= n or not EX[k]:
                        break  # 데이터 끝까지 미청산 → 제외
                    if not J[e:xk + 1].any():
                        trades.append((e, xk))
                    t = xk
                else:
                    t += 1
            for e, xk in trades:
                d = dates[e]
                rows.append((d, x, "sig", o[xk] * (1 - SELL) / (o[e] * (1 + BUY)) - 1))
                L = xk - e
                pool = [s for s in valid_r if s + 1 + L < n and o[s + 1 + L] > 0 and not J[s + 1:s + 2 + L].any()]
                if not pool:
                    continue
                for sd in range(SEEDS):
                    s = pool[np.random.default_rng([sd, j, e]).integers(len(pool))]
                    rows.append((d, x, f"B1s{sd}", o[s + 1 + L] * (1 - SELL) / (o[s + 1] * (1 + BUY)) - 1))
    df = pd.DataFrame(rows, columns=["date", "X", "g", "ret"])
    tdays = sorted(df[df["g"] == "sig"]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "탐색", "확인")

    def st(r):
        r = np.asarray(r)
        g, l = r[r > 0].sum(), -r[r < 0].sum()
        return dict(n=len(r), ev=r.mean() * 100, pf=g / l if l > 0 else np.inf, win=(r > 0).mean() * 100)

    def view(part, x):
        d = df[(df["part"] == part) & (df["X"] == x)]
        s = st(d[d["g"] == "sig"]["ret"])
        b = np.mean([d[d["g"] == f"B1s{k}"]["ret"].mean() for k in range(SEEDS)]) * 100
        return s, b, d[d["g"] == "sig"]

    print(f"\n[B. 개별 종목] 진입일 {tdays[0]}~{tdays[-1]} ({len(tdays)}일), 탐색 < {cut} ≤ 확인, 종목 {op.shape[1]:,}")
    for part in ("탐색", "확인"):
        for x in ("X1", "X2"):
            s, b, _ = view(part, x)
            print(f"  [{part}] {x}: n={s['n']:,} EV={s['ev']:+.3f}% PF={s['pf']:.2f} 승률={s['win']:.1f}% / B1 {b:+.3f}%")
    x = max(("X1", "X2"), key=lambda xx: view("탐색", xx)[0]["ev"])
    s, b, sig = view("확인", x)
    mon = sig.groupby(sig["date"].str[:6])["ret"].mean()
    hold_days = None
    checks = [("거래당 ≥ +0.30%", s["ev"] >= 0.30, f"{s['ev']:+.3f}%"),
              ("B1 대비 ≥ +0.30%p", s["ev"] - b >= 0.30, f"{s['ev'] - b:+.3f}%p (B1 {b:+.3f}%)"),
              ("PF ≥ 1.1", s["pf"] >= 1.1, f"{s['pf']:.2f}"),
              ("양수 달 ≥ 60%", (mon > 0).mean() >= 0.6, f"{(mon > 0).sum()}/{len(mon)}"),
              ("거래 ≥ 300", s["n"] >= 300, f"{s['n']:,}")]
    print(f"  [판정 B] 탐색 최고 = {x}")
    for name, ok, v in checks:
        print(f"    {'✅' if ok else '❌'} {name}: {v}")
    print(f"    결과: {'합격' if all(q[1] for q in checks) else '불합격'}")
    print("    확인 구간 월별: " + ", ".join(f"{m} {v * 100:+.2f}%" for m, v in mon.items()))


if __name__ == "__main__":
    part_a(sys.argv[1])
    part_b()
