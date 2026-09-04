"""
인메모리 링버퍼 (CircularCandleBuffer & MarketDataBuffer) 단위 테스트 스위트
"""
import unittest
import asyncio
import time
from datetime import datetime, timedelta
import pandas as pd
from market_data_buffer import CircularCandleBuffer, MarketDataBuffer


class MockDatabase:
    """테스트용 Mock Database"""
    def __init__(self):
        self.inserted_candles = []

    async def batch_upsert_minute_candles(self, candle_list):
        self.inserted_candles.extend(candle_list)


class TestCircularCandleBuffer(unittest.TestCase):
    def setUp(self):
        self.buffer = CircularCandleBuffer(code="005930", maxlen=10)

    def test_single_candle_append_and_df(self):
        """단일 캔들 추가 및 DataFrame 변환 검증"""
        self.buffer.append_candle(70000, 71000, 69500, 70500, 10000, "2026-09-04 09:00:00")
        self.buffer.append_candle(70500, 71500, 70200, 71200, 15000, "2026-09-04 09:01:00")

        df = self.buffer.get_dataframe()
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[-1]['close'], 71200)
        self.assertEqual(self.buffer.get_latest_price(), 71200)

    def test_ring_buffer_maxlen_overflow(self):
        """링버퍼 최대 용량(maxlen) 초과 시 자동 오버플로우 검증"""
        for i in range(15):
            self.buffer.append_candle(
                70000 + i, 70100 + i, 69900 + i, 70050 + i, 1000, f"2026-09-04 09:{i:02d}:00"
            )

        df = self.buffer.get_dataframe()
        self.assertEqual(len(df), 10)  # maxlen=10 제한 유지
        self.assertEqual(df.iloc[-1]['close'], 70050 + 14)

    def test_tick_update_and_minute_assembly(self):
        """틱 수신 시 분봉 롤링 조립 검증"""
        # 1분차 틱 3개
        self.buffer.update_tick(70000, 100, "2026-09-04 09:00:00")
        self.buffer.update_tick(70500, 200, "2026-09-04 09:00:00")
        self.buffer.update_tick(69800, 300, "2026-09-04 09:00:00")

        # 2분차 틱 유입 -> 1분차 분봉 완성 반환
        completed = self.buffer.update_tick(70200, 400, "2026-09-04 09:01:00")
        self.assertIsNotNone(completed)
        self.assertEqual(completed['open'], 70000)
        self.assertEqual(completed['high'], 70500)
        self.assertEqual(completed['low'], 69800)
        self.assertEqual(completed['close'], 69800)
        self.assertEqual(completed['volume'], 300)

    def test_highest_lowest_calculations(self):
        """최고가 및 최저가 초고속 산출 검증"""
        prices = [100, 150, 80, 200, 120]
        for idx, p in enumerate(prices):
            self.buffer.append_candle(p, p + 10, p - 10, p, 100, f"2026-09-04 09:0{idx}:00")

        self.assertEqual(self.buffer.get_highest_high(window=5), 210)  # 200 + 10
        self.assertEqual(self.buffer.get_lowest_low(window=5), 70)    # 80 - 10


class TestMarketDataBufferAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_db = MockDatabase()
        self.market_buffer = MarketDataBuffer(db_manager=self.mock_db, flush_interval=0.1, buffer_maxlen=10)
        await self.market_buffer.start()

    async def asyncTearDown(self):
        await self.market_buffer.stop()

    async def test_multi_stock_ticks_and_db_flush(self):
        """다중 종목 틱 유입 및 백그라운드 DB 배치 저장 검증"""
        # 삼성전자 틱 유입
        self.market_buffer.update_tick("005930", 70000, 100, "2026-09-04 09:00:00")
        self.market_buffer.update_tick("005930", 70500, 200, "2026-09-04 09:01:00")  # 09:00 완성

        # SK하이닉스 틱 유입
        self.market_buffer.update_tick("000660", 150000, 50, "2026-09-04 09:00:00")
        self.market_buffer.update_tick("000660", 151000, 80, "2026-09-04 09:01:00")  # 09:00 완성

        # 백그라운드 워커 동작 대기 (0.15초)
        await asyncio.sleep(0.2)

        self.assertGreaterEqual(len(self.mock_db.inserted_candles), 2)
        saved_codes = [c['code'] for c in self.mock_db.inserted_candles]
        self.assertIn("005930", saved_codes)
        self.assertIn("000660", saved_codes)

    async def test_graceful_shutdown_flush_all(self):
        """종료 시 미완성 캔들까지 무손실 DB 저장(Flush) 검증"""
        self.market_buffer.update_tick("035420", 200000, 10, "2026-09-04 09:00:00")
        await self.market_buffer.stop()

        # stop() 호출 시 현재 진행 중인 미완성 캔들도 flush_all로 저장되어야 함
        saved_codes = [c['code'] for c in self.mock_db.inserted_candles]
        self.assertIn("035420", saved_codes)


if __name__ == '__main__':
    unittest.main()
