"""
고충실도 백테스터 (HighFidelityBacktester) 단위 및 WFO 테스트 스위트
"""
import unittest
import numpy as np
import pandas as pd
from backtest import HighFidelityBacktester
from indicators import TechnicalIndicators


def make_minute_bars(n_days: int = 2, bars_per_day: int = 60, seed: int = 42) -> pd.DataFrame:
    """09:00부터 장중 1분봉 합성 데이터 (단위 테스트 전용, 지표 계산이 봉당 수십 ms라 봉 수를 줄임)"""
    rng = np.random.default_rng(seed)
    times = []
    for d in pd.bdate_range(start="2025-01-06", periods=n_days):
        times.extend(pd.date_range(start=d + pd.Timedelta(hours=9), periods=bars_per_day, freq="min"))
    n = len(times)
    close_p = 50000.0 * np.cumprod(1 + rng.normal(0.0002, 0.002, n))
    open_p = np.concatenate([[close_p[0]], close_p[:-1]])
    high_p = np.maximum(open_p, close_p) * (1 + rng.uniform(0, 0.002, n))
    low_p = np.minimum(open_p, close_p) * (1 - rng.uniform(0, 0.002, n))
    return pd.DataFrame({'datetime': times, 'open': open_p, 'high': high_p, 'low': low_p,
                         'close': close_p, 'volume': rng.integers(10000, 500000, n)})


class StubStrategy:
    """지정 봉에서 매수/매도하고 전달받은 봉 시각을 기록하는 스텁 전략"""
    k_breakout = 0.5

    def __init__(self, buy_at, sell_at):
        self.buy_at, self.sell_at = buy_at, sell_at
        self.buy_times, self.sell_times = [], []

    async def check_buy_signal(self, code, current_price, current_volume, ind=None):
        self.buy_times.append(ind['now'])
        return ind['now'] == self.buy_at, "stub_buy"

    async def check_sell_signal(self, code, buy_price, current_price, ind=None, sell_stage=0, highest_price=None):
        self.sell_times.append(ind['now'])
        return ("SELL_ALL", "stub_sell") if ind['now'] == self.sell_at else ("WAIT", "")


