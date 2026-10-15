"""수정주가 재수집 테스트 (collect_history: adjusted_dates, minute_daily_mismatch, refetch_minute_adjusted)"""
import asyncio
from datetime import timedelta

from collect_history import adjusted_dates, minute_daily_mismatch, refetch_minute_adjusted
from database import get_kst_now


def _row(d, close):
    return ("000500", d, close, close, close, close, 100, 1000)


def test_adjusted_dates_detects_changed_past_close_only():
    old = {"20261013": 21400, "20261014": 18000, "20261015": 18100}
    rows = [_row("20261013", 11889), _row("20261014", 18050), _row("20261015", 18500), _row("20261012", 11000)]
    # 10/13: 21,400 → 11,889(무상증자 1.8배 수정) 감지 / 10/14: 0.28% 차이는 무시 / 오늘(10/15)은 비교 안 함 / 저장 안 된 날 무시
    assert adjusted_dates(old, rows, "20261015") == ["20261013"]
    assert adjusted_dates({}, rows, "20261015") == []


class _FakeDB:
    def __init__(self, ranges, minute):
        self.ranges, self.minute, self.saved = ranges, minute, []

    async def get_daily_ranges(self, code, since, table="daily_ohlcv"):
        return self.ranges

    async def get_minute_close_between(self, code, a, b, table="minute_ohlcv"):
        return self.minute.get(a[:8])

    async def upsert_minute_rows(self, rows):
        self.saved.extend(rows)

    upsert_nxt_minute_rows = upsert_minute_rows


def test_minute_daily_mismatch_finds_unadjusted_segment():
    ranges = [(f"202601{d:02d}", 11000, 10500) for d in range(1, 25)]
    ok = {d: 10800 for d, _, _ in ranges}
    assert asyncio.run(minute_daily_mismatch(_FakeDB(ranges, ok), "000500", 365, samples=12)) is None
    bad = dict(ok, **{"20260101": 19440})  # 원주가(1.8배)가 남은 구간
    assert asyncio.run(minute_daily_mismatch(_FakeDB(ranges, bad), "000500", 365, samples=12)) == "20260101"
    assert asyncio.run(minute_daily_mismatch(_FakeDB([], {}), "000500", 365)) is None


class _FakeClient:
    base_url = "https://api"

    def __init__(self, pages, fail_at=None):
        self.pages, self.fail_at, self.calls = pages, fail_at, []

    async def request(self, api_id, url, payload, headers_override=None):
        i = len(self.calls)
        self.calls.append((payload["stk_cd"], payload["base_dt"], headers_override))
        if self.fail_at == i:
            return None, None
        more = i + 1 < len(self.pages)
        return {"return_code": 0, "stk_min_pole_chart_qry": self.pages[i]}, \
               ({"cont-yn": "Y", "next-key": f"k{i + 1}"} if more else {"cont-yn": "N"})


def _bars(day, times):
    return [{"cntr_tm": f"{day}{t}00", "open_pric": "-100", "high_pric": "101", "low_pric": "99", "cur_prc": "+100",
             "trde_qty": "10"} for t in times]


def test_refetch_uses_today_base_and_continuation():
    today = get_kst_now().strftime('%Y%m%d')
    d1 = (get_kst_now() - timedelta(days=1)).strftime('%Y%m%d')
    old = (get_kst_now() - timedelta(days=400)).strftime('%Y%m%d')
    client = _FakeClient([_bars(today, ["1500", "1959"]), _bars(d1, ["0930", "1000"]), _bars(old, ["1000"])])
    db = _FakeDB([], {})
    n = asyncio.run(refetch_minute_adjusted(client, db, "005930", 365))
    assert n == 3  # 오늘 15:00, 어제 09:30·10:00 (19:59는 정규장 밖, 400일 전은 기간 밖)
    assert {c[1] for c in client.calls} == {today}  # 모든 페이지가 오늘 기준일 (수정주가 적용 조건)
    assert client.calls[0][2] is None and client.calls[1][2] == {"cont-yn": "Y", "next-key": "k1"}
    assert all(r[5] == 100 for r in db.saved)  # 부호 제거

    nx_client = _FakeClient([_bars(today, ["1500", "1959"])])
    nx_db = _FakeDB([], {})
    assert asyncio.run(refetch_minute_adjusted(nx_client, nx_db, "005930", 365, nx=True)) == 1
    assert nx_client.calls[0][0] == "005930_NX" and nx_db.saved[0][0] == "005930"  # 6자리로 저장

    err = _FakeClient([_bars(today, ["1500"]), _bars(d1, ["1000"])], fail_at=1)
    err_db = _FakeDB([], {})
    assert asyncio.run(refetch_minute_adjusted(err, err_db, "005930", 365)) == -1
    assert len(err_db.saved) == 1  # 오류 전까지 받은 것은 저장
