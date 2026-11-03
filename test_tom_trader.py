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


def test_start_passes_mode_flag_to_daemon(monkeypatch):
    # 모의 Pod(KIWOOM_MODE=MOCK)에서 데몬이 실전으로 뜨던 문제(11/2): 플래그 없으면 데몬 --real 기본값이 True
    import start
    cmds = []
    monkeypatch.setattr(start.subprocess, "Popen", lambda cmd: cmds.append(cmd))
    start.run_trading_bot(False)
    start.run_trading_bot(True)
    assert cmds[0][-1] == "--mock" and cmds[1][-1] == "--real"


def test_daemon_main_honors_mock_env(monkeypatch):
    import sys
    import main_rest_async as m
    seen = []

    class FakeBot:
        def __init__(self, is_demo, initial_capital):
            seen.append(is_demo)

        async def run_daemon(self):
            pass

        async def shutdown(self):
            pass

    monkeypatch.setattr(m, "AsyncTradingBot", FakeBot)
    monkeypatch.setattr(sys, "argv", ["main_rest_async.py"])
    monkeypatch.delenv("IS_REAL", raising=False)
    monkeypatch.delenv("KIWOOM_MODE", raising=False)
    asyncio.run(m.main())                      # 실계좌 Pod: 모드 변수 없음 → 실전 그대로
    monkeypatch.setenv("KIWOOM_MODE", "MOCK")
    asyncio.run(m.main())                      # 모의 Pod: 플래그가 없어도 모의
    monkeypatch.setattr(sys, "argv", ["main_rest_async.py", "--real"])
    asyncio.run(m.main())                      # 환경변수가 모의면 --real이 있어도 모의
    assert seen == [False, True, True]


def test_mock_summary_ends_stale_snapshot_first():
    # 11/2: 실계좌 Pod 풀 연결이 옛 읽기 스냅샷을 잡고 있어 모의 봇이 쓴 잔고·로그가 포탈에 안 보였음
    from database import AsyncDatabase
    calls = []

    class Cur:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def execute(self, q, args=None): calls.append("execute")
        async def fetchall(self): return []
        async def fetchone(self): return None

    class Conn:
        async def commit(self): calls.append("commit")
        def cursor(self): return Cur()

    class Acq:
        async def __aenter__(self): return Conn()
        async def __aexit__(self, *a): pass

    class Pool:
        def acquire(self): return Acq()

    db = AsyncDatabase.__new__(AsyncDatabase)
    db.pool = Pool()
    out = asyncio.run(db.get_mock_summary("kiwoom_mock_db"))
    assert "error" not in out and calls[0] == "commit" and calls.count("execute") == 6 and out["rehearsal_log"] == []


def test_rehearsal_day_enters_once_ignoring_risk_off(monkeypatch):
    # 11/2 대표 결정: 11/3 1회 리허설(월말 아님·하락장 필터 무시), 그 외 날은 원래 규칙
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-11-02")]
    # 시장 전체는 하락(위험 회피 켜짐) — 한 종목만 60일선·20일 전보다 위
    spec = {"111110": (lambda i, d: 10000 * (1 + 0.002 * i), lambda i, d: 1e9 + 1),
            "222220": (lambda i, d: max(1000.0, 30000 * (1 - 0.003 * i)), lambda i, d: 1e9 + 2),
            "333330": (lambda i, d: max(1000.0, 30000 * (1 - 0.003 * i)), lambda i, d: 1e9 + 3)}
    rows = _rows(days, spec)
    v, c = tt._frames(rows)
    assert tt.market_risk_off(c, tt.month_universes(v, c)) is True
    quotes = {"111110": int(rows[-3][2]) + 100, "222220": 1000, "333330": 1000}
    monkeypatch.setenv("TOM_REHEARSAL_DATE", "2026-11-03")
    db, client = _run([], [], date(2026, 11, 3), rows=rows, quotes=quotes)
    buys = [s for s in client.sent if s[0] == "BUY"]
    assert [s[1] for s in buys] == ["111110"] and buys[0][4] == "00"
    assert any("리허설" in m for _, m in db.logs)
    # 리허설 다음 날은 월말이 아니라 진입 안 함
    db, client = _run([], [], date(2026, 11, 4), rows=rows, quotes=quotes)
    assert client.sent == [] and not db.rows_called
    # 설정이 없으면 같은 날도 진입 안 함 / 잘못된 값은 무시
    monkeypatch.setenv("TOM_REHEARSAL_DATE", "11월3일")
    db, client = _run([], [], date(2026, 11, 3), rows=rows, quotes=quotes)
    assert client.sent == [] and not db.rows_called