class TestHighFidelityBacktester(unittest.TestCase):
    def setUp(self):
        self.df = make_minute_bars()
        self.backtester = HighFidelityBacktester(initial_capital=10_000_000, k_breakout=0.5)

    def test_backtest_execution_and_metrics(self):
        """실거래 전략으로 백테스트 실행 및 현실적 마찰비용/성과 지표 검증"""
        metrics = self.backtester.run_backtest(self.df, code="005930")

        for key in ('initial_capital', 'final_equity', 'total_return_pct', 'sharpe_ratio', 'mdd_pct', 'total_fee_tax_paid'):
            self.assertIn(key, metrics)
        if metrics['total_trades'] > 0:
            self.assertGreater(metrics['total_fee_tax_paid'], 0.0)

    def test_delegates_to_strategy_with_bar_time(self):
        """백테스트가 주입된 전략을 호출하고 봉 시각을 넘기며, 종가 진입·저가 청산 비용을 반영하는지 검증"""
        times = pd.to_datetime(self.df['datetime'])
        buy_i, sell_i = 20, 40
        stub = StubStrategy(buy_at=times[buy_i].to_pydatetime(), sell_at=times[sell_i].to_pydatetime())
        bt = HighFidelityBacktester(initial_capital=10_000_000, strategy=stub)
        metrics = bt.run_backtest(self.df)

        self.assertEqual(metrics['total_trades'], 1)
        trade = bt.closed_trades[0]
        self.assertAlmostEqual(trade.entry_price, self.df['close'][buy_i] * (1 + bt.slippage_rate))
        self.assertAlmostEqual(trade.exit_price, self.df['low'][sell_i] * (1 - bt.slippage_rate))
        self.assertEqual(stub.sell_times[0], times[buy_i + 1].to_pydatetime())  # 진입 다음 봉부터 청산 판정
        self.assertLess(trade.pnl, trade.realized)  # 매수 원가·수수료 차감 확인

    def test_daily_atr_source_uses_prior_days_only(self):
        """atr_source='daily'면 전일까지 일봉 ATR을 사용 (2일 미만이면 0, look-ahead 없음)"""
        df = make_minute_bars(n_days=3, bars_per_day=30)
        seen = {}

        class AtrSpy(StubStrategy):
            async def check_buy_signal(self, code, current_price, current_volume, ind=None):
                seen.setdefault(ind['now'].date(), ind['atr14'])
                return False, ""

        HighFidelityBacktester(strategy=AtrSpy(None, None), atr_source="daily").run_backtest(df)
        times = pd.to_datetime(df['datetime'])
        daily = df.groupby(times.dt.date).agg(open=('open', 'first'), high=('high', 'max'),
                                              low=('low', 'min'), close=('close', 'last'))
        days = list(daily.index)
        self.assertEqual(seen[days[0]], 0.0)
        self.assertEqual(seen[days[1]], 0.0)  # 전일 1일뿐 → 0
        expected = float(TechnicalIndicators.calculate_atr(daily.iloc[:2], period=14).iloc[-1])
        self.assertAlmostEqual(seen[days[2]], expected)
        with self.assertRaises(ValueError):
            HighFidelityBacktester(atr_source="weekly")

    def test_walk_forward_optimization(self):
        """Walk-Forward Optimization (WFO) 롤링 검증"""
        wfo_res = self.backtester.run_walk_forward_optimization(
            self.df, k_values=[0.4, 0.5, 0.6], in_sample_ratio=0.7
        )

        self.assertIn('best_k', wfo_res)
        self.assertIn(wfo_res['best_k'], [0.4, 0.5, 0.6])
        self.assertIn('out_of_sample_metrics', wfo_res)
        self.assertIn('total_return_pct', wfo_res['out_of_sample_metrics'])
        # 일 경계 분할: 2일 데이터 → 1일 학습 / 1일 검증
        self.assertEqual(wfo_res['folds'][0]['train'], ('2025-01-06', '2025-01-06'))
        self.assertEqual(wfo_res['folds'][0]['test'], ('2025-01-07', '2025-01-07'))

    def test_rolling_wfo_folds_trade_only_inside_test_window(self):
        """일 단위 롤링 폴드 구성, OOS 거래는 검증 구간 안에서만 발생, k 원복 검증"""
        df = make_minute_bars(n_days=4, bars_per_day=30)
        times = pd.to_datetime(df['datetime'])
        # 매일 20번째 봉 매수, 25번째 봉 매도하는 스텁
        buy_set = {t.to_pydatetime() for t in times[19::30]}
        sell_set = {t.to_pydatetime() for t in times[24::30]}

        class DailyStub(StubStrategy):
            async def check_buy_signal(self, code, current_price, current_volume, ind=None):
                return ind['now'] in buy_set, "stub_buy"

            async def check_sell_signal(self, code, buy_price, current_price, ind=None, sell_stage=0, highest_price=None):
                return ("SELL_ALL", "stub_sell") if ind['now'] in sell_set else ("WAIT", "")

        bt = HighFidelityBacktester(strategy=DailyStub(None, None))
        res = bt.run_walk_forward_optimization(df, k_values=[0.4, 0.6], train_days=2, test_days=1)

        self.assertEqual([f['test'] for f in res['folds']],
                         [('2025-01-08', '2025-01-08'), ('2025-01-09', '2025-01-09')])
        for f in res['folds']:
            self.assertEqual(f['out_of_sample_metrics']['total_trades'], 1)  # 검증일 하루치 1건만
        self.assertEqual(res['out_of_sample_metrics']['total_trades'], 2)
        self.assertEqual(res['out_of_sample_metrics']['n_folds'], 2)
        self.assertEqual(bt.k_breakout, 0.5)  # 최적화 후 원래 k 복원


