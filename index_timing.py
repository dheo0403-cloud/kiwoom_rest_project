# 사용: curl -s -A "Mozilla/5.0" -o ks11.json "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?period1=852000000&period2=<현재 유닉스초>&interval=1d"
#       PYTHONIOENCODING=utf-8 python index_timing.py ks11.json
# 코스피 지수 보유 vs 월말 추세 규칙 (사전 지정: 10개월 이평, 12개월 절대 모멘텀) — 조회·계산만
# 신호: 월말 종가로 판단 → 다음 거래일 종가에 체결(1일 지연). 현금 구간 수익 0%(이자 미반영 → 규칙에 불리).
# 비용: ETF 기준 편도 0.095%(슬리피지 0.08% + 수수료 0.015%, 증권거래세 없음). 지수라 배당 미포함.
import json, sys
import numpy as np
import pandas as pd

ONE_WAY = 0.0008 + 0.00015
r = json.load(open(sys.argv[1]))["chart"]["result"][0]
c = pd.Series(r["indicators"]["quote"][0]["close"],
              index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None).normalize()).dropna()
c = c[~c.index.duplicated(keep="last")]
month = c.index.to_period("M")
m = c.groupby(month).last().iloc[:-1]  # 진행 중인 마지막 달 제외
rules = {"10개월 이평": m > m.rolling(10).mean(), "12개월 모멘텀": m / m.shift(12) > 1}
start = max(s.dropna().index[12] for s in rules.values())  # 두 규칙 모두 판단 가능한 달부터
daily = c.pct_change()
days = c.index[(month > start) & (month <= m.index[-1] + 1)]
ret = daily.loc[days]


def stats(x: pd.Series, switches: int = 0, exposure: float = 1.0) -> dict:
    eq = (1 + x).cumprod()
    yrs = len(x) / 252
    return {"연환산%": round((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2), "변동성%": round(x.std() * np.sqrt(252) * 100, 1),
            "MDD%": round(((eq / eq.cummax()).min() - 1) * 100, 1), "누적배": round(eq.iloc[-1], 2),
            "매매/년": round(switches / yrs, 2), "보유비중%": round(exposure * 100, 0)}


rows, curves = {}, {"보유": ret}
rows["보유"] = stats(ret)
for name, sig in rules.items():
    pos_month = sig.astype(float).reindex(pd.period_range(m.index[0], m.index[-1] + 1, freq="M")).shift(1)  # 지난달 말 신호
    pos = pd.Series(pos_month.reindex(month[month.isin(pos_month.index)]).to_numpy(), index=c.index[month.isin(pos_month.index)])
    pos = pos.shift(1).loc[days].fillna(0)  # 다음 거래일 종가 체결 (1일 지연)
    flips = pos.diff().abs().fillna(0)
    x = pos * ret - flips * ONE_WAY
    curves[name] = x
    rows[name] = stats(x, int((flips > 0).sum()), pos.mean())
print(f"코스피 일봉 {c.index[0].date()} ~ {c.index[-1].date()}, 평가 {days[0].date()} ~ {days[-1].date()} ({len(days)}일)")
print(pd.DataFrame(rows).T.to_string())
print("\n[10년 구간별 연환산% / MDD%]")
for a, b in (("1998", "2007"), ("2008", "2017"), ("2018", "2026")):
    seg = {k: stats(v.loc[a:b]) for k, v in curves.items()}
    print(f"  {a}~{b}: " + " | ".join(f"{k} {s['연환산%']:+.1f} / {s['MDD%']:.1f}" for k, s in seg.items()))
print("\n[큰 하락 구간 낙폭: 보유 vs 10개월 이평 vs 12개월 모멘텀]")
for a, b in (("2000-01", "2001-09"), ("2007-10", "2009-03"), ("2011-04", "2011-10"), ("2020-01", "2020-03"), ("2021-06", "2022-10"), ("2026-06", "2026-07")):
    seg = [((1 + v.loc[a:b]).prod() - 1) * 100 for v in curves.values()]
    print(f"  {a}~{b}: " + " / ".join(f"{s:+.1f}%" for s in seg))
