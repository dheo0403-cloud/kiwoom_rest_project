"""N3 애프터마켓 매매 판단 로직 테스트 (n3_trader.py)"""
import asyncio
from datetime import timedelta

from async_kiwoom_client import parse_executions
from database import get_kst_now
from n3_trader import N3Trader, parse_watch_quotes, pick_n3_orders


def test_parse_watch_quotes_signed_and_suffix():
    data = {"return_code": 0, "atn_stk_infr": [
        {"stk_cd": "005930_NX", "cur_prc": "-233000", "sel_bid": "-233500", "cntr_tm": "195012"},
        {"stk_cd": "A000660_NX", "cur_prc": "+500000", "sel_bid": "", "cntr_tm": "194900"},
        {"stk_cd": "035420_NX", "cur_prc": "0", "cntr_tm": ""},
    ]}
    q = parse_watch_quotes(data)
    assert q == {"005930": {"cur": 233000, "ask": 233500, "tm": "195012"},
                 "000660": {"cur": 500000, "ask": 0, "tm": "194900"}}
    assert parse_watch_quotes(None) == {}


def _u(code, close, value, prev=None):
    return {"code": code, "name": f"N{code}", "close": close, "value": value, "prev_close": prev or close}


def test_pick_n3_orders_rules():
    universe = [_u("111110", 10000, 900), _u("222220", 10000, 800), _u("333330", 50000, 700),
                _u("444440", 10000, 600, prev=7000), _u("555550", 10000, 500), _u("666660", 10000, 400),
                _u("000880", 10000, 300), _u("777770", 10000, 200)]
    quotes = {
        "111110": {"cur": 9600, "ask": 9610, "tm": "195000"},   # −4% → 신호
        "222220": {"cur": 9800, "ask": 9810, "tm": "195000"},   # −2% → 신호 아님
        "333330": {"cur": 48000, "ask": 48050, "tm": "195000"}, # −4%, 예산 2만 원으론 0주 → 주문가능 10만 원의 70% 이하라 1주
        "444440": {"cur": 9000, "ask": 9010, "tm": "195000"},   # 당일 +42.9% 급등 종목 제외
        "555550": {"cur": 9000, "ask": 0, "tm": "153000"},      # 15:40 이후 NXT 체결 없음
        "666660": {"cur": 6000, "ask": 6010, "tm": "195000"},   # −40% → ±30% 초과 제외
        "000880": {"cur": 9000, "ask": 9010, "tm": "195000"},   # 정합성 제외 종목
        "777770": {"cur": 9700, "ask": 0, "tm": "195500"},      # −3% 경계 → 신호, 매도호가 없으면 현재가
    }
    picks = pick_n3_orders(universe, quotes, slots=5, budget=20000)
    assert [(p["code"], p["qty"], p["price"]) for p in picks] == [("111110", 2, 9610), ("333330", 1, 48050), ("777770", 2, 9700)]
    assert [p["code"] for p in pick_n3_orders(universe, quotes, slots=1, budget=20000)] == ["111110"]
    assert pick_n3_orders(universe, quotes, slots=0, budget=20000) == []


