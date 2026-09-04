"""
카카오톡 알림 엔진 (KakaoNotifier) 단위 테스트 스위트
"""
import unittest
import asyncio
from notifier import KakaoNotifier


class TestKakaoNotifier(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.notifier = KakaoNotifier(rest_api_key="test_key", access_token="test_token", refresh_token="test_refresh")
        await self.notifier.start()

    async def asyncTearDown(self):
        await self.notifier.stop()

    async def test_order_filled_notification_formatting(self):
        """체결 알림 메시지 큐 적재 및 포맷팅 검증"""
        self.notifier.notify_order_filled(
            side="BUY", name="삼성전자", code="005930", qty=10, price=75000.0,
            reason="ATR_변동성돌파"
        )
        self.assertEqual(self.notifier.queue.qsize(), 1)
        item = await self.notifier.queue.get()
        self.assertIn("삼성전자(005930)", item["text"])
        self.assertIn("75,000원", item["text"])
        self.assertIn("ATR_변동성돌파", item["text"])

    async def test_circuit_breaker_notification_formatting(self):
        """서킷 브레이커 경보 메시지 검증"""
        self.notifier.notify_circuit_breaker(mdd=-5.5, total_asset=9500000)
        self.assertEqual(self.notifier.queue.qsize(), 1)
        item = await self.notifier.queue.get()
        self.assertIn("계좌 서킷 브레이커 발동", item["text"])
        self.assertIn("-5.50%", item["text"])

    async def test_daily_settlement_formatting(self):
        """일일 정산 리포트 메시지 검증"""
        snapshot = {
            'initial_capital': 10000000,
            'total_asset': 10500000,
            'total_pnl': 500000,
            'total_yield': 5.0,
            'positions_count': 2,
            'kelly_allocation_pct': 22.5
        }
        self.notifier.notify_daily_settlement(snapshot)
        self.assertEqual(self.notifier.queue.qsize(), 1)
        item = await self.notifier.queue.get()
        self.assertIn("일일 장 마감 퀀트 정산 리포트", item["text"])
        self.assertIn("10,500,000원", item["text"])
        self.assertIn("+500,000원", item["text"])


if __name__ == '__main__':
    unittest.main()