def test_rehearsal_budget_buys_every_affordable_pick(monkeypatch):
    # 11/2 대표: 리허설은 10자리 대신 실계좌 금액(종목당 91,000원)으로 살 수 있는 종목 전부
    picks = tt.pick_tom_orders(["111110", "222220", "333330"], {c: [1.0] * 70 for c in ("111110", "222220", "333330")},
                               {"111110": 20000, "222220": 95000, "333330": 50000}, set(), 3, 91000, 10_000_000)
    assert [(p["code"], p["qty"]) for p in picks] == [("111110", 4), ("333330", 1)]   # 95,000원짜리는 1주도 못 삼
    monkeypatch.setenv("TOM_REHEARSAL_BUDGET", "91000")
    assert tt._rehearsal_budget() == 91000
    monkeypatch.setenv("TOM_REHEARSAL_BUDGET", "구만원")
    assert tt._rehearsal_budget() == 0


def test_rehearsal_orders_both_groups_merged(monkeypatch):
    # 그룹 B(원래 규칙, 종목당 총자산/10)와 그룹 A(종목당 91,000원, 살 수 있는 종목 전부)를 합친 주문
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-11-02")]
    spec = {"111110": (lambda i, d: 10000 * (1 + 0.002 * i), lambda i, d: 1e9 + 1),
            "444440": (lambda i, d: 100000 * (1 + 0.002 * i), lambda i, d: 1e9 + 4),
            "222220": (lambda i, d: max(1000.0, 30000 * (1 - 0.003 * i)), lambda i, d: 1e9 + 2)}
    rows = _rows(days, spec)
    last = {c: r[2] for r in rows for c in [r[0]] if r[1] == days[-1]}
    quotes = {"111110": int(last["111110"]) + 100, "444440": int(last["444440"]) + 1000, "222220": 1000}
    monkeypatch.setenv("TOM_REHEARSAL_DATE", "2026-11-03")
    monkeypatch.setenv("TOM_REHEARSAL_BUDGET", "91000")
    db, client = _run([], [], date(2026, 11, 3), rows=rows, quotes=quotes)
    buys = {s[1]: s for s in client.sent if s[0] == "BUY"}
    lim1, lim4 = buys["111110"][3], buys["444440"][3]
    assert lim4 > 91000                                                   # 비싼 종목은 B만
    assert buys["444440"][2] == 1_000_000 // lim4
    assert buys["111110"][2] == 1_000_000 // lim1 + 91000 // lim1         # 싼 종목은 B+A 합산
    assert any("그룹 A" in m for _, m in db.logs)


def test_rehearsal_compare_groups():
    msg = ("[TOM/모의 리허설] 그룹 B(원래 10자리): 000810 1주, 011200 41주 / "
           "그룹 A(종목당 91,000원): 011200 3주, 396300 10주")
    positions = [{"code": "011200", "name": "HMM", "qty": 44, "buy_price": 24000, "current_price": 26400},
                 {"code": "000810", "name": "삼성화재", "qty": 1, "buy_price": 660000, "current_price": 594000}]
    orders = [{"code": "000810", "name": "삼성화재", "side": "TOM_BUY", "price": 664000},
              {"code": "011200", "name": "HMM", "side": "TOM_BUY", "price": 24100},
              {"code": "396300", "name": "HT로보틱스", "side": "TOM_BUY", "price": 8670},
              {"code": "396300", "name": "HT로보틱스", "side": "TOM_SELL", "price": 9537}]   # 이미 팔림 → 매도가로 평가
    out = tt.rehearsal_compare(msg, positions, orders)
    rows = {r["code"]: r for r in out["rows"]}
    assert out["budget"] == 91000
    assert (rows["011200"]["qty_a"], rows["011200"]["qty_b"]) == (3, 41)
    assert rows["000810"]["qty_a"] == 0 and rows["396300"]["qty_b"] == 0
    assert rows["396300"]["sold"] and abs(rows["396300"]["ret"] - 10.0) < 1e-9
    a, b = out["groups"]["A"], out["groups"]["B"]
    assert a["stocks"] == 2 and a["invest"] == 3 * 24000 + 10 * 8670
    assert abs(a["ret"] - ((3 * 26400 + 10 * 9537) / (3 * 24000 + 10 * 8670) - 1) * 100) < 1e-9
    assert abs(b["ret"] - ((594000 + 41 * 26400) / (660000 + 41 * 24000) - 1) * 100) < 1e-9
    assert tt.rehearsal_compare(None, positions, orders) is None
    assert tt.rehearsal_compare("[TOM/모의] 다른 로그", positions, orders) is None
    assert tt.rehearsal_compare([], positions, orders) is None


