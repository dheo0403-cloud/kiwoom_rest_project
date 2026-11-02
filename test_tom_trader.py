"""TOM 월말 스윙(모의 전용) 판단·매매 흐름 테스트 (tom_trader.py)"""
import asyncio
from datetime import date, datetime

import pandas as pd

import tom_trader as tt
from main_rest_async import AsyncTradingBot

H = AsyncTradingBot.is_korean_market_holiday


def test_month_end_uses_krx_calendar():
    assert tt.is_month_end(date(2026, 11, 30), H)       # 다음 거래일 12/1
    assert not tt.is_month_end(date(2026, 11, 27), H)   # 다음 거래일 11/30
    assert tt.is_month_end(date(2026, 12, 30), H)       # 12/31 휴장 → 다음 거래일 1월
    assert not tt.is_month_end(date(2026, 12, 29), H)


def test_trading_days_after_counts_holidays():
    # 11/30 진입 → 12월 거래일 1~4, 7~11, 14~18, 21 = 15번째가 12/21
    assert tt.trading_days_after(date(2026, 11, 30), date(2026, 12, 18), H) == 14
    assert tt.trading_days_after(date(2026, 11, 30), date(2026, 12, 21), H) == 15
    # 12/25 휴장 반영
    assert tt.trading_days_after(date(2026, 12, 24), date(2026, 12, 28), H) == 1


def test_schedule_helpers():
    assert tt.add_trading_days(date(2026, 11, 30), 15, H) == date(2026, 12, 21)
    assert tt.next_month_end(date(2026, 11, 2), H) == date(2026, 11, 30)
    assert tt.next_month_end(date(2026, 11, 30), H) == date(2026, 11, 30)
    assert tt.next_month_end(date(2026, 12, 1), H) == date(2026, 12, 30)


def test_mock_summary_api_read_only(monkeypatch):
    """모의계좌 탭 API: MOCK_DB_NAME이 없거나 식별자가 아니면 꺼짐, 있으면 모의 DB 읽기 + 예정 청산일·다음 월말"""
    from fastapi.testclient import TestClient
    from api_server import app, ctx

    class _SumDB:
        called = None

        async def get_mock_summary(self, name):
            _SumDB.called = name
            return {"balance": [], "positions": [], "orders": [], "logs": [],
                    "tom_open": [{"code": "111110", "name": "가", "qty": 3, "entry_ts": "2026-11-30 15:21:05"}]}

    ctx.db = _SumDB()
    c = TestClient(app)
    monkeypatch.delenv("MOCK_DB_NAME", raising=False)
    assert c.get("/api/mock/summary").json() == {"configured": False}
    monkeypatch.setenv("MOCK_DB_NAME", "x; DROP TABLE logs")
    assert c.get("/api/mock/summary").json() == {"configured": False}
    monkeypatch.setenv("MOCK_DB_NAME", "kiwoom_mock_db")
    body = c.get("/kiwoom/api/mock/summary").json()
    assert _SumDB.called == "kiwoom_mock_db" and body["configured"] and body["hold_days"] == 15
    assert body["tom_open"][0]["planned_exit"] == "2026-12-21"
    assert body["next_month_end"] >= "2026-11-30"


def _rows(days, spec):
    """spec: {code: (close_fn(i, d), value_fn(i, d))}"""
    return [(c, d, cf(i, d), vf(i, d)) for i, d in enumerate(days) for c, (cf, vf) in spec.items()]


def test_month_universes_prior_20_days_and_filters():
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-03-10")]
    feb1, mar1 = min(d for d in days if d[4:6] == "02"), min(d for d in days if d[4:6] == "03")
    spec = {
        "111110": (lambda i, d: 5000, lambda i, d: 1e12 if d == feb1 else 100),  # 첫 거래일 당일 값은 2월 순위에 안 들어감
        "222220": (lambda i, d: 5000, lambda i, d: 200),
        "333330": (lambda i, d: 900, lambda i, d: 300),                          # 전날 종가 1,000원 미만 제외
    }
    value, close = tt._frames(_rows(days, spec))
    u = tt.month_universes(value, close, top_n=2)
    assert "2026-01" not in u                      # 조회 첫 달은 버림
    assert u["2026-02"] == ["222220", "111110"]
    assert u["2026-03"][0] == "111110"             # 3월은 그 전 20일 안에 2월 첫날 큰 값이 들어감
    assert "333330" not in u["2026-03"]
    assert feb1 < mar1


