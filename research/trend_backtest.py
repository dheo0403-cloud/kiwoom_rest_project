"""
지수 ETF 월말 추세 모드 백테스트 (봇 구현 전 확인용, 조회·계산만) — 2026-10-10 결과 보기 전 고정

실행: PYTHONIOENCODING=utf-8 python research/trend_backtest.py <ks11.json> <278530.json>
  (Yahoo chart API 일봉 JSON: ^KS11 = 코스피 지수, 278530.KS = KODEX 200TR)

규칙 (index_timing.py의 10개월 이평과 같음, 봇 구현안의 실행 방식 반영):
- 신호: 완료된 달의 월말 종가 > 최근 10개 월말 종가 평균이면 '보유', 아니면 '현금'. 신호는 코스피 지수로 계산
  (봇은 069500 일봉으로 계산할 예정 — 같은 지수 추종이라 월말 신호가 사실상 같음).
- 실행: 새 달 첫 거래일 종가(봇은 15:00 시장가 예정)에 지난달 말 신호로 맞춤. 보유인데 0주면 매수, 현금인데 보유 중이면 전량 매도.
- 비용: 편도 0.095% (슬리피지 0.08% + 수수료 0.015%, ETF 거래세 0). 현금 이자 0%.
- A. 코스피 지수(분할 매수 가능) 1998~: 장기 성격 확인.
- B. KODEX 200TR 실제 가격, 시작 자금 103,541원(현재 계좌), 정수 주식만: 실제 계좌에서 나올 결과.
- 비교: 같은 시작일·같은 자금으로 첫날 사서 계속 보유.
"""
import json
import sys

import numpy as np
import pandas as pd

ONE_WAY = 0.0008 + 0.00015
CAPITAL = 103_541
MA_MONTHS = 10


def load_yahoo(path) -> pd.Series:
    r = json.load(open(path, encoding="utf-8"))["chart"]["result"][0]
    s = pd.Series(r["indicators"]["quote"][0]["close"],
                  index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None).normalize())
    s = s.dropna()
    return s[~s.index.duplicated(keep="last")].astype(float)


def month_signal(close: pd.Series) -> pd.Series:
    """월(Period) → 그 달 말 기준 보유 여부. 진행 중인 마지막 달은 제외"""
    m = close.groupby(close.index.to_period("M")).last().iloc[:-1]
    return (m > m.rolling(MA_MONTHS).mean()).where(m.rolling(MA_MONTHS).count() == MA_MONTHS)


def simulate(trade: pd.Series, sig: pd.Series, capital: float, integer: bool):
    """새 달 첫 거래일 종가에 지난달 신호로 맞춤 → 일별 자산·매매 기록"""
    month = trade.index.to_period("M")
    first_day = ~pd.Series(month, index=trade.index).duplicated()
    cash, shares, rows, trades = capital, 0.0, [], []
    started = False
    for d, px in trade.items():
        if first_day[d]:
            target = sig.get(month[trade.index.get_loc(d)] - 1)
            if target is not None and not pd.isna(target):
                started = True
                if bool(target) and shares == 0:
                    q = np.floor(cash / (px * (1 + ONE_WAY))) if integer else cash / (px * (1 + ONE_WAY))
                    if q > 0:
                        cash -= q * px * (1 + ONE_WAY)
                        shares = q
                        trades.append((d, "BUY", q, px))
                    else:
                        trades.append((d, "BUY_SKIP(1주 미만)", 0, px))
                elif not bool(target) and shares > 0:
                    cash += shares * px * (1 - ONE_WAY)
                    trades.append((d, "SELL", shares, px))
                    shares = 0.0
        if started:
            rows.append((d, cash + shares * px, shares > 0))
    eq = pd.DataFrame(rows, columns=["date", "equity", "held"]).set_index("date")
    return eq, trades


