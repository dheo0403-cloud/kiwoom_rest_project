"""일봉 근사 백테스트 (실험 전용, 조회만): 장중 변동성 돌파 진입 가설을 daily_ohlcv 전 기간·전 종목으로 검증한다.

근사 규칙 (분봉 백테스트와 같은 전략 파라미터·비용 사용):
- 전일까지의 일봉 ATR14 → 당일 돌파선 = 시가 + k × ATR. 고가가 돌파선에 닿으면 돌파선 가격에 매수(돌파선은 항상 시가 이상).
- 손절가 = max(진입가 − ATR×하드스탑배수, 진입가 × (1 + 하드스탑률)).
- 봉 내부 순서는 표준 OHLC 경로 가정: 시가가 저가 쪽에 가까우면 시가→저가→고가→종가(저가는 진입 전, 진입 후 최저 = 종가),
  아니면 시가→고가→저가→종가(진입 후 최저 = 저가). 진입 후 최저가가 손절가 이하면 손절가에 청산.
- 손절이 없으면 당일 종가 청산(데이트레이딩). 트레일링 익절은 일봉으로 재현할 수 없어 제외.
- 가설 그룹: 전일 종가 > MA20 이고 전일 종가 >= 최근 20일 고점 × 0.95.
"""
import pandas as pd

from backtest import HighFidelityBacktester
from indicators import TechnicalIndicators
from strategy import MIN_STOCK_PRICE

MAX_DAILY_MOVE = 0.30  # KRX 가격제한폭 초과 변동 = 액면분할 등 수정주가 미반영 의심 → 그날 제외
NEAR_HIGH_RATIO = 0.95


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
    return t[['date', 'ret', 'stopped', 'hypothesis']]


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
            sql = "SELECT code, date, open, high, low, close, volume FROM daily_ohlcv"
            if codes:
                sql += " WHERE code IN (" + ",".join(["%s"] * len(codes)) + ")"
            cur.execute(sql + " ORDER BY code, date", tuple(codes or ()))
            rows = cur.fetchall()
    finally:
        con.close()
    return pd.DataFrame(rows, columns=['code', 'date', 'open', 'high', 'low', 'close', 'volume'])


def summarize(trades: pd.DataFrame) -> dict:
    r = trades['ret']
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    return {'거래': len(r), '승률%': round((r > 0).mean() * 100, 1) if len(r) else 0.0,
            '기대값%': round(r.mean() * 100, 3) if len(r) else 0.0,
            'PF': round(gain / loss, 2) if loss else float('nan'),
            '손절%': round(trades['stopped'].mean() * 100, 1) if len(r) else 0.0}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="일봉 근사 돌파 백테스트 — 기준(A) / 가설(B) / 반대(C) 비교")
    parser.add_argument("--codes", nargs="*", help="대상 종목 (생략 시 전체)")
    parser.add_argument("--start", help="시작일 YYYYMMDD (지표 계산은 전체 기간, 집계만 제한)")
    parser.add_argument("--end", help="종료일 YYYYMMDD")
    args = parser.parse_args()

    raw = load_daily_from_db(args.codes)
    bt = HighFidelityBacktester()
    all_trades = pd.concat([simulate_daily(g, bt).assign(code=c) for c, g in raw.groupby('code')], ignore_index=True)
    all_trades['hypothesis'] = all_trades['hypothesis'].astype(bool)  # 빈 결과와 합쳐지며 object로 바뀌는 것 방지
    if args.start:
        all_trades = all_trades[all_trades['date'] >= args.start]
    if args.end:
        all_trades = all_trades[all_trades['date'] <= args.end]
    print(f"[데이터] {raw['code'].nunique()}종목, {raw['date'].min()} ~ {raw['date'].max()}, 거래 {len(all_trades):,}건")
    groups = {'A 기준': all_trades, 'B 가설': all_trades[all_trades['hypothesis']],
              'C 반대': all_trades[~all_trades['hypothesis']]}
    rows = []
    for name, g in groups.items():
        rows.append({'그룹': name, '기간': '전체', **summarize(g)})
        for year, gy in g.groupby(g['date'].str[:4]):
            rows.append({'그룹': name, '기간': year, **summarize(gy)})
    print(pd.DataFrame(rows).to_string(index=False))
