"""
장대양봉 중심값 규칙 1차 탐색 (research/midpoint_plan.md, 2026-10-12 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/midpoint_explore.py

설계 문서에 없던 세부(결과 보기 전 고정):
- 거래정지 등으로 종가가 없는 날은 직전 종가로 본다(중심값 판정·평가 모두).
- 무작위 R20은 그날 대상 300 중 신호 종목 자신도 뽑힐 수 있다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
BUY, SELL = 0.00095, 0.00275
TOP_N, LOOKBACK, BODY, HOLD = 300, 250, 0.05, 20


def load():
    c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                        password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    q = lambda sql: pd.read_sql(sql, c)
    px = q("SELECT code, date, open, high, close, value FROM daily_ohlcv WHERE code NOT LIKE '%%\\_AL%%'")
    common = set(q("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                      AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""")["code"])
    c.close()
    px = px[px["code"].isin(common)]
    return px


def net(buy, sell):
    """비용 포함 수익률"""
    return sell * (1 - SELL) / (buy * (1 + BUY)) - 1


def summary(r):
    r = pd.Series(r).dropna()
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    return dict(건수=len(r), 평균=round(r.mean() * 100, 2), 중앙값=round(r.median() * 100, 2), 승률=round((r > 0).mean() * 100, 1),
                PF=round(gain / loss, 2) if loss > 0 else np.inf, 하위10=round(r.quantile(0.1) * 100, 2))


def main():
    px = load()
    dates = sorted(px["date"].unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, cl, val = piv("open"), piv("high"), piv("close"), piv("value")
    clf = cl.ffill()
    jump = (cl.pct_change(fill_method=None).abs() > 0.30).astype(float).rolling(LOOKBACK + 1, min_periods=1).max() > 0
    prev_high = hi.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()

    rows, rand = [], {s: [] for s in range(5)}
    for t in range(LOOKBACK, len(dates) - HOLD - 1):
        d = dates[t]
        v = val.loc[d]
        u = v[(cl.loc[d, v.index] >= 1000) & ~jump.loc[d]].dropna().nlargest(TOP_N).index
        o, c = op.loc[d, u], cl.loc[d, u]
        sig = u[(c > prev_high.loc[d, u]) & ((c - o) / o >= BODY)]
        for code in sig:
            entry, mid = c[code], (o[code] + c[code]) / 2
            path = clf[code].iloc[t + 1:t + HOLD + 1].values
            mid_ret = net(entry, path[-1])
            for k, pk in enumerate(path, start=1):
                if pk < mid:
                    nxt = op[code].iloc[t + k + 1]
                    mid_ret = net(entry, nxt if nxt > 0 else pk)
                    break
            kept = path[0] >= mid
            rows.append(dict(date=d, code=code, MID=mid_ret, H20=net(entry, path[-1]), H1=net(entry, path[0]),
                             kept=kept, fwd=net(path[0], clf[code].iloc[t + HOLD + 1])))
        for s in rand:
            for _ in sig:
                rc = np.random.default_rng([s, t, len(rand[s])]).choice(list(u))
                rand[s].append(net(cl.at[d, rc], clf[rc].iloc[t + HOLD]))

    ev = pd.DataFrame(rows)
    print(f"신호 기간 {dates[LOOKBACK]}~{dates[len(dates) - HOLD - 2]}, 사건 {len(ev)}개, 종목 {ev['code'].nunique()}개")
    r20 = np.mean([summary(rand[s])["평균"] for s in rand])
    table = pd.DataFrame({k: summary(ev[k]) for k in ("MID", "H20", "H1")}).T
    print(table.to_string())
    print(f"R20(무작위 5시드) 평균 {r20:+.2f}%")

    ev["year"] = ev["date"].str[:4]
    yr = ev.groupby("year").agg(건수=("MID", "size"), MID=("MID", "mean"), H20=("MID", lambda x: ev.loc[x.index, "H20"].mean()))
    print("\n[신호 연도별 평균 %]\n" + (yr.assign(MID=yr["MID"] * 100, H20=yr["H20"] * 100).round(2)).to_string())

    k, nk = summary(ev.loc[ev["kept"], "fwd"]), summary(ev.loc[~ev["kept"], "fwd"])
    print(f"\n[주장 ①] D+1 종가 지킴 {k}\n         못 지킴 {nk}")
    hold_days = ev.loc[ev["MID"] != ev["H20"]]
    print(f"MID 중도 청산 비율 {len(hold_days) / len(ev):.0%}")

    m, h = table.loc["MID"], table.loc["H20"]
    yrs_ok = all(r["MID"] > 0 for _, r in yr.iterrows() if r["건수"] >= 20)
    checks = [
        ("사건 수 ≥ 100", len(ev) >= 100, f"{len(ev)}"),
        ("MID 평균 > 0, PF ≥ 1.2", m["평균"] > 0 and m["PF"] >= 1.2, f"{m['평균']:+.2f}%, PF {m['PF']}"),
        ("MID ≥ R20 + 1%p", m["평균"] >= r20 + 1, f"{m['평균']:+.2f} vs {r20:+.2f}"),
        ("MID 평균 ≥ H20, 하위10% 개선", m["평균"] >= h["평균"] and m["하위10"] > h["하위10"],
         f"평균 {m['평균']:+.2f} vs {h['평균']:+.2f}, 하위10 {m['하위10']} vs {h['하위10']}"),
        ("연도별 MID 평균 > 0", yrs_ok, ", ".join(f"{y} {r['MID'] * 100:+.2f}" for y, r in yr.iterrows())),
        ("지킴 − 못 지킴 ≥ +1%p", k["평균"] - nk["평균"] >= 1, f"{k['평균'] - nk['평균']:+.2f}%p"),
    ]
    print("\n[판정]")
    for name, ok, v in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {v}")
    print(f"  결과: {'합격' if all(x[1] for x in checks) else '불합격'}")


if __name__ == "__main__":
    sys.exit(main())
