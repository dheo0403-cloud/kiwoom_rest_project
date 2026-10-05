"""collect_history 단위 테스트: 응답 파싱, 이어받기(cont-yn/next-key), 기간 자르기, 종목 정보 ETF 표시 (네트워크·DB 없이 가짜 객체 사용)"""
import asyncio
import unittest
from unittest import mock

import collect_history as ch


class FakeClient:
    base_url = "https://example.invalid"

    def __init__(self, pages):
        self.pages = pages      # [(data, headers), ...] 순서대로 반환
        self.calls = []

    async def request(self, api_id, url, payload, headers_override=None, **_):
        self.calls.append((api_id, dict(payload), headers_override))
        return self.pages[len(self.calls) - 1]


class FakeDB:
    def __init__(self):
        self.daily, self.minute, self.master = [], [], []

    async def upsert_daily_rows(self, rows): self.daily += rows
    async def upsert_minute_rows(self, rows): self.minute += rows
    async def upsert_stock_master(self, rows): self.master += rows
    async def ensure_stock_master(self): pass


def daily_item(dt, close):
    return {"dt": dt, "open_pric": "+100", "high_pric": "110", "low_pric": "-90", "cur_prc": str(close),
            "trde_qty": "1,000", "trde_prica": "5"}


class TestCollectHistory(unittest.TestCase):
    def test_parse_daily_strips_signs_and_commas(self):
        rows = ch.parse_daily("005930", [daily_item("20260904", "-101"), {"dt": ""}])
        self.assertEqual(rows, [("005930", "20260904", 100, 110, 90, 101, 1000, 5)])

    def test_daily_paging_follows_next_key_until_cutoff(self):
        """2페이지째에 기준일(1년 전)보다 오래된 날짜가 나오면 멈추고, 기준일 이전 행은 버림"""
        pages = [({"stk_dt_pole_chart_qry": [daily_item("20260904", 1), daily_item("20260101", 1)]}, {"cont-yn": "Y", "next-key": "K1"}),
                 ({"stk_dt_pole_chart_qry": [daily_item("20251201", 1), daily_item("20240101", 1)]}, {"cont-yn": "Y", "next-key": "K2"})]
        client, db = FakeClient(pages), FakeDB()
        with mock.patch.object(ch, "get_kst_now", return_value=__import__("datetime").datetime(2026, 9, 5)):
            n = asyncio.run(ch.collect_daily(client, db, "005930", years=1))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1][2], {"cont-yn": "Y", "next-key": "K1"})
        self.assertEqual(n, 3)
        self.assertNotIn("20240101", [r[1] for r in db.daily])

    def test_minute_keeps_recent_trade_days_only(self):
        items = [{"cntr_tm": t, "open_pric": "1", "high_pric": "1", "low_pric": "1", "cur_prc": "1", "trde_qty": "7"}
                 for t in ("20260904153000", "20260904090000", "20260903153000", "bad")]
        client, db = FakeClient([({"stk_min_pole_chart_qry": items}, {"cont-yn": "N"})]), FakeDB()
        with mock.patch.object(ch, "get_kst_now", return_value=__import__("datetime").datetime(2026, 9, 4, 16)):
            asyncio.run(ch.collect_minute(client, db, "005930", days=1))
        self.assertEqual({r[1][:8] for r in db.minute}, {"20260904"})
        self.assertTrue(all(len(r[1]) == 14 and r[6] == 7 for r in db.minute))

    def test_stock_master_marks_etf(self):
        lists = {"0": [{"code": "005930", "name": "삼성전자"}], "10": [{"code": "035720", "name": "카카오"}],
                 "8": [{"code": "069500", "name": "KODEX 200"}]}
        client, db = FakeClient([]), FakeDB()

        async def request(api_id, url, payload, headers_override=None, **_):
            return {"list": lists[payload["mrkt_tp"]]}, {"cont-yn": "N"}
        client.request = request
        asyncio.run(ch.refresh_stock_master(client, db))
        self.assertIn(("069500", "KODEX 200", "ETF", 1), db.master)
        self.assertIn(("005930", "삼성전자", "KOSPI", 0), db.master)

    def test_business_error_returns_no_rows(self):
        client, db = FakeClient([({"return_code": 1, "return_msg": "오류"}, {})]), FakeDB()
        self.assertEqual(asyncio.run(ch.collect_daily(client, db, "005930")), 0)


