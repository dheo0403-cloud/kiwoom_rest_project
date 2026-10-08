"""
외국인 순매수 결합 탐색 B (research/foreign_flow_plan.md, 2026-10-07 사전 고정) — DB 조회 전용

설계 문서에 없던 세부는 결과를 보기 전(2026-10-09)에 여기 고정한다:
- 거래일 달력 = daily_ohlcv에 행이 있는 날짜 전체. F5·보유 기간은 이 달력 기준 거래일로 센다.
- F5: D-1까지 최근 5거래일 모두에 investor_daily·daily_ohlcv 행이 있는 종목만 계산(하나라도 없으면 그날 제외).
  분모(5일 거래대금 합, daily_ohlcv.value 원 → 백만원)가 0이면 제외.
- 대상: D-1 거래대금 상위 300 (stock_master 보통주: is_etf=0, KOSPI/KOSDAQ, 코드 끝 0, 스팩 제외, '_AL' 코드 제외),
  D-1 종가 >= 1,000. F5를 계산할 수 없는 종목은 상위 300을 고른 뒤 뺀다(300을 다시 채우지 않음).
- 강함 = 그날 대상 중 F5 상위 20% (개수 = floor(대상 수 × 0.2)), 약함 = 하위 20% (참고).
- 데이터 품질: 진입 D-1 ~ 청산일 사이 하루 종가 변동이 ±30% 초과면(가격제한폭 밖 = 수정주가 의심) 그 거래 제외.
  D 시가 또는 청산일 종가가 없거나 0이면 제외. 청산일이 데이터 끝을 넘으면 제외.
- 무작위: 같은 날 같은 대상에서 강함과 같은 개수, 시드 0~4. 시드별 평균을 다시 평균.
- 월별 승리: 진입일 월 기준, 그 달 강함 거래당 평균 > 무작위 거래당 평균(5시드 평균)이면 승.
- 탐색/확인: 진입일 D 기준 거래일을 시간순 앞 60% / 뒤 40%.
- H 선택(탐색 구간): (강함 거래당 평균 − 무작위 거래당 평균)이 큰 H. 확인 구간은 그 H만 평가.
- KODEX200(069500): 같은 진입일 집합(강함 거래가 1건 이상인 날)에 1건씩, 비용은 거래세 0.
"""
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

BUY_COST = 0.0008 + 0.00015            # 매수 슬리피지 + 수수료
SELL_COST = 0.0008 + 0.00015 + 0.0018  # 매도 슬리피지 + 수수료 + 거래세
SELL_COST_ETF = 0.0008 + 0.00015       # ETF는 거래세 0
H_LIST = (5, 20)
TOP_N, PCT, SEEDS = 300, 0.2, range(5)
PASS = dict(mean=0.30, vs_random=0.30, vs_kodex=0.30, month_win=0.60, trades=300)  # %·비율·건수


def load():
    c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                        password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=600)
    q = lambda sql: pd.read_sql(sql, c)
    px = q("SELECT code, date, open, close, value FROM daily_ohlcv")
    inv = q("SELECT code, date, frgn FROM investor_daily")
    common = set(q("""SELECT code FROM stock_master WHERE is_etf = 0 AND market IN ('KOSPI','KOSDAQ')
                      AND code REGEXP '^[0-9]{5}0$' AND name NOT LIKE '%%스팩%%'""")["code"])
    c.close()
    return px, inv, common


def trade_ret(o, cl, etf=False):
    return cl * (1 - (SELL_COST_ETF if etf else SELL_COST)) / (o * (1 + BUY_COST)) - 1


