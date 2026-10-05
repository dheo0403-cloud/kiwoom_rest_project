"""일봉 근사 백테스트 (실험 전용, 조회만): 장중 변동성 돌파 진입 가설을 daily_ohlcv 전 기간·전 종목으로 검증한다.

근사 규칙 (분봉 백테스트와 같은 전략 파라미터·비용 사용):
- 전일까지의 일봉 ATR14 → 당일 돌파선 = 시가 + k × ATR. 고가가 돌파선에 닿으면 돌파선 가격에 매수(돌파선은 항상 시가 이상).
- 손절가 = max(진입가 − ATR×하드스탑배수, 진입가 × (1 + 하드스탑률)).
- 봉 내부 순서는 표준 OHLC 경로 가정: 시가가 저가 쪽에 가까우면 시가→저가→고가→종가(저가는 진입 전, 진입 후 최저 = 종가),
  아니면 시가→고가→저가→종가(진입 후 최저 = 저가). 진입 후 최저가가 손절가 이하면 손절가에 청산.
- 손절이 없으면 당일 종가 청산(데이트레이딩). 트레일링 익절은 일봉으로 재현할 수 없어 제외.
- 가설 그룹: 전일 종가 > MA20 이고 전일 종가 >= 최근 20일 고점 × 0.95.
- 시장 국면(전일 기준): KODEX200 종가 > MA20, 전 종목 중 MA20 위 비율 > 50%. 종목 선정: 전일 거래대금 상위 N(실거래 WATCHLIST_SIZE).
- SPO 반등(별도 진입 가설): SPO가 −1 아래에서 상승 전환한 다음 날 시가 매수, 같은 손절, SPO > 0 또는 보유 N일째 종가 청산.
"""
import os

import pandas as pd

from backtest import HighFidelityBacktester
from indicators import TechnicalIndicators
from strategy import MIN_STOCK_PRICE

MAX_DAILY_MOVE = 0.30  # KRX 가격제한폭 초과 변동 = 액면분할 등 수정주가 미반영 의심 → 그날 제외
NEAR_HIGH_RATIO = 0.95
MARKET_PROXY = "069500"  # KODEX 200 — 지수 테이블이 없어 ETF 일봉으로 시장 대용
UNIVERSE_TOP_N = int(os.getenv("WATCHLIST_SIZE", "30"))  # 실거래 관심종목 수와 동일
SPO_THRESHOLD = 1.0  # SPO 원문 과매도 기준
SPO_HOLD_DAYS = 5


def simulate_daily(df: pd.DataFrame, bt: HighFidelityBacktester = None) -> pd.DataFrame:
    """한 종목 일봉(date, open, high, low, close, volume 오름차순) → 돌파 거래 목록 (가설 여부 컬럼 포함)"""
    bt = bt or HighFidelityBacktester()
    s = bt.strategy
    d = df[df['volume'] > 0].reset_index(drop=True)
    if len(d) < 21:
        return pd.DataFrame()
    prev_close = d['close'].shift(1)
    atr = TechnicalIndicators.calculate_atr(d, period=14).shift(1)  # 전일까지
    ma20 = d['close'].rolling(20).mean().shift(1)
    high20 = d['high'].rolling(20).max().shift(1)
    level = d['open'] + s.k_breakout * atr
    jump = (d['close'] / prev_close - 1).abs() > MAX_DAILY_MOVE
    hit = ma20.notna() & (atr > 0) & (d['high'] >= level) & ~jump & (d['open'] >= MIN_STOCK_PRICE)  # 동전주 제외(전략과 동일)

    t = d[hit].copy()
    raw_entry = level[hit]
    stop = (raw_entry - s.atr_hard_stop_mult * atr[hit]).combine(raw_entry * (1 + s.hard_stop_loss_rate), max)
    low_first = (t['open'] - t['low']) < (t['high'] - t['open'])
    after_entry_low = t['close'].where(low_first, t['low'])
    stopped = after_entry_low <= stop
    raw_exit = stop.where(stopped, t['close'])
    buy = raw_entry * (1 + bt.slippage_rate) * (1 + bt.buy_fee_rate)
    sell = raw_exit * (1 - bt.slippage_rate) * (1 - bt.sell_fee_rate - bt.sell_tax_rate)
    t['ret'] = sell / buy - 1
    t['stopped'] = stopped
    t['hypothesis'] = (prev_close[hit] > ma20[hit]) & (prev_close[hit] >= high20[hit] * NEAR_HIGH_RATIO)
    spo = TechnicalIndicators.calculate_spo(d['close'])
    t['spo_up'] = (spo.shift(1) > spo.shift(2))[hit]  # 전일까지 SPO 상승 중
    t['atr_pct'] = (atr / prev_close)[hit]
    t['price'] = prev_close[hit]
    t['ret20'] = (prev_close / d['close'].shift(21) - 1)[hit]  # 직전 20일 수익률
    return t[['date', 'ret', 'stopped', 'hypothesis', 'spo_up', 'atr_pct', 'price', 'ret20']]


