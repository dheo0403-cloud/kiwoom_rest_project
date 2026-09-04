"""
고충실도 백테스터 (HighFidelityBacktester) 단위 및 WFO 테스트 스위트
"""
import unittest
import numpy as np
import pandas as pd
from backtest import HighFidelityBacktester


class TestHighFidelityBacktester(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        n = 100
        dates = pd.date_range(start="2025-01-01", periods=n, freq="B")
        returns = np.random.normal(0.002, 0.02, n)
        close_p = 50000.0 * np.cumprod(1 + returns)
        high_p = close_p * (1 + np.random.uniform(0.005, 0.03, n))
        low_p = close_p * (1 - np.random.uniform(0.005, 0.03, n))
        open_p = low_p + (high_p - low_p) * np.random.uniform(0.2, 0.8, n)
        vols = np.random.randint(10000, 500000, n)

        self.df = pd.DataFrame({
            'date': dates,
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': vols
        })
        self.backtester = HighFidelityBacktester(initial_capital=10_000_000, k_breakout=0.5)

    def test_backtest_execution_and_metrics(self):
        """백테스트 실행 및 현실적 마찰비용/성과 지표 검증"""
        metrics = self.backtester.run_backtest(self.df, code="005930")

        self.assertIn('initial_capital', metrics)
        self.assertIn('final_equity', metrics)
        self.assertIn('total_return_pct', metrics)
        self.assertIn('sharpe_ratio', metrics)
        self.assertIn('mdd_pct', metrics)
        self.assertIn('total_fee_tax_paid', metrics)

        # 수수료/세금 지출 확인
        if metrics['total_trades'] > 0:
            self.assertGreater(metrics['total_fee_tax_paid'], 0.0)

    def test_walk_forward_optimization(self):
        """Walk-Forward Optimization (WFO) 롤링 검증"""
        wfo_res = self.backtester.run_walk_forward_optimization(
            self.df, k_values=[0.4, 0.5, 0.6], in_sample_ratio=0.7
        )

        self.assertIn('best_k', wfo_res)
        self.assertIn(wfo_res['best_k'], [0.4, 0.5, 0.6])
        self.assertIn('out_of_sample_metrics', wfo_res)
        self.assertIn('total_return_pct', wfo_res['out_of_sample_metrics'])


if __name__ == '__main__':
    unittest.main()