def test_pick_one_share_cap_and_cash_total():
    """예산으로 0주면 주문가능금액 70% 이하 가격만 1주, 합계는 주문가능금액 안 (10/20 결정)"""
    universe = [_u("111110", 33000, 900), _u("222220", 75000, 800), _u("333330", 50000, 700), _u("444440", 10000, 600)]
    quotes = {"111110": {"cur": 31200, "ask": 31250, "tm": "195000"},   # 1주 31,250 ≤ 70,000 → 1주
              "222220": {"cur": 72200, "ask": 72300, "tm": "195000"},   # 72,300 > 70,000 → 건너뜀
              "333330": {"cur": 48000, "ask": 48050, "tm": "195000"},   # 48,050 ≤ 남은 돈 68,750 → 1주
              "444440": {"cur": 9600, "ask": 9610, "tm": "195000"}}     # 예산 20,000 → 2주 (남은 20,700)
    picks = pick_n3_orders(universe, quotes, slots=5, budget=20000, cash=100000)
    assert [(p["code"], p["qty"]) for p in picks] == [("111110", 1), ("333330", 1), ("444440", 2)]
    assert sum(p["qty"] * p["price"] for p in picks) <= 100000
    # 남은 돈이 부족하면 1주 허용도 안 함
    assert [p["code"] for p in pick_n3_orders(universe[:1], quotes, slots=5, budget=20000, cash=30000)] == []
    # 10/19 큐리옥스 사례: 67,300 ≤ 97,781 × 70% → 1주 (50% 상한에서는 건너뜀)
    u, q = [_u("445680", 73100, 900)], {"445680": {"cur": 67300, "ask": 67300, "tm": "195000"}}
    assert [(p["code"], p["qty"]) for p in pick_n3_orders(u, q, slots=5, budget=19556, cash=97781)] == [("445680", 1)]


def test_exit_qty_caps_to_n3_quantity():
    """다른 보유분까지 팔지 않고, NXT 잔고가 안 보이면 N3 순매수 수량으로"""
    from n3_trader import exit_qty
    assert exit_qty(105, 5) == 5
    assert exit_qty(3, 5) == 3
    assert exit_qty(0, 4) == 4


def test_parse_executions_weighted_and_leading_zero():
    data = {"return_code": 0, "cntr": [
        {"ord_no": "0000037", "cntr_pric": "17100", "cntr_qty": "1"},
        {"ord_no": "0000037", "cntr_pric": "+17400", "cntr_qty": "2"},
        {"ord_no": "0000038", "cntr_pric": "18000", "cntr_qty": "0"},   # 체결 전 주문
        {"ord_no": "", "cntr_pric": "18000", "cntr_qty": "1"},
    ]}
    assert parse_executions(data) == {"37": {"price": 17300.0, "qty": 3}}
    assert parse_executions(None) == {}
    assert parse_executions({"return_code": 0}) == {}


class _FillDb:
    def __init__(self, ok=True):
        self.ok, self.updates, self.logs, self.orders = ok, [], [], []

    async def update_n3_sell_fill(self, code, price, qty):
        self.updates.append((code, price, qty))
        return self.ok

    async def log_message(self, level, msg):
        self.logs.append((level, msg))

    async def log_order(self, code, name, side, qty, price):
        self.orders.append((code, side, qty, price))

    async def get_n3_open(self):
        return [{"code": "190510", "name": "나무가", "qty": 1}]


class _FillClient:
    def __init__(self, data=None):
        self.data = data

    async def get_executions(self, priority=None):
        return self.data

    async def send_order(self, *a, **k):
        return {"return_code": 0, "ord_no": "0000123"}


class _Notifier:
    def send_message(self, msg):
        pass


def _fill_trader(data, ok=True):
    class _Portfolio:
        async def get_snapshot(self):
            return {"positions": [{"code": "190510", "qty": 1, "current_price": 18030}]}

    class _Bot:
        db, client, notifier, portfolio = _FillDb(ok), _FillClient(data), _Notifier(), _Portfolio()
    t = N3Trader(_Bot())
    t.orders.gap = 0
    return t


def test_record_fills_updates_only_full_fill():
    """전량 체결만 고치고, 일부 체결은 다음 확인까지 기다린다"""
    t = _fill_trader({"cntr": [{"ord_no": "0000001", "cntr_pric": "17170", "cntr_qty": "1"},
                               {"ord_no": "0000002", "cntr_pric": "5000", "cntr_qty": "1"}]})
    t.pending_fills = {"1": {"code": "190510", "name": "나무가", "qty": 1}, "2": {"code": "222220", "name": "B", "qty": 3}}
    asyncio.run(t.record_fills())
    assert t.db.updates == [("190510", 17170, 1)]
    assert list(t.pending_fills) == ["2"]