def simulate_spo_reversion(df: pd.DataFrame, bt: HighFidelityBacktester = None, hold_days: int = SPO_HOLD_DAYS) -> pd.DataFrame:
    """한 종목 일봉 → SPO 과매도 반등 거래 목록 (겹치지 않게 순차 진입, 보유 기간이 데이터 끝을 넘는 거래는 제외)"""
    bt = bt or HighFidelityBacktester()
    s = bt.strategy
    d = df[df['volume'] > 0].reset_index(drop=True)
    spo = TechnicalIndicators.calculate_spo(d['close']).to_numpy()
    atr = TechnicalIndicators.calculate_atr(d, period=14).to_numpy()
    signal = (spo < -SPO_THRESHOLD) & (spo > pd.Series(spo).shift(1).to_numpy())
    jump = ((d['close'] / d['close'].shift(1) - 1).abs() > MAX_DAILY_MOVE).to_numpy()
    o, lo, c, dates = d['open'].to_numpy(), d['low'].to_numpy(), d['close'].to_numpy(), d['date'].to_numpy()
    trades, i, n = [], 0, len(d)
    while i < n - hold_days:
        e, last = i + 1, i + hold_days
        if not signal[i] or not atr[i] > 0 or o[e] < MIN_STOCK_PRICE or jump[e:last + 1].any():
            i += 1
            continue
        entry = o[e]
        stop = max(entry - s.atr_hard_stop_mult * atr[i], entry * (1 + s.hard_stop_loss_rate))
        for j in range(e, last + 1):
            if lo[j] <= stop:  # 보유 중 갭하락이면 시가에 청산
                exit_price, stopped = (stop if j == e else min(o[j], stop)), True
                break
            if spo[j] > 0 or j == last:
                exit_price, stopped = c[j], False
                break
        buy = entry * (1 + bt.slippage_rate) * (1 + bt.buy_fee_rate)
        sell = exit_price * (1 - bt.slippage_rate) * (1 - bt.sell_fee_rate - bt.sell_tax_rate)
        trades.append({'date': dates[e], 'ret': sell / buy - 1, 'stopped': stopped, 'days': j - e + 1})
        i = j
    return pd.DataFrame(trades, columns=['date', 'ret', 'stopped', 'days'])


def market_context(raw: pd.DataFrame) -> pd.DataFrame:
    """(code, date)별 전일 기준 시장 국면·거래대금 상위 여부 (raw: code, date, close, value)"""
    r = raw.sort_values(['code', 'date']).copy()
    ma20 = r.groupby('code')['close'].transform(lambda x: x.rolling(20).mean())
    r['above'] = r['close'] > ma20
    valid = r[ma20.notna()]
    breadth = valid.groupby('date')['above'].mean() > 0.5
    proxy = valid[valid['code'] == MARKET_PROXY].set_index('date')['above']
    dates = sorted(r['date'].unique())
    prev = r['date'].map(dict(zip(dates[1:], dates[:-1])))  # 시장 거래일 기준 전일
    r['kodex_up'] = prev.map(proxy).astype('boolean')
    r['breadth_up'] = prev.map(breadth).astype('boolean')
    r['value_prev'] = r.groupby('code')['value'].shift(1)
    r['top_value'] = r.groupby('date')['value_prev'].rank(ascending=False, method='first') <= UNIVERSE_TOP_N
    return r[['code', 'date', 'kodex_up', 'breadth_up', 'top_value']]