def test_rehearsal_compare_reorder_after_unfilled():
    """11/3 미체결(429) 종목을 11/4 다시 주문 → 새 수량으로 바뀌고, 다시 안 산 미체결 종목은 빠진다"""
    first = ("[TOM/모의 리허설] 그룹 B(원래 10자리): 000810 1주, 011200 42주, 028300 24주 / "
             "그룹 A(종목당 91,000원): 011200 3주, 028300 2주")
    second = ("[TOM/모의 리허설] 그룹 B(원래 10자리): 011200 40주 / "
              "그룹 A(종목당 91,000원): 011200 4주")
    positions = [{"code": "000810", "name": "삼성화재", "qty": 1, "buy_price": 651000, "current_price": 660000},
                 {"code": "011200", "name": "HMM", "qty": 44, "buy_price": 23000, "current_price": 23500}]
    orders = [{"code": "000810", "name": "삼성화재", "side": "TOM_BUY", "price": 664000},
              {"code": "011200", "name": "HMM", "side": "TOM_BUY", "price": 23650},   # 11/3 응답 없음
              {"code": "028300", "name": "HLB", "side": "TOM_BUY", "price": 41500},   # 11/3 응답 없음
              {"code": "011200", "name": "HMM", "side": "TOM_SELL", "price": 0},      # 11/4 미체결 닫음
              {"code": "028300", "name": "HLB", "side": "TOM_SELL", "price": 0},
              {"code": "011200", "name": "HMM", "side": "TOM_BUY", "price": 23450}]   # 11/4 재주문
    out = tt.rehearsal_compare([first, second], positions, orders)
    rows = {r["code"]: r for r in out["rows"]}
    assert set(rows) == {"000810", "011200"}                       # HLB(재주문 안 함, 미체결)는 제외
    assert (rows["011200"]["qty_b"], rows["011200"]["qty_a"]) == (40, 4)
    assert out["groups"]["A"]["stocks"] == 1 and out["groups"]["B"]["stocks"] == 2


def test_rehearsal_reorder_skips_held_and_closes_unfilled(monkeypatch):
    """리허설 다음 날 다시 리허설: 체결된 종목은 보유로 빼고, 미체결 기록은 닫은 뒤 빈 자리만 다시 주문"""
    days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-01-01", "2026-11-03")]
    spec = {c: (lambda i, d, k=k: 10000 * (1 + 0.001 * i) + k, lambda i, d, k=k: 1e9 + k)
            for k, c in enumerate(["111110", "222220", "333330"])}
    rows = _rows(days, spec)
    quotes = {"111110": 13000, "222220": 13000, "333330": 13000}
    entry = datetime(2026, 11, 3, 6, 21)
    opened = [{"code": "111110", "name": "가", "qty": 76, "entry_ts": entry},   # 체결됨(보유)
              {"code": "222220", "name": "나", "qty": 76, "entry_ts": entry}]   # 429로 미체결
    pos = [{"code": "111110", "qty": 76, "current_price": 13000}]
    monkeypatch.setenv("TOM_REHEARSAL_DATE", "2026-11-04")
    monkeypatch.delenv("TOM_REHEARSAL_BUDGET", raising=False)
    db, client = _run(opened, pos, date(2026, 11, 4), rows=rows, quotes=quotes)
    assert ("222220", "TOM_SELL", 0, 0) in db.orders                      # 미체결 기록 닫음
    assert [s[1] for s in client.sent if s[0] == "BUY"] == ["222220", "333330"]  # 보유 111110 제외


def test_order_pacer_keeps_gap(monkeypatch):
    """여러 건 주문 사이 ORDER_GAP_SECONDS 이상 간격 (첫 주문은 바로)"""
    import n3_trader
    clock, slept = [100.0], []

    async def fake_sleep(s):
        slept.append(s)
        clock[0] += s

    monkeypatch.setattr(n3_trader.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(n3_trader.asyncio, "sleep", fake_sleep)
    client = _Client()
    pacer = n3_trader.OrderPacer(client, gap=1.0)

    async def go():
        for c in ("A", "B", "C"):
            await pacer.send_order(c, 1, 100)
            clock[0] += 0.2  # 응답 처리 0.2초
    asyncio.run(go())
    assert [s[1] for s in client.sent] == ["A", "B", "C"]
    assert len(slept) == 2 and all(abs(s - 0.8) < 1e-9 for s in slept)