def _market(trend):
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-11-27")]
    spec = {c: (lambda i, d, k=k: max(1000.0, 10000 * (1 + trend * i) + k), lambda i, d, k=k: 1000 + k)
            for k, c in enumerate(["111110", "222220", "333330"])}
    value, close = tt._frames(_rows(days, spec))
    return value, close


def test_market_risk_off_direction_and_shortage():
    value, close = _market(+0.002)
    assert tt.market_risk_off(close, tt.month_universes(value, close)) is False
    value, close = _market(-0.002)
    assert tt.market_risk_off(close, tt.month_universes(value, close)) is True
    short = close.iloc[-60:]
    assert tt.market_risk_off(short, tt.month_universes(value.iloc[-60:], short)) is None


def test_entry_ok_ma60_and_20day_return():
    flat = [100.0] * 59
    assert tt.entry_ok(flat, 101)
    assert not tt.entry_ok(flat, 99)
    assert not tt.entry_ok(flat[:58], 101)                 # 59일 미만
    assert not tt.entry_ok([100.0] * 39 + [120.0] * 20, 110)  # 20일 전 120보다 낮음


def test_pick_tom_orders_order_slots_budget_cash():
    prior = {c: [10000.0] * 59 for c in ["333330", "111110", "222220", "444440"]}
    prices = {"111110": 10100, "222220": 10100, "333330": 9000, "444440": 10100}  # 333330은 60일선 아래
    picks = tt.pick_tom_orders(["444440", "333330", "222220", "111110"], prior, prices, held={"222220"},
                               slots=2, budget=100000, cash=1_000_000)
    assert [(p["code"], p["qty"], p["price"]) for p in picks] == [("111110", 9, 10300), ("444440", 9, 10300)]
    # 주문가능금액이 모자라면 남은 돈만큼, 0주면 건너뜀
    picks = tt.pick_tom_orders(["111110", "444440"], prior, prices, held=set(), slots=5, budget=100000, cash=120000)
    assert [(p["code"], p["qty"]) for p in picks] == [("111110", 9), ("444440", 2)]
    assert tt.pick_tom_orders(["111110"], prior, prices, held=set(), slots=0, budget=100000, cash=10**9) == []
    assert tt.pick_tom_orders(["111110"], prior, prices, held=set(), slots=3, budget=5000, cash=10**9) == []


# ── 매매 흐름 (가짜 DB·주문) ──
class _Portfolio:
    def __init__(self, positions, total=10_000_000, cash=10_000_000):
        self.positions, self.total_asset, self.current_capital = positions, total, cash

    async def get_snapshot(self):
        return {"positions": self.positions}


class _DB:
    def __init__(self, opened, buys_today=0, rows=None):
        self.opened, self.buys_today, self.rows, self.orders, self.logs = opened, buys_today, rows or [], [], []
        self.rows_called = False

    async def get_tom_open(self):
        return self.opened

    async def count_side_since(self, side, utc_start):
        return self.buys_today

    async def get_tom_market_rows(self, market_db, start, before):
        self.rows_called = True
        return [r for r in self.rows if start <= r[1] < before]

    async def get_stock_names(self, market_db, codes):
        return {c: f"N{c}" for c in codes}

    async def log_order(self, code, name, side, qty, price):
        self.orders.append((code, side, qty, price))

    async def log_message(self, level, msg):
        self.logs.append((level, msg))


class _Client:
    def __init__(self, quotes=None):
        self.sent, self.quotes = [], quotes or {}

    async def send_order(self, code, qty, price, order_type="00", side="BUY", priority=None, exchange="KRX"):
        self.sent.append((side, code, qty, price, order_type))
        return {"return_code": 0, "ord_no": "1"}

    async def get_watch_quotes(self, codes, priority=None):
        return {"return_code": 0, "atn_stk_infr": [{"stk_cd": c, "cur_prc": str(self.quotes[c])} for c in codes if c in self.quotes]}


