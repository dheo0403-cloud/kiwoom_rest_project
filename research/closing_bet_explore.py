"""
종가 매수 → 다음 날 매도 (OBV 필터) 탐색 (research/closing_bet_plan.md, 2026-10-10 사전 고정) — DB 조회 전용

실행: PYTHONIOENCODING=utf-8 python research/closing_bet_explore.py

설계 문서에 없던 세부는 결과를 보기 전(2026-10-10)에 여기 고정한다:
- 거래일 달력 = daily_ohlcv에 행이 있는 날짜 전체. D+1 = 달력의 다음 거래일.
- O20: 부호 거래량 s_D = sign(종가_D − 종가_{D−1}) × 거래량_D (전일 종가·당일 종가·거래량 중 하나라도 없으면 NaN).
  O20_D = Σ_{D−19..D} s ÷ Σ_{D−19..D} 거래량, 20개 모두 있어야 계산(→ 21거래일 연속 행). 분모 0이면 NaN.
- 대상: D 거래대금(value) 상위 300 (보통주 필터는 foreign_flow_b.py와 같음), D 종가 ≥ 1,000.
  O20을 계산할 수 없는 종목은 상위 300을 고른 뒤 뺀다(다시 채우지 않음). 강함 = 상위 20% (floor), 약함 = 하위 20%.
- 진입 제외(거래 단위, 강함·약함·무작위 모두 같은 규칙): D 종가 ≥ D−1 종가 × 1.29(상한가 근사),
  D 또는 D+1 하루 종가 변동 > ±30%, D+1 시가·고가·종가 중 없거나 0.
- 무작위: 같은 날 같은 대상에서 강함과 같은 개수, 시드 0~4 (rng = [시드, 날짜 index]). 시드별 평균을 다시 평균.
- KODEX200(069500): 강함 거래가 1건 이상인 날마다 1건, 비용 거래세 0. 상한가·±30% 제외 규칙은 적용하지 않음.
- 탐색/확인: 진입일 D 기준 (강함 거래가 있는) 거래일을 시간순 앞 60% / 뒤 40%.
- 월별 승리: 진입일 월 기준, 그 달 강함 거래당 평균 > 무작위 거래당 평균(시드별 월평균의 평균)이면 승.
"""
import os
import sys

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

BUY_COST = 0.0008 + 0.00015
SELL_COST = 0.0008 + 0.00015 + 0.0018
SELL_COST_ETF = 0.0008 + 0.00015
TOP_N, PCT, SEEDS = 300, 0.2, range(5)
EXITS = ("X1", "X2", "X3")
PASS = dict(mean=0.15, vs_random=0.20, vs_kodex=0.20, month_win=0.60, trades=300)


def load():
    c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                        password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    q = lambda sql: pd.read_sql(sql, c)
    px = q("SELECT code, date, open, high, close, volume, value FROM daily_ohlcv")
    common = set(q("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                      AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""")["code"])
    c.close()
    return px, common


def ret(entry, exit_, etf=False):
    return exit_ * (1 - (SELL_COST_ETF if etf else SELL_COST)) / (entry * (1 + BUY_COST)) - 1


def exit_price(x, entry, o, h, c):
    if x == "X1":
        return o
    if x == "X2":
        return c
    tgt = entry * 1.10
    return o if o >= tgt else (tgt if h >= tgt else c)