def test_record_fills_final_uses_partial_and_warns_missing():
    t = _fill_trader({"cntr": [{"ord_no": "2", "cntr_pric": "5000", "cntr_qty": "1"}]})
    t.pending_fills = {"2": {"code": "222220", "name": "B", "qty": 3}, "3": {"code": "333330", "name": "C", "qty": 1}}
    asyncio.run(t.record_fills(final=True))
    assert t.db.updates == [("222220", 5000, 1)]  # 체결분 수량만 기록
    assert t.pending_fills == {}
    assert any(l == "WARNING" and "잔량 2주" in m for l, m in t.db.logs)
    assert any(l == "WARNING" and "333330" in m for l, m in t.db.logs)


def test_record_fills_keeps_pending_when_query_or_update_fails():
    t = _fill_trader(None)
    t.pending_fills = {"1": {"code": "190510", "name": "나무가", "qty": 1}}
    asyncio.run(t.record_fills())
    assert list(t.pending_fills) == ["1"] and t.db.updates == []
    t = _fill_trader({"cntr": [{"ord_no": "1", "cntr_pric": "17170", "cntr_qty": "1"}]}, ok=False)
    t.pending_fills = {"1": {"code": "190510", "name": "나무가", "qty": 1}}
    asyncio.run(t.record_fills())
    assert list(t.pending_fills) == ["1"]


def test_exit_registers_order_number_for_fill_check():
    """접수 때는 임시 기록가로 N3_SELL을 남기고, 주문번호를 체결가 확인 대기에 넣는다"""
    t = _fill_trader(None)
    assert asyncio.run(t.exit_positions()) is True
    assert t.db.orders == [("190510", "N3_SELL", 1, 18030)]
    assert t.pending_fills == {"123": {"code": "190510", "name": "나무가", "qty": 1}}


def test_protects_until_deadline():
    class _Bot:
        db = client = None
    t = N3Trader(_Bot())
    t.exit_codes = {"111110": get_kst_now() + timedelta(minutes=1), "222220": get_kst_now() - timedelta(seconds=1)}
    assert t.protects("111110") and not t.protects("222220") and not t.protects("999990")


# ===== 포털 N3 패널 (표시 전용) =====
from datetime import time as dtime
from n3_trader import n3_timeline, candidate_preview


def test_n3_timeline_marks_current_step():
    tl = n3_timeline(dtime(19, 50, 30), True)
    assert tl["current"] == "판단" and tl["next"] == {"time": "19:51", "label": "매수"}
    st = {s["label"]: s["status"] for s in tl["steps"]}
    assert st["후보 감시"] == "done" and st["판단"] == "current" and st["미체결 취소"] == "upcoming"
    assert n3_timeline(dtime(7, 0), True)["current"] == "장 시작 전"
    assert n3_timeline(dtime(20, 30), True)["next"] is None
    off = n3_timeline(dtime(19, 50), False)
    assert off["trading_day"] is False and off["current"] is None


def test_candidate_preview_sorted_with_signal_flags():
    universe = [_u("111110", 10000, 900), _u("222220", 10000, 800), _u("333330", 10000, 700), _u("000880", 10000, 600)]
    quotes = {"111110": {"cur": 9800, "ask": 9810, "tm": "190000"},   # −2% 신호 아님
              "222220": {"cur": 9500, "ask": 9510, "tm": "190000"},   # −5% 신호
              "333330": {"cur": 9400, "ask": 9410, "tm": "153900"},   # −6%지만 15:40 전 체결 → 신호 아님
              "000880": {"cur": 9000, "ask": 9010, "tm": "190000"}}   # 제외 종목 → 신호 아님
    p = candidate_preview(universe, quotes, top=3)
    assert [r["code"] for r in p["items"]] == ["000880", "333330", "222220"]
    assert [r["signal"] for r in p["items"]] == [False, False, True]
    assert p["universe"] == 4 and p["quoted"] == 4 and p["signals"] == 1
    assert p["items"][2]["a"] == -0.05


