"""미국 시장 야간 데이터: 차트 파싱(미확정 당일 봉 제외), 요약, 국내일 매핑(엄격히 이전 날짜), 매크로 입력 on/off·신선도"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import us_market


def chart(tz, items):
    ts = [int(datetime(*d, tzinfo=ZoneInfo(tz)).timestamp()) for d, _ in items]
    return {"chart": {"result": [{"meta": {"exchangeTimezoneName": tz}, "timestamp": ts,
                                  "indicators": {"quote": [{"close": [c for _, c in items]}]}}]}}


class TestUsMarket(unittest.TestCase):
    def test_parse_skips_unsettled_today_and_nulls(self):
        p = chart("America/New_York", [((2026, 10, 1, 9, 30), 100.0), ((2026, 10, 2, 9, 30), None), ((2026, 10, 5, 9, 30), 105.0)])
        now = datetime(2026, 10, 5, 14, 0, tzinfo=ZoneInfo("America/New_York"))   # 장중 → 10/5 봉 미확정
        self.assertEqual(us_market.parse_chart("^GSPC", p, now), [("^GSPC", "20261001", 100.0)])
        after = datetime(2026, 10, 5, 18, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(len(us_market.parse_chart("^GSPC", p, after)), 2)

    def test_parse_handles_empty(self):
        self.assertEqual(us_market.parse_chart("^VIX", {"chart": {"result": None}}), [])

    def test_summarize_change(self):
        s = us_market.summarize([{"symbol": "^IXIC", "date": "20261001", "close": 100.0},
                                 {"symbol": "^IXIC", "date": "20261002", "close": 102.0}])
        self.assertEqual(s["^IXIC"]["date"], "20261002")
        self.assertAlmostEqual(s["^IXIC"]["change_pct"], 2.0)

    def test_overnight_features_use_strictly_earlier_us_date(self):
        from daily_backtest import overnight_features
        us = pd.DataFrame({"symbol": ["^IXIC"] * 3 + ["^VIX"] * 3, "date": ["20261001", "20261002", "20261005"] * 2,
                           "close": [100.0, 110.0, 99.0, 20.0, 30.0, 40.0]})
        f = overnight_features(us, ["20261002", "20261005", "20261006"]).set_index("date")
        self.assertTrue(pd.isna(f.loc["20261002", "ixic_chg"]))            # 10/1 미국은 첫 행(등락률 없음)
        self.assertAlmostEqual(f.loc["20261005", "ixic_chg"], 10.0)         # 10/2 미국 (+10%)
        self.assertAlmostEqual(f.loc["20261006", "vix"], 40.0)              # 10/5 미국, 같은 날짜 아님

    def test_bot_macro_inputs_respect_flag_and_age(self):
        from main_rest_async import AsyncTradingBot
        bot = AsyncTradingBot.__new__(AsyncTradingBot)
        bot.us_overnight = {"^VIX": {"date": datetime.now().strftime("%Y%m%d"), "close": 30.0, "change_pct": 5.0},
                            "KRW=X": {"date": "20200101", "close": 1300.0, "change_pct": 1.5}}
        bot.us_data_max_age_days = 4
        bot.us_macro_filter_enabled = False
        self.assertEqual(bot._us_macro_inputs(), {})
        bot.us_macro_filter_enabled = True
        self.assertEqual(bot._us_macro_inputs(), {"vix_value": 30.0})        # 오래된 환율은 제외


if __name__ == "__main__":
    unittest.main()