def hold(trade: pd.Series, start, capital: float, integer: bool) -> pd.Series:
    t = trade[trade.index >= start]
    px0 = t.iloc[0]
    q = np.floor(capital / (px0 * (1 + ONE_WAY))) if integer else capital / (px0 * (1 + ONE_WAY))
    cash = capital - q * px0 * (1 + ONE_WAY)
    return cash + q * t


def stats(eq: pd.Series) -> dict:
    yrs = len(eq) / 252
    r = eq.pct_change().dropna()
    return {"시작": eq.index[0].strftime("%Y-%m-%d"), "최종 자산": round(eq.iloc[-1]), "누적%": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1),
            "연환산%": round(((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 2), "변동성%": round(r.std() * np.sqrt(252) * 100, 1),
            "MDD%": round(((eq / eq.cummax()).min() - 1) * 100, 1)}


def episodes(held: pd.Series) -> str:
    """보유·현금 구간 길이(거래일 → 개월 근사)"""
    runs, cur, n = [], held.iloc[0], 0
    for h in held:
        if h == cur:
            n += 1
        else:
            runs.append((cur, n)); cur, n = h, 1
    runs.append((cur, n))
    on = [n / 21 for h, n in runs if h]
    off = [n / 21 for h, n in runs if not h]
    fmt = lambda v: f"{len(v)}회, 평균 {np.mean(v):.1f}개월, 최장 {max(v):.0f}개월" if v else "0회"
    return f"보유 구간 {fmt(on)} / 현금 구간 {fmt(off)} / 보유 비중 {held.mean():.0%}"


def report(title, trade, sig, capital, integer, years=True):
    eq, trades = simulate(trade, sig, capital, integer)
    h = hold(trade, eq.index[0], capital, integer)
    print(f"\n===== {title} =====")
    print(pd.DataFrame({"추세 모드": stats(eq["equity"]), "계속 보유": stats(h)}).T.to_string())
    yrs = len(eq) / 252
    print(f"매매 {len(trades)}회 (연 {len(trades) / yrs:.1f}회). {episodes(eq['held'])}")
    if years:
        yr = pd.DataFrame({"추세%": eq["equity"].groupby(eq.index.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1),
                           "보유%": h.groupby(h.index.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1)}) * 100
        yr["현금 비중%"] = (1 - eq["held"].groupby(eq.index.year).mean()) * 100
        print(yr.round(1).T.to_string())
    return eq, trades


def main():
    ks = load_yahoo(sys.argv[1])
    tr = load_yahoo(sys.argv[2])
    sig = month_signal(ks)

    eq_a, _ = report("A. 코스피 지수 (분할 매수, 1998~)", ks, sig, 1.0, integer=False, years=False)
    for a, b in (("1998", "2007"), ("2008", "2017"), ("2018", "2026")):
        e = eq_a["equity"].loc[a:b]
        hh = hold(ks, eq_a.index[0], 1.0, False).loc[a:b]
        print(f"  {a}~{b}: 추세 연환산 {stats(e)['연환산%']:+.1f}% / MDD {stats(e)['MDD%']:.1f}%  |  보유 {stats(hh)['연환산%']:+.1f}% / {stats(hh)['MDD%']:.1f}%")

    eq_b, trades = report(f"B. KODEX 200TR 실제 가격, 시작 {CAPITAL:,}원, 정수 주식", tr, sig, CAPITAL, integer=True)
    print("매매 기록:")
    for d, side, q, px in trades:
        print(f"  {d.date()} {side} {int(q)}주 @ {px:,.0f}원")
    cur = sig.dropna()
    print(f"\n현재 신호: {cur.index[-1]} 월말 기준 {'보유' if cur.iloc[-1] else '현금'} (최근 6개월: "
          + ", ".join(f"{p.strftime('%y-%m')} {'보유' if v else '현금'}" for p, v in cur.iloc[-6:].items()) + ")")


if __name__ == "__main__":
    sys.exit(main())