def test_n3_status_endpoint(monkeypatch):
    """/n3/status: 감시 시간에 후보·예상 주문, 보유 종목 시세·체결강도, 60초 재사용"""
    from datetime import datetime
    from fastapi.testclient import TestClient
    import api_server
    from api_server import app, ctx
    from async_portfolio import AsyncPortfolioManager
    from main_rest_async import AsyncTradingBot
    from test_async_trading_loop import MockKiwoomClient, MockDatabaseManager

    universe = [_u(f"{i:05d}0", 10000, 1000 - i) for i in range(1, 121)]
    quote_calls = []

    class Db(MockDatabaseManager):
        async def get_n3_universe(self, date): return universe
        async def get_n3_open(self): return [{"code": "000010", "name": "N000010", "qty": 3}]
        async def get_latest_log_like(self, pattern): return {"message": "[N3/실거래] 19:50 판단: 신호 0건", "timestamp": "11-11 19:50"}

    class Client(MockKiwoomClient):
        async def get_watch_quotes(self, codes, priority=None):
            quote_calls.append(len(codes))
            return {"return_code": 0, "atn_stk_infr": [
                {"stk_cd": c, "cur_prc": "9500" if c.startswith("000020") else "9900", "sel_bid": "9510", "cntr_tm": "190000"} for c in codes]}
        async def get_volume_power(self, code, priority=None):
            return {"cntr_str_tm": [{"cntr_str": "88.10"}]}

    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(api_server, "get_kst_now", lambda: datetime(2026, 11, 12, 19, 0))
    monkeypatch.setattr(AsyncTradingBot, "is_korean_market_holiday", staticmethod(lambda dt: False))
    ctx.client, ctx.db = Client(), Db()
    ctx.portfolio = AsyncPortfolioManager(initial_capital=1_000_000, max_stocks=5)
    ctx.bot = AsyncTradingBot(is_demo=True, client=ctx.client, portfolio=ctx.portfolio, db=ctx.db)
    api_server._n3_cache.clear(); api_server._micro_cache.clear()
    c = TestClient(app)

    body = c.get("/api/n3/status").json()
    assert body["timeline"]["current"] == "후보 감시"
    assert body["last_judgment"]["timestamp"] == "11-11 19:50"
    cand = body["candidates"]
    assert cand["universe"] == 120 and cand["signals"] == 1 and cand["items"][0]["code"] == "000020"
    assert [p["code"] for p in cand["picks"]] == ["000020"]
    h = body["holdings"][0]
    assert h["code"] == "000010" and h["market"] == "NXT" and h["cur"] == 9900 and h["volume_power"] == 88.1
    n = len(quote_calls)
    c.get("/api/n3/status")
    assert len(quote_calls) == n  # 재사용 시간 안이면 키움 재조회 없음

    # 엔진 판단·주문 시각(19:50): 키움을 부르지 않고 직전 값을 그대로 보여준다
    monkeypatch.setattr(api_server, "get_kst_now", lambda: datetime(2026, 11, 12, 19, 50, 20))
    busy = c.get("/api/n3/status").json()
    assert len(quote_calls) == n and busy["candidates"]["signals"] == 1 and busy["holdings"][0]["code"] == "000010"
    api_server._n3_cache.clear(); api_server._micro_cache.clear()
    busy = c.get("/api/n3/status").json()  # 직전 값도 없으면 안내 문구, 보유는 빈 목록
    assert len(quote_calls) == n and busy["candidates"]["note"].startswith("엔진 판단") and busy["holdings"] == []
    assert c.get("/api/quant/status").json()["volume_power"] is None

    monkeypatch.setattr(api_server, "get_kst_now", lambda: datetime(2026, 11, 12, 10, 0))
    assert c.get("/api/n3/status").json()["candidates"]["note"] == "15:40부터 후보 감시"
    api_server._n3_cache.clear(); api_server._micro_cache.clear()