def main():
    px, inv, common = load()
    dates = sorted(px["date"].unique())
    di = {d: i for i, d in enumerate(dates)}
    stocks = px[px["code"].isin(common) & ~px["code"].str.contains("_AL")]
    op = stocks.pivot(index="date", columns="code", values="open").reindex(dates)
    cl = stocks.pivot(index="date", columns="code", values="close").reindex(dates)
    val = stocks.pivot(index="date", columns="code", values="value").reindex(dates) / 1e6  # 원 → 백만원
    fr = inv[inv["code"].isin(op.columns)].pivot(index="date", columns="code", values="frgn").reindex(dates)
    kd = px[px["code"] == "069500"].set_index("date").reindex(dates)

    f5 = fr.rolling(5, min_periods=5).sum() / val.rolling(5, min_periods=5).sum().replace(0, np.nan)  # D-1 행의 값 = D-1까지 5일
    chg = cl.pct_change(fill_method=None).abs()

    inv_dates = set(inv["date"])
    start = max(dates.index(min(d for d in dates if d in inv_dates)) + 6, 1)
    rows = []  # (진입일, 그룹, 시드, H, 수익률)
    kodex = []  # (진입일, H, 수익률)
    for t in range(start, len(dates)):
        prev = dates[t - 1]
        v, c_prev, f = val.loc[prev], cl.loc[prev], f5.loc[prev]
        uni = v[(c_prev >= 1000)].dropna().nlargest(TOP_N).index
        uni = [s for s in uni if pd.notna(f.get(s))]
        n = int(len(uni) * PCT)
        if n == 0:
            continue
        ranked = f[uni].sort_values(ascending=False)
        groups = {"strong": list(ranked.index[:n]), "weak": list(ranked.index[-n:])}
        for s in SEEDS:
            groups[f"rand{s}"] = list(np.random.default_rng([s, t]).choice(uni, n, replace=False))
        for H in H_LIST:
            e = t + H - 1
            if e >= len(dates):
                continue
            for g, codes in groups.items():
                for code in codes:
                    o, x = op.at[dates[t], code], cl.at[dates[e], code]
                    if not (o > 0 and x > 0) or (chg[code].iloc[t - 1:e + 1] > 0.30).any():
                        continue
                    rows.append((dates[t], g, H, trade_ret(o, x)))
            ko, kx = kd["open"].iloc[t], kd["close"].iloc[e]
            if ko > 0 and kx > 0:
                kodex.append((dates[t], H, trade_ret(ko, kx, etf=True)))

    df = pd.DataFrame(rows, columns=["date", "group", "H", "ret"])
    kx = pd.DataFrame(kodex, columns=["date", "H", "ret"])
    trade_days = sorted(df["date"].unique())
    cut = trade_days[int(len(trade_days) * 0.6)]
    df["part"] = np.where(df["date"] < cut, "explore", "confirm")
    kx["part"] = np.where(kx["date"] < cut, "explore", "confirm")
    df["month"] = df["date"].str[:6]

    def stats(part, H):
        d = df[(df["part"] == part) & (df["H"] == H)]
        st = d[d["group"] == "strong"]
        rnd = d[d["group"].str.startswith("rand")]
        rnd_mean = rnd.groupby("group")["ret"].mean().mean()
        k = kx[(kx["part"] == part) & (kx["H"] == H) & kx["date"].isin(st["date"].unique())]["ret"].mean()
        m_st = st.groupby("month")["ret"].mean()
        m_rd = rnd.groupby(["month", "group"])["ret"].mean().groupby("month").mean()
        win = (m_st > m_rd.reindex(m_st.index)).mean()
        return dict(trades=len(st), strong=st["ret"].mean() * 100, weak=d[d["group"] == "weak"]["ret"].mean() * 100,
                    random=rnd_mean * 100, kodex=k * 100, month_win=win, months=len(m_st),
                    monthly=pd.DataFrame({"strong%": m_st * 100, "random%": m_rd.reindex(m_st.index) * 100}))

    print(f"기간: 진입일 {trade_days[0]} ~ {trade_days[-1]} ({len(trade_days)}거래일), 탐색/확인 경계 {cut}")
    print(f"가격 데이터 {dates[0]}~{dates[-1]}, 외국인 데이터 {min(inv_dates)}~{max(inv_dates)}, 보통주 {len(op.columns)}종목")
    ex = {H: stats("explore", H) for H in H_LIST}
    for H, s in ex.items():
        print(f"[탐색] H={H}: 강함 {s['strong']:+.3f}% ({s['trades']}건) / 무작위 {s['random']:+.3f}% / 약함 {s['weak']:+.3f}% "
              f"/ KODEX200 {s['kodex']:+.3f}% / 무작위 이긴 달 {s['month_win']:.0%} ({s['months']}개월)")
    H = max(H_LIST, key=lambda h: ex[h]["strong"] - ex[h]["random"])
    s = stats("confirm", H)
    checks = {
        f"거래당 ≥ +{PASS['mean']}%": s["strong"] >= PASS["mean"],
        f"무작위 대비 ≥ +{PASS['vs_random']}%p": s["strong"] - s["random"] >= PASS["vs_random"],
        f"KODEX200 대비 ≥ +{PASS['vs_kodex']}%p": s["strong"] - s["kodex"] >= PASS["vs_kodex"],
        f"무작위 이긴 달 ≥ {PASS['month_win']:.0%}": s["month_win"] >= PASS["month_win"],
        f"거래 ≥ {PASS['trades']}건": s["trades"] >= PASS["trades"],
    }
    print(f"\n[확인] 선택 H={H}: 강함 {s['strong']:+.3f}% ({s['trades']}건) / 무작위 {s['random']:+.3f}% / 약함 {s['weak']:+.3f}% "
          f"/ KODEX200 {s['kodex']:+.3f}% / 무작위 이긴 달 {s['month_win']:.0%} ({s['months']}개월)")
    for k, ok in checks.items():
        print(f"  {'✅' if ok else '❌'} {k}")
    print(f"판정: {'합격' if all(checks.values()) else '불합격'}")
    print("\n[확인 구간 월별]")
    print(s["monthly"].round(3).to_string())
    print(f"\n제외: 데이터 품질(±30% 초과 등)로 빠진 거래는 집계에 없음. 생존 편향·보유 기간 겹침(독립 집계)·백만원 반올림은 한계.")


if __name__ == "__main__":
    sys.exit(main())
