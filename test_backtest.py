"""
고충실도 백테스터 (HighFidelityBacktester) 단위 및 WFO 테스트 스위트
"""
import unittest
import numpy as np
import pandas as pd
from backtest import HighFidelityBacktester


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