class _Bot:
    is_shutdown = is_paused = False
    is_korean_market_holiday = staticmethod(H)

    def __init__(self, db, client, portfolio):
        self.db, self.client, self.portfolio = db, client, portfolio

    async def _remote_paused(self):
        return False

    async def _sync_account_balance(self):
        pass


def _run(opened, positions, today, **kw):
    db = _DB(opened, **{k: v for k, v in kw.items() if k in ("buys_today", "rows")})
    client = _Client(kw.get("quotes"))
    bot = _Bot(db, client, _Portfolio(positions))
    asyncio.run(tt.TomTrader(bot).daily(today))
    return db, client


ENTRY_UTC = datetime(2026, 11, 30, 6, 21)  # 11/30 15:21 KST


def test_exit_on_15th_trading_day_only():
    opened = [{"code": "111110", "name": "가", "qty": 10, "entry_ts": ENTRY_UTC}]
    pos = [{"code": "111110", "qty": 10, "current_price": 11000}]
    db, client = _run(opened, pos, date(2026, 12, 18))
    assert client.sent == [] and db.orders == []
    db, client = _run(opened, pos, date(2026, 12, 21))
    assert client.sent == [("SELL", "111110", 10, 0, "03")]
    assert db.orders == [("111110", "TOM_SELL", 10, 11000)]


def test_unfilled_buy_closed_without_order():
    opened = [{"code": "111110", "name": "가", "qty": 10, "entry_ts": ENTRY_UTC}]
    other = [{"code": "999990", "qty": 1, "current_price": 1000}]  # 잔고 TR은 정상(다른 종목은 보임)
    db, client = _run(opened, other, date(2026, 12, 1))
    assert client.sent == [] and db.orders == [("111110", "TOM_SELL", 0, 0)]
    db, client = _run(opened, other, date(2026, 11, 30))  # 당일(체결 전)에는 닫지 않음
    assert db.orders == []


def test_empty_balance_keeps_record_until_hold_days():
    """잔고 목록이 통째로 비면 TR 실패일 수 있어 닫지 않고, 보유 기간이 차면 닫는다 (보유분을 놓치지 않게)"""
    opened = [{"code": "111110", "name": "가", "qty": 10, "entry_ts": ENTRY_UTC}]
    db, client = _run(opened, [], date(2026, 12, 1))
    assert db.orders == [] and client.sent == [] and db.logs[-1][0] == "WARNING"
    db, client = _run(opened, [], date(2026, 12, 21))
    assert db.orders == [("111110", "TOM_SELL", 0, 0)] and client.sent == []


def test_restart_does_not_buy_twice_on_month_end():
    db, client = _run([], [], date(2026, 11, 30), buys_today=1)
    assert client.sent == [] and not db.rows_called


def test_month_end_entry_end_to_end():
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-11-27")]
    spec = {c: (lambda i, d, k=k: 10000 * (1 + 0.001 * i) + k, lambda i, d, k=k: 1e9 + k)
            for k, c in enumerate(["333330", "111110", "222220"])}
    rows = _rows(days, spec)
    quotes = {"111110": 13000, "222220": 13000, "333330": 13000}
    db, client = _run([], [], date(2026, 11, 30), rows=rows, quotes=quotes)
    buys = [s for s in client.sent if s[0] == "BUY"]
    assert [s[1] for s in buys] == ["111110", "222220", "333330"]   # 코드 순
    assert all(s[4] == "00" and s[3] == 13260 and s[2] == 1_000_000 // 13260 for s in buys)
    assert [o[1] for o in db.orders] == ["TOM_BUY"] * 3
    # 월말이 아니면 시세도 읽지 않음
    db, client = _run([], [], date(2026, 11, 27), rows=rows, quotes=quotes)
    assert client.sent == [] and not db.rows_called
    # 전 거래일(11/27) 일봉이 없으면(수집 누락) 진입 안 함
    db, client = _run([], [], date(2026, 11, 30), rows=[r for r in rows if r[1] < "20261127"], quotes=quotes)
    assert client.sent == [] and "수집 누락" in db.logs[-1][1]