if __name__ == '__main__':
    unittest.main()


class TestFilterAblation(unittest.TestCase):
    def test_disabled_filter_changes_only_that_rule(self):
        """disabled_filters로 끈 필터만 건너뛰고, 알 수 없는 이름은 거부"""
        import asyncio
        from datetime import datetime
        from strategy import AdaptiveVolatilityBreakoutStrategy
        ind = {'now': datetime(2026, 9, 1, 10, 0), 'adx': 25.0, 'vwap': 10000.0, 'rsi14': 85.0, 'open': 9900.0, 'atr14': 50.0}
        base = asyncio.run(AdaptiveVolatilityBreakoutStrategy().check_buy_signal("A", 10000.0, 0, dict(ind)))
        no_rsi = asyncio.run(AdaptiveVolatilityBreakoutStrategy(disabled_filters={"rsi"}).check_buy_signal("A", 10000.0, 0, dict(ind)))
        self.assertTrue(base[1].startswith("RSI_"))
        self.assertFalse(no_rsi[1].startswith("RSI_"))
        with self.assertRaises(ValueError):
            AdaptiveVolatilityBreakoutStrategy(disabled_filters={"nope"})

    def test_wfo_reuses_prepared_data(self):
        """prepare() 결과를 넘기면 df 없이도 같은 결과"""
        df = make_minute_bars(n_days=3, bars_per_day=30)
        bt = HighFidelityBacktester(atr_source="daily")
        data = bt.prepare(df)
        a = bt.run_walk_forward_optimization(df, k_values=[0.5], train_days=1, test_days=1)
        b = bt.run_walk_forward_optimization(None, k_values=[0.5], train_days=1, test_days=1, data=data)
        self.assertEqual(a['out_of_sample_metrics'], b['out_of_sample_metrics'])


class TestDailyBacktest(unittest.TestCase):
    """일봉 근사 백테스트: 평탄한 25일(ATR=200, 돌파선=시가+100) 뒤 마지막 날 한 봉으로 진입·청산 규칙 확인"""

    @staticmethod
    def _run(last):
        from daily_backtest import simulate_daily
        rows = [dict(date=f"2026{i:04d}", open=10000, high=10100, low=9900, close=10000, volume=1000) for i in range(101, 126)]
        rows.append(dict(date="20260201", volume=1000, **last))
        bt = HighFidelityBacktester()
        cost = lambda e, x: x * (1 - bt.slippage_rate) * (1 - bt.sell_fee_rate - bt.sell_tax_rate) / (e * (1 + bt.slippage_rate) * (1 + bt.buy_fee_rate)) - 1
        return simulate_daily(pd.DataFrame(rows), bt).iloc[-1], cost

    def test_close_exit(self):
        t, cost = self._run(dict(open=10000, high=10200, low=10050, close=10150))
        self.assertFalse(t['stopped'])
        self.assertAlmostEqual(t['ret'], cost(10100, 10150))

    def test_high_first_then_stop(self):
        """시가가 고가 쪽에 가까우면 고가→저가 순서: 진입(10100) 뒤 저가가 손절가(10100 − 1.5×200 = 9800)에 닿아 손절"""
        t, cost = self._run(dict(open=10000, high=10200, low=9700, close=10150))
        self.assertTrue(t['stopped'])
        self.assertAlmostEqual(t['ret'], cost(10100, 9800))

    def test_low_first_no_stop(self):
        """시가가 저가 쪽에 가까우면 저가는 진입 전 → 손절 없이 종가 청산"""
        t, cost = self._run(dict(open=10000, high=10400, low=9750, close=10200))
        self.assertFalse(t['stopped'])
        self.assertAlmostEqual(t['ret'], cost(10100, 10200))

    def test_gap_up_uses_today_open(self):
        """돌파선은 당일 시가 기준: 시가 10300이면 돌파선 10400에 진입"""
        t, cost = self._run(dict(open=10300, high=10400, low=10250, close=10350))
        self.assertAlmostEqual(t['ret'], cost(10400, 10350))