def minute_items(times):
    return [{"cntr_tm": t, "open_pric": "1", "high_pric": "1", "low_pric": "1", "cur_prc": "1", "trde_qty": "5"} for t in times]


class BackfillDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.progress = {}

    async def upsert_minute_backfill(self, code, oldest, rows, done, reason=None):
        self.progress[code] = (oldest, rows, done, reason)

    async def get_stock_universe(self):
        return ["000020", "005930"]


class TestMinuteBackfill(unittest.TestCase):
    NOW = __import__("datetime").datetime(2026, 10, 6, 17)

    def _step(self, pages, state=None, days=365):
        client, db = FakeClient(pages), BackfillDB()
        with mock.patch.object(ch, "get_kst_now", return_value=self.NOW):
            new = asyncio.run(ch.backfill_minute_step(client, db, "005930", state, days, pages=5))
        return client, db, new

    def test_resumes_from_oldest_and_marks_done_when_kiwoom_runs_out(self):
        pages = [({"stk_min_pole_chart_qry": minute_items(["20260410153000", "20260409090000"])}, {"cont-yn": "Y", "next-key": "K"}),
                 ({"stk_min_pole_chart_qry": []}, {"cont-yn": "N"})]
        client, db, new = self._step(pages, {"oldest_date": "20260410", "rows_saved": 100, "done": 0})
        self.assertEqual(client.calls[0][1]["base_dt"], "20260410")          # 진행표 날짜부터 이어받기
        self.assertEqual(new, {"oldest_date": "20260409", "rows_saved": 102, "done": 1})
        self.assertEqual(db.progress["005930"][3], "no_more")               # 키움 보관 한도 도달

    def test_stops_at_target_period(self):
        pages = [({"stk_min_pole_chart_qry": minute_items(["20261005150000", "20251001090000"])}, {"cont-yn": "Y", "next-key": "K"})]
        _, db, new = self._step(pages, days=30)
        self.assertEqual(new["done"], 1)
        self.assertEqual([r[1] for r in db.minute], ["20261005150000"])     # 목표 기간 밖 행은 저장 안 함

    def test_business_error_is_retried_not_done(self):
        _, db, new = self._step([({"return_code": 5, "return_msg": "한도 초과"}, {})], {"oldest_date": "20260410", "rows_saved": 0, "done": 0})
        self.assertEqual(new["done"], 0)
        self.assertEqual(new["oldest_date"], "20260410")

    def test_collect_after_close_uses_universe_when_all(self):
        client, db = FakeClient([]), BackfillDB()
        called = []

        async def fake_minute(client, db, code, days=1, base_dt=None):
            called.append(code)
            return 0

        async def zero(*a, **k):
            return 0
        with mock.patch.object(ch, "refresh_stock_master", zero), mock.patch.object(ch, "collect_daily", zero), \
                mock.patch.object(ch, "collect_minute", fake_minute):
            stats = asyncio.run(ch.collect_after_close(client, db, ["111110"], [], "all"))
        self.assertEqual(sorted(called), ["000020", "005930", "111110"])
        self.assertEqual(stats["minute_codes"], 3)

    def test_bot_backfill_window(self):
        from datetime import datetime
        from main_rest_async import AsyncTradingBot
        bot = AsyncTradingBot.__new__(AsyncTradingBot)
        bot.backfill_window = ((16, 10), (8, 0))
        self.assertTrue(bot._backfill_allowed(datetime(2026, 10, 6, 16, 30)))   # 화 장 마감 후
        self.assertTrue(bot._backfill_allowed(datetime(2026, 10, 7, 7, 59)))    # 수 개장 전
        self.assertFalse(bot._backfill_allowed(datetime(2026, 10, 7, 10, 0)))   # 수 장중
        self.assertTrue(bot._backfill_allowed(datetime(2026, 10, 10, 11, 0)))   # 토요일


if __name__ == "__main__":
    unittest.main()
