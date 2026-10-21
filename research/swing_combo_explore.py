"""
수급 추세 스윙 1차 탐색 (research/swing_combo_plan.md, 2026-10-10 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/swing_combo_explore.py <ks11.json>

설계 문서에 없던 세부(결과 보기 전 고정):
- 시작일 = 결합 점수가 있는 종목이 50개 이상인 첫 거래일. 그날 필터가 '보유'면 바로 첫 리밸런싱.
- 정수 주식(C3)에서 한 종목 몫으로 1주도 못 사면 다음 순위 중 살 수 있는 종목으로 대체(봇이 실제로 할 동작). 대체할 종목이 없으면 현금.
- ETF(069500) 매도 비용은 거래세 0 (0.095%).
- 백분위 순위 = rank(pct=True), 점수 동점은 코드 오름차순.
"""
import os
import sys

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_backtest import load_yahoo, month_signal  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
BUY, SELL, SELL_ETF = 0.00095, 0.00275, 0.00095
TOP_N, HOLD, CAPITAL = 300, 20, 103_541
ETF = "069500"


def load():
    c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                        password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    q = lambda sql: pd.read_sql(sql, c)
    px = q("SELECT code, date, open, close, volume, value FROM daily_ohlcv WHERE code NOT LIKE '%%\\_AL%%'")
    inv = q("SELECT code, date, frgn FROM investor_daily")
    common = set(q("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                      AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""")["code"])
    c.close()
    return px, inv, common


class Data:
    def __init__(self, px, inv, common, ks):
        self.dates = sorted(px["date"].unique())
        piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(self.dates).astype(float)
        self.op, self.cl, vol, val = piv("open"), piv("close"), piv("volume"), piv("value")
        self.clf = self.cl.ffill()
        stocks = [c for c in self.cl.columns if c in common]
        cl, vol, val = self.cl[stocks], vol[stocks], val[stocks]
        fr = inv[inv["code"].isin(stocks)].pivot(index="date", columns="code", values="frgn").reindex(self.dates).reindex(columns=stocks)
        self.val = val
        self.f5 = fr.rolling(5, min_periods=5).sum() / (val / 1e6).rolling(5, min_periods=5).sum().replace(0, np.nan)
        s = np.sign(cl.diff()) * vol
        self.o20 = s.rolling(20, min_periods=20).sum() / vol.rolling(20, min_periods=20).sum().replace(0, np.nan)
        self.mom = cl.shift(5) / cl.shift(65) - 1
        self.jump = (cl.pct_change(fill_method=None).abs() > 0.30).astype(float).rolling(65, min_periods=1).max() > 0
        self.sig = month_signal(ks)
        self.month = pd.PeriodIndex(pd.to_datetime(self.dates), freq="M")

    def universe(self, t):
        p = self.dates[t - 1]
        v = self.val.loc[p]
        ok = (self.cl.loc[p, v.index] >= 1000) & ~self.jump.loc[p]
        return v[ok].dropna().nlargest(TOP_N).index

    def ranked(self, t):
        p = self.dates[t - 1]
        u = self.universe(t)
        sc = pd.DataFrame({"f5": self.f5.loc[p, u], "o20": self.o20.loc[p, u], "mom": self.mom.loc[p, u]}).dropna()
        if sc.empty:
            return []
        score = sc.rank(pct=True).mean(axis=1)
        return list(score.sort_index().sort_values(ascending=False, kind="stable").index)

    def regime(self, t):
        v = self.sig.get(self.month[t] - 1)
        return bool(v) if v is not None and not pd.isna(v) else False


def simulate(data, start, pick, n, capital, integer, use_filter=True):
    """pick(t) → 순위 목록. 반환: 일별 자산(종가 평가)"""
    cash, hold, last, eq = capital, {}, None, []
    dates, op, clf = data.dates, data.op, data.clf

    def price_open(t, c):
        o = op.at[dates[t], c]
        return o if o > 0 else clf.at[dates[t - 1], c]

    def sell(t, c):
        nonlocal cash
        cash += hold.pop(c) * price_open(t, c) * (1 - (SELL_ETF if c == ETF else SELL))

    for t in range(start, len(dates)):
        first = t == start or data.month[t] != data.month[t - 1]
        on = data.regime(t) if use_filter else True
        if first and not on and hold:
            for c in list(hold):
                sell(t, c)
        if on and (last is None or (first and not hold) or t - last >= HOLD):
            last = t
            ranks = pick(t)
            target = ranks[:n]
            for c in [c for c in hold if c not in target]:
                sell(t, c)
            slots = n - len(hold)
            cands = [c for c in (ranks if integer else target) if c not in hold]
            per = cash / slots if slots > 0 else 0
            for c in cands:
                if slots <= 0:
                    break
                o = op.at[dates[t], c]
                if not o > 0:
                    continue
                q = np.floor(per / (o * (1 + BUY))) if integer else per / (o * (1 + BUY))
                if q <= 0:
                    continue
                cash -= q * o * (1 + BUY)
                hold[c] = q
                slots -= 1
        eq.append(cash + sum(q * clf.at[dates[t], c] for c, q in hold.items()))
    return pd.Series(eq, index=pd.to_datetime(dates[start:]))


