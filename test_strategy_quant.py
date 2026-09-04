"""
적응형 퀀트 매매 전략 (AdaptiveVolatilityBreakoutStrategy & Fractional Kelly) 단위 테스트 스위트
"""
import unittest
import asyncio
from datetime import datetime
import pandas as pd
from strategy import AdaptiveVolatilityBreakoutStrategy
from async_portfolio import AsyncPortfolioManager
from market_data_buffer import CircularCandleBuffer, MarketDataBuffer


class TestAdaptiveQuantStrategy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.market_buffer = MarketDataBuffer(db_manager=None, buffer_maxlen=20)
        self.strategy = AdaptiveVolatilityBreakoutStrategy(
            buffer_manager=self.market_buffer,
            k_breakout=0.5,
            atr_hard_stop_mult=2.0,
            atr_trailing_stop_mult=2.5
        )
        self.portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5, kelly_fraction=0.4)

    async def test_fractional_kelly_sizing(self):
        """프랙셔널 켈리 자산 배분 산출 및 승률 연동 검증"""
        # 1. 초기 10회 미만 거래 시 기본 비중(20%) 유지
        alloc_initial = self.portfolio.get_kelly_allocation_fraction(min_trades=10)
        self.assertEqual(alloc_initial, 0.20)

        # 2. 높은 승률(70%) + 우수한 손익비(2.0) 기록 10건 추가
        # 수익 매매 7건 (+6%), 손실 매매 3건 (-3%)
        for _ in range(7):
            await self.portfolio.record_closed_trade(0.06)
        for _ in range(3):
            await self.portfolio.record_closed_trade(-0.03)

        # 켈리 공식: p=0.7, b=2.0 -> f* = (0.7*2 - 0.3)/2 = 1.1/2 = 0.55
        # 40% Half-Kelly: 0.55 * 0.4 = 0.22 (22%)
        alloc_kelly = self.portfolio.get_kelly_allocation_fraction(min_trades=10)
        self.assertGreater(alloc_kelly, 0.20)
        self.assertLessEqual(alloc_kelly, 0.25)

        # 주문 수량 계산 (1주당 50,000원 기준)
        qty = await self.portfolio.get_order_qty(current_price=50000, atr=1500)
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty * 50000, 10_000_000 * 0.25)

    async def test_atr_hard_stop_signal(self):
        """ATR 기반 하드 스탑로스(-2.0 ATR) 매도 시그널 검증"""
        code = "005930"
        buy_price = 70000.0
        atr14 = 2000.0
        ind = {'atr14': atr14, 'skip_time_filter': True}

        # 정상 가격 (손절선 미도달)
        action, _ = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=68000, ind=ind
        )
        self.assertEqual(action, "WAIT")

        # ATR 2배 초과 하락 (70,000 - 4,000 = 66,000 이하로 하락)
        action, reason = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=65500, ind=ind
        )
        self.assertEqual(action, "SELL_ALL")
        self.assertIn("ATR_하드스탑로스", reason)

    async def test_chandelier_trailing_stop_signal(self):
        """샹들리에 엑시트(최고가 대비 -2.5 ATR) 트레일링 스탑 검증"""
        code = "000660"
        buy_price = 100000.0
        atr14 = 3000.0
        highest_price = 115000.0  # +15% 최고가 달성
        ind = {'atr14': atr14, 'skip_time_filter': True}

        # 최고가(115,000) 대비 2.5 ATR(7,500원) 하락한 스탑가 = 107,500원
        # sell_stage=2 (2차 분할익절 완료 상태)
        # 현재가 108,000원 -> 대기 (WAIT)
        action, _ = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=108000, ind=ind,
            highest_price=highest_price, sell_stage=2
        )
        self.assertEqual(action, "WAIT")

        # 현재가 107,000원 (스탑가 하향 돌파) -> 전량 청산 (SELL_ALL)
        action, reason = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=107000, ind=ind,
            highest_price=highest_price, sell_stage=2
        )
        self.assertEqual(action, "SELL_ALL")
        self.assertIn("샹들리에_트레일링스탑", reason)

    async def test_atr_take_profit_stages(self):
        """ATR R-배수 다단계 분할 익절 검증"""
        code = "035420"
        buy_price = 200000.0
        atr14 = 4000.0
        ind = {'atr14': atr14, 'skip_time_filter': True}

        # 1차 목표가: 200,000 + 1.5 * 4,000 = 206,000원 (+3%)
        action, reason = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=206500, ind=ind, sell_stage=0
        )
        self.assertEqual(action, "SELL_PARTIAL")
        self.assertIn("1차_ATR_R1_분할익절", reason)

        # 2차 목표가: 200,000 + 2.5 * 4,000 = 210,000원 (+5%)
        action, reason = await self.strategy.check_sell_signal(
            code=code, buy_price=buy_price, current_price=210500, ind=ind, sell_stage=1
        )
        self.assertEqual(action, "SELL_PARTIAL")
        self.assertIn("2차_ATR_R2_분할익절", reason)


if __name__ == '__main__':
    unittest.main()