def load_daily_from_db(codes=None) -> pd.DataFrame:
    """DB daily_ohlcv 조회 (조회 전용)"""
    import os
    import pymysql
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    con = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")),
                          user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
                          database=os.getenv("DB_NAME"), connect_timeout=10)
    try:
        with con.cursor() as cur:
            sql = "SELECT code, date, open, high, low, close, volume, value FROM daily_ohlcv"
            if codes:
                sql += " WHERE code IN (" + ",".join(["%s"] * len(codes)) + ")"
            cur.execute(sql + " ORDER BY code, date", tuple(codes or ()))
            rows = cur.fetchall()
    finally:
        con.close()
    return pd.DataFrame(rows, columns=['code', 'date', 'open', 'high', 'low', 'close', 'volume', 'value'])


def summarize(trades: pd.DataFrame) -> dict:
    r = trades['ret']
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    return {'거래': len(r), '승률%': round((r > 0).mean() * 100, 1) if len(r) else 0.0,
            '기대값%': round(r.mean() * 100, 3) if len(r) else 0.0,
            'PF': round(gain / loss, 2) if loss else float('nan'),
            '손절%': round(trades['stopped'].mean() * 100, 1) if len(r) else 0.0}


def print_groups(groups: dict, start=None, end=None):
    rows = []
    for name, g in groups.items():
        g = g[(g['date'] >= (start or '')) & (g['date'] <= (end or '99999999'))]
        rows.append({'그룹': name, '기간': '전체', **summarize(g)})
        for year, gy in g.groupby(g['date'].str[:4]):
            rows.append({'그룹': name, '기간': year, **summarize(gy)})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="일봉 근사 백테스트 — 돌파 가설 / 시장 국면 / 종목 선정 / SPO 비교")
    parser.add_argument("--codes", nargs="*", help="대상 종목 (생략 시 전체)")
    parser.add_argument("--start", help="시작일 YYYYMMDD (지표 계산은 전체 기간, 집계만 제한)")
    parser.add_argument("--end", help="종료일 YYYYMMDD")
    parser.add_argument("--report", choices=["trend", "regime", "universe", "spo"], default="trend")
    args = parser.parse_args()

    raw = load_daily_from_db(args.codes)
    bt = HighFidelityBacktester()
    t = pd.concat([simulate_daily(g, bt).assign(code=c) for c, g in raw.groupby('code')], ignore_index=True)
    t['hypothesis'] = t['hypothesis'].astype(bool)  # 빈 결과와 합쳐지며 object로 바뀌는 것 방지
    t = t.merge(market_context(raw), on=['code', 'date'], how='left')
    print(f"[데이터] {raw['code'].nunique()}종목, {raw['date'].min()} ~ {raw['date'].max()}, 돌파 거래 {len(t):,}건")

    if args.report == "trend":
        groups = {'A 기준': t, 'B 가설': t[t['hypothesis']], 'C 반대': t[~t['hypothesis']]}
    elif args.report == "regime":
        k, b = t['kodex_up'].fillna(False).astype(bool), t['breadth_up'].fillna(False).astype(bool)
        groups = {'A 기준': t, 'KODEX200>MA20': t[k], 'KODEX200<=MA20': t[~k & t['kodex_up'].notna()],
                  '시장폭>50%': t[b], '시장폭<=50%': t[~b & t['breadth_up'].notna()], '둘 다 상승': t[k & b]}
    elif args.report == "universe":
        top = t['top_value'].fillna(False).astype(bool)
        groups = {f'거래대금 상위{UNIVERSE_TOP_N}': t[top], '나머지': t[~top]}
        feats = pd.DataFrame({name: {'ATR%(중앙값)': g['atr_pct'].median() * 100, '가격(중앙값)': g['price'].median(),
                                     '직전20일 수익률%(중앙값)': g['ret20'].median() * 100}
                              for name, g in groups.items()}).round(2)
        print(feats.to_string())
    else:
        rev = pd.concat([simulate_spo_reversion(g, bt) for _, g in raw.groupby('code')], ignore_index=True)
        print(f"[SPO 반등] 평균 보유 {rev['days'].mean():.1f}일")
        spo_up = t['spo_up'].fillna(False).astype(bool)
        groups = {'A 돌파 기준': t, '돌파+SPO상승': t[spo_up], '돌파+SPO하락': t[~spo_up], 'SPO 과매도 반등': rev}
    print_groups(groups, args.start, args.end)