def st(e):
    yrs = len(e) / 245
    return dict(누적=round((e.iloc[-1] / e.iloc[0] - 1) * 100, 1), 연환산=round(((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100, 1),
                MDD=round(((e / e.cummax()).min() - 1) * 100, 1), 최종=round(e.iloc[-1]))


def main():
    px, inv, common = load()
    data = Data(px, inv, common, load_yahoo(sys.argv[1]))
    start = next(t for t in range(70, len(data.dates)) if len(data.ranked(t)) >= 50)
    print(f"기간 {data.dates[start]}~{data.dates[-1]} ({len(data.dates) - start}거래일), 필터 '보유' 비중 "
          f"{np.mean([data.regime(t) for t in range(start, len(data.dates))]):.0%}")

    cache = {}
    pick = lambda t: cache.setdefault(t, data.ranked(t))
    res = {"C10": simulate(data, start, pick, 10, 1.0, False),
           "C10-필터없음": simulate(data, start, pick, 10, 1.0, False, use_filter=False)}
    rs = []
    for s in range(5):
        rp = lambda t, s=s: list(np.random.default_rng([s, t]).permutation(list(data.universe(t))))
        rs.append(simulate(data, start, rp, 10, 1.0, False))
    res["R10(5시드 평균)"] = sum(rs) / 5
    res["KODEX200 보유"] = simulate(data, start, lambda t: [ETF], 1, 1.0, False, use_filter=False)
    res["추세 ETF"] = simulate(data, start, lambda t: [ETF], 1, 1.0, False)
    c3 = simulate(data, start, pick, 3, CAPITAL, True)
    k3 = simulate(data, start, lambda t: [ETF], 1, CAPITAL, True, use_filter=False)
    table = pd.DataFrame({k: st(v) for k, v in res.items()}).T.drop(columns="최종")
    print(table.to_string())
    print(f"\nC3 (103,541원, 정수 주식): {st(c3)}  |  같은 자금 KODEX200 정수 보유: {st(k3)}")

    yr = pd.DataFrame({k: v.groupby(v.index.year).apply(lambda x: (x.iloc[-1] / x.iloc[0] - 1) * 100) for k, v in {**res, "C3": c3}.items()})
    print("\n[연도별 %]\n" + yr.round(1).T.to_string())
    half = len(res["C10"]) // 2
    h = {k: [(v.iloc[half - 1] / v.iloc[0] - 1) * 100, (v.iloc[-1] / v.iloc[half - 1] - 1) * 100] for k, v in res.items()}
    print("\n[앞·뒤 절반 누적 %] " + " | ".join(f"{k} {a:+.1f} / {b:+.1f}" for k, (a, b) in h.items()))

    c, k, tr, r = (table.loc[x] for x in ("C10", "KODEX200 보유", "추세 ETF", "R10(5시드 평균)"))
    checks = [
        ("C10 연환산 ≥ KODEX200 + 3%p", c["연환산"] >= k["연환산"] + 3, f"{c['연환산']:+.1f} vs {k['연환산']:+.1f}"),
        ("C10 연환산 ≥ 추세 ETF", c["연환산"] >= tr["연환산"], f"{c['연환산']:+.1f} vs {tr['연환산']:+.1f}"),
        ("C10 − R10 ≥ +3%p", c["연환산"] - r["연환산"] >= 3, f"{c['연환산'] - r['연환산']:+.1f}%p"),
        ("C10 MDD ≤ KODEX200 MDD", c["MDD"] >= k["MDD"], f"{c['MDD']} vs {k['MDD']}"),
        ("앞·뒤 절반 모두 C10 > KODEX200", all(h["C10"][i] > h["KODEX200 보유"][i] for i in (0, 1)),
         f"{h['C10'][0]:+.1f}/{h['C10'][1]:+.1f} vs {h['KODEX200 보유'][0]:+.1f}/{h['KODEX200 보유'][1]:+.1f}"),
        ("C3 최종 > KODEX200 정수 보유", c3.iloc[-1] > k3.iloc[-1], f"{c3.iloc[-1]:,.0f} vs {k3.iloc[-1]:,.0f}"),
    ]
    print("\n[판정]")
    for name, ok, v in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {v}")
    print(f"  결과: {'합격' if all(x[1] for x in checks) else '불합격'}")
    last = len(data.dates)
    print(f"\n현재 신호(마지막 거래일 다음 날 기준 상위 5): {data.ranked(last)[:5]}")


if __name__ == "__main__":
    sys.exit(main())