def main():
    px, common = load()
    dates = sorted(px["date"].unique())
    st = px[px["code"].isin(common) & ~px["code"].str.contains("_AL")]
    piv = lambda col: st.pivot(index="date", columns="code", values=col).reindex(dates).astype(float)
    op, hi, cl, vol, val = piv("open"), piv("high"), piv("close"), piv("volume"), piv("value")
    kd = px[px["code"] == "069500"].set_index("date").reindex(dates)

    s = np.sign(cl.diff()) * vol  # 전일 종가 없으면 NaN
    o20 = s.rolling(20, min_periods=20).sum() / vol.rolling(20, min_periods=20).sum().replace(0, np.nan)
    chg = cl.pct_change(fill_method=None)

    rows, kodex, hit, gaps = [], [], [], []  # rows: (진입일, 그룹, X, 수익률)
    for t in range(21, len(dates) - 1):
        D, N = dates[t], dates[t + 1]
        v = val.loc[D][cl.loc[D] >= 1000].dropna()
        uni = [c for c in v.nlargest(TOP_N).index if pd.notna(o20.at[D, c])]
        n = int(len(uni) * PCT)
        if n == 0:
            continue
        ranked = o20.loc[D, uni].sort_values(ascending=False)
        groups = {"strong": list(ranked.index[:n]), "weak": list(ranked.index[-n:])}
        for sd in SEEDS:
            groups[f"rand{sd}"] = list(np.random.default_rng([sd, t]).choice(uni, n, replace=False))
        any_strong = False
        for g, codes in groups.items():
            for code in codes:
                e, o, h, c = cl.at[D, code], op.at[N, code], hi.at[N, code], cl.at[N, code]
                if not (o > 0 and h > 0 and c > 0) or chg.at[D, code] >= 0.29 or abs(chg.at[N, code]) > 0.30:
                    continue
                for x in EXITS:
                    rows.append((D, g, x, ret(e, exit_price(x, e, o, h, c))))
                hit.append((D, g, h >= e * 1.10))
                if g == "strong":
                    any_strong = True
                    gaps.append((D, o / e - 1))
        ke, ko, kh, kc = kd["close"].iloc[t], kd["open"].iloc[t + 1], kd["high"].iloc[t + 1], kd["close"].iloc[t + 1]
        if any_strong and ke > 0 and ko > 0 and kc > 0:
            for x in EXITS:
                kodex.append((D, x, ret(ke, exit_price(x, ke, ko, kh, kc), etf=True)))

    df = pd.DataFrame(rows, columns=["date", "group", "X", "ret"])
    kx = pd.DataFrame(kodex, columns=["date", "X", "ret"])
    hit = pd.DataFrame(hit, columns=["date", "group", "hit"])
    gaps = pd.DataFrame(gaps, columns=["date", "gap"])
    tdays = sorted(df[df["group"] == "strong"]["date"].unique())
    cut = tdays[int(len(tdays) * 0.6)]
    for f in (df, kx, hit, gaps):
        f["part"] = np.where(f["date"] < cut, "explore", "confirm")
    df["month"] = df["date"].str[:6]

    def stats(part, x):
        d = df[(df["part"] == part) & (df["X"] == x)]
        sg = d[d["group"] == "strong"]
        rnd = d[d["group"].str.startswith("rand")]
        k = kx[(kx["part"] == part) & (kx["X"] == x)]["ret"].mean()
        m_st = sg.groupby("month")["ret"].mean()
        m_rd = rnd.groupby(["month", "group"])["ret"].mean().groupby("month").mean()
        return dict(trades=len(sg), strong=sg["ret"].mean() * 100, weak=d[d["group"] == "weak"]["ret"].mean() * 100,
                    random=rnd.groupby("group")["ret"].mean().mean() * 100, kodex=k * 100,
                    month_win=(m_st > m_rd.reindex(m_st.index)).mean(), months=len(m_st),
                    monthly=pd.DataFrame({"strong%": m_st * 100, "random%": m_rd.reindex(m_st.index) * 100}))

    line = lambda s: (f"강함 {s['strong']:+.3f}% ({s['trades']:,}건) / 무작위 {s['random']:+.3f}% / 약함 {s['weak']:+.3f}% "
                      f"/ KODEX200 {s['kodex']:+.3f}% / 무작위 이긴 달 {s['month_win']:.0%} ({s['months']}개월)")
    print(f"가격 {dates[0]}~{dates[-1]} ({len(dates)}거래일), 보통주 {len(op.columns):,}종목")
    print(f"진입일 {tdays[0]}~{tdays[-1]} ({len(tdays)}거래일), 탐색 < {cut} ≤ 확인")
    ex = {x: stats("explore", x) for x in EXITS}
    for x, s in ex.items():
        print(f"[탐색] {x}: {line(s)}")
    x = max(EXITS, key=lambda xx: ex[xx]["strong"] - ex[xx]["random"])
    s = stats("confirm", x)
    checks = {
        f"거래당 ≥ +{PASS['mean']}%": s["strong"] >= PASS["mean"],
        f"무작위 대비 ≥ +{PASS['vs_random']}%p": s["strong"] - s["random"] >= PASS["vs_random"],
        f"KODEX200 대비 ≥ +{PASS['vs_kodex']}%p": s["strong"] - s["kodex"] >= PASS["vs_kodex"],
        f"무작위 이긴 달 ≥ {PASS['month_win']:.0%}": s["month_win"] >= PASS["month_win"],
        f"거래 ≥ {PASS['trades']}건": s["trades"] >= PASS["trades"],
    }
    print(f"\n[확인] 선택 {x}: {line(s)}")
    for k, ok in checks.items():
        print(f"  {'✅' if ok else '❌'} {k}")
    print(f"판정: {'합격' if all(checks.values()) else '불합격'}")

    print("\n[영상 주장 대조] D+1 고가가 매수가 +10% 이상인 비율")
    for part in ("explore", "confirm"):
        h = hit[hit["part"] == part]
        rr = h[h["group"].str.startswith("rand")].groupby("group")["hit"].mean().mean()
        print(f"  {part}: 강함 {h[h['group'] == 'strong']['hit'].mean():.1%} / 약함 {h[h['group'] == 'weak']['hit'].mean():.1%} / 무작위 {rr:.1%}")
    g = gaps["gap"] * 100
    print(f"\n[D+1 시가 갭, 강함] 평균 {g.mean():+.2f}% / 하위 5% {g.quantile(0.05):+.2f}% / 하위 1% {g.quantile(0.01):+.2f}% / 최저 {g.min():+.2f}%")
    print(f"\n[확인 구간 {x} 월별]")
    print(s["monthly"].round(3).to_string())


if __name__ == "__main__":
    sys.exit(main())
