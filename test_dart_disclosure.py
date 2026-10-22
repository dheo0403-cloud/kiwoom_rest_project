"""dart_disclosure·_dart_worker 단위 테스트: 응답 파싱, 페이지 넘김, 장중 폴링 중단, 오류 시 미저장, 처음 본 시각 기록 (네트워크·DB 없음)"""
import asyncio
from datetime import datetime

import collection_workers
import dart_disclosure as dd
from collection_workers import CollectionWorkersMixin
from database import KST


def item(no, day="20261022", code="005930"):
    return {"rcept_no": no, "rcept_dt": day, "corp_code": "00126380", "corp_name": "삼성전자", "stock_code": code,
            "corp_cls": "Y", "report_nm": "주요사항보고서", "flr_nm": "삼성전자", "rm": "유"}


class FakeDart(dd.DartClient):
    """fetch_page를 미리 정한 응답으로 대신 (키: (corp_cls, page_no))"""

    def __init__(self, pages):
        super().__init__("test-key")
        self.pages, self.asked = pages, []

    async def fetch_page(self, day, corp_cls, page_no, today):
        self.asked.append((corp_cls, page_no))
        return self.pages.get((corp_cls, page_no), {"list": [], "total_page": 0})


def test_parse_list_keeps_valid_rows_with_seen_time():
    seen = datetime(2026, 10, 22, 10, 5)
    rows = dd.parse_list({"list": [item("20261022000123"), {"rcept_no": "bad", "rcept_dt": "20261022"}]}, seen)
    assert rows == [("20261022000123", "20261022", "00126380", "삼성전자", "005930", "Y", "주요사항보고서", "삼성전자", "유", seen)]


def test_fetch_day_reads_all_pages_of_both_markets():
    dart = FakeDart({("Y", 1): {"list": [item("20261022000001")], "total_page": 2},
                     ("Y", 2): {"list": [item("20261022000002")], "total_page": 2},
                     ("K", 1): {"list": [item("20261022000003", code="035720")], "total_page": 1}})
    rows = asyncio.run(dd.fetch_day(dart, "20261022", "20261022"))
    assert [r[0] for r in rows] == ["20261022000001", "20261022000002", "20261022000003"]
    assert dart.asked == [("Y", 1), ("Y", 2), ("K", 1)]


def test_fetch_day_stops_at_known_page_when_polling():
    dart = FakeDart({("Y", 1): {"list": [item("20261022000009"), item("20261022000001")], "total_page": 3}})
    rows = asyncio.run(dd.fetch_day(dart, "20261022", "20261022", known={"20261022000001"}))
    assert ("Y", 2) not in dart.asked                                       # 아는 공시가 나온 페이지에서 멈춤
    assert [r[0] for r in rows] == ["20261022000009", "20261022000001"]


def test_fetch_day_returns_none_on_error():
    class ErrDart(FakeDart):
        async def fetch_page(self, day, corp_cls, page_no, today):
            return None if corp_cls == "K" else {"list": [item("20261022000001")], "total_page": 1}
    assert asyncio.run(dd.fetch_day(ErrDart({}), "20261022", "20261022")) is None   # 부분 결과로 완료 처리 안 함


def test_budget_resets_each_day():
    dart = dd.DartClient("k", daily_limit=2)
    dart.calls, dart.calls_day = 2, "20261021"
    assert dart.budget_left("20261022") == 2


class WorkerDB:
    def __init__(self):
        self.saved, self.logs = [], []

    async def log_message(self, level, msg): self.logs.append((level, msg))
    async def ensure_dart(self): pass
    async def get_dart_rcept_nos(self, day): return set()

    async def upsert_dart_rows(self, rows):
        self.saved.extend(rows)
        return True


class Bot(CollectionWorkersMixin):
    def __init__(self, db):
        self.db, self.is_shutdown = db, False

    @staticmethod
    def is_korean_market_holiday(dt): return dt.weekday() >= 5

    def _nxt_sync_allowed(self, now): return now.hour >= 20 or now.hour < 8


def test_worker_records_first_seen_time_only_for_new_disclosures(monkeypatch):
    """첫 조회분은 시각 NULL, 다음 조회에서 새로 나온 공시만 그 시각으로 저장 (이미 본 공시는 다시 저장하지 않음)"""
    monkeypatch.setenv("DART_API_KEY", "k")
    times = iter([datetime(2026, 10, 22, 10, 0, tzinfo=KST), datetime(2026, 10, 22, 10, 1, tzinfo=KST)])
    clock = {"now": None}

    def fake_now():
        clock["now"] = next(times)
        return clock["now"]

    polls = iter([[("20261022000001",) + ("x",) * 8 + (None,)],
                  [("20261022000002",) + ("x",) * 8 + (datetime(2026, 10, 22, 10, 1),), ("20261022000001",) + ("x",) * 9]])
    seen_args = []

    async def fake_fetch_day(dart, day, today, known=None, first_seen_at=None):
        seen_args.append(first_seen_at)
        return next(polls)

    db = WorkerDB()
    bot = Bot(db)

    async def fake_sleep(sec):
        if len(seen_args) >= 2:
            bot.is_shutdown = True

    monkeypatch.setattr(collection_workers, "get_kst_now", fake_now)
    monkeypatch.setattr(collection_workers, "fetch_day", fake_fetch_day)
    monkeypatch.setattr(collection_workers.asyncio, "sleep", fake_sleep)
    asyncio.run(bot._dart_worker())
    assert seen_args == [None, datetime(2026, 10, 22, 10, 1)]
    assert [r[0] for r in db.saved] == ["20261022000001", "20261022000002"]


def test_worker_without_key_logs_once_and_exits(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    db = WorkerDB()
    asyncio.run(Bot(db)._dart_worker())
    assert db.logs and db.logs[0][0] == "WARNING"