class TestDailyContextAndSpo(unittest.TestCase):
    """SPO 미래 누출 없음, 시장 국면·거래대금 상위는 전일 기준, SPO 반등 진입·청산 규칙"""

    def test_spo_no_lookahead(self):
        close = pd.Series(10000 + np.cumsum(np.random.default_rng(0).normal(0, 50, 300)))
        changed = close.copy()
        changed.iloc[250:] += 3000
        a = TechnicalIndicators.calculate_spo(close).iloc[:250]
        b = TechnicalIndicators.calculate_spo(changed).iloc[:250]
        self.assertTrue(a.notna().any())
        pd.testing.assert_series_equal(a, b)

    def test_market_context_uses_previous_day(self):
        import daily_backtest as db
        dates = [f"2026{i:04d}" for i in range(101, 126)]
        proxy = [100] * 22 + [200, 200, 200]  # 23번째 날(인덱스 22) 처음 MA20 위
        other_value = [50] * 25
        other_value[22] = 1000                # 인덱스 22에 거래대금 급증 → 다음 날 상위
        rows = [dict(code=db.MARKET_PROXY, date=d, close=p, value=100) for d, p in zip(dates, proxy)]
        rows += [dict(code="000001", date=d, close=100, value=v) for d, v in zip(dates, other_value)]
        orig = db.UNIVERSE_TOP_N
        db.UNIVERSE_TOP_N = 1
        try:
            ctx = db.market_context(pd.DataFrame(rows)).set_index(['code', 'date'])
        finally:
            db.UNIVERSE_TOP_N = orig
        self.assertFalse(ctx.loc[("000001", dates[22]), 'kodex_up'])
        self.assertTrue(ctx.loc[("000001", dates[23]), 'kodex_up'])
        self.assertFalse(ctx.loc[("000001", dates[22]), 'top_value'])
        self.assertTrue(ctx.loc[("000001", dates[23]), 'top_value'])

    def _reversion(self, bars):
        from unittest import mock
        import daily_backtest as db
        spo = [0.5] * 30
        spo[19], spo[20] = -2.0, -1.5          # 인덱스 20: −1 아래에서 상승 전환 → 21일 시가 진입
        spo[21] = spo[22] = -0.5
        spo[23] = 0.5                          # 23일 SPO > 0 → 종가 청산
        rows = [dict(date=f"2026{i:04d}", open=10000, high=10100, low=9900, close=10000, volume=1000) for i in range(101, 131)]
        for idx, bar in bars.items():
            rows[idx].update(bar)
        bt = HighFidelityBacktester()
        with mock.patch.object(db.TechnicalIndicators, 'calculate_spo', return_value=pd.Series(spo)):
            trades = db.simulate_spo_reversion(pd.DataFrame(rows), bt)
        cost = lambda e, x: x * (1 - bt.slippage_rate) * (1 - bt.sell_fee_rate - bt.sell_tax_rate) / (e * (1 + bt.slippage_rate) * (1 + bt.buy_fee_rate)) - 1
        return trades, cost

    def test_spo_reversion_exit_on_zero_cross(self):
        trades, cost = self._reversion({23: dict(close=10050)})
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]['days'], 3)
        self.assertAlmostEqual(trades.iloc[0]['ret'], cost(10000, 10050))

    def test_spo_reversion_gap_down_stop(self):
        """손절가 9700(= max(10000 − 1.5×200, 10000×0.97)), 다음 날 시가 9650 갭하락 → 시가 청산"""
        trades, cost = self._reversion({22: dict(open=9650, high=9700, low=9600, close=9650)})
        self.assertTrue(trades.iloc[0]['stopped'])
        self.assertAlmostEqual(trades.iloc[0]['ret'], cost(10000, 9650))
