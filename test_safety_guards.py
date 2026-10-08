"""
안전장치 검증: API 토큰 인증, 중복 매수 차단, 지표 미산출 진입 보류, 로그 마스킹, 주문 재시도 1회
"""
import asyncio
from fastapi.testclient import TestClient
from api_server import app, ctx
from async_kiwoom_client import AsyncKiwoomClient
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot
from strategy import AdaptiveVolatilityBreakoutStrategy
from test_async_trading_loop import MockKiwoomClient, MockDatabaseManager


def test_mutating_api_requires_token_when_configured(monkeypatch):
    """API_AUTH_TOKEN 설정 시 변경성 API는 토큰 없으면 401, 조회 API는 영향 없음"""
    ctx.client, ctx.db = MockKiwoomClient(), MockDatabaseManager()
    ctx.portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    ctx.bot = AsyncTradingBot(is_demo=True, client=ctx.client, portfolio=ctx.portfolio, db=ctx.db)
    c = TestClient(app)

    monkeypatch.setenv("API_AUTH_TOKEN", "secret-token")
    assert c.post("/api/bot/reset-circuit-breaker").status_code == 401
    assert c.post("/api/bot/reset-circuit-breaker", headers={"X-API-Token": "wrong"}).status_code == 401
    assert c.post("/api/bot/reset-circuit-breaker", headers={"X-API-Token": "secret-token"}).status_code != 401
    assert c.get("/api/health").status_code == 200

    monkeypatch.delenv("API_AUTH_TOKEN")  # 미설정 시 기존 동작 유지
    assert c.post("/api/bot/reset-circuit-breaker").status_code != 401


async def test_concurrent_buy_evaluation_places_single_order():
    """같은 종목을 동시에 평가해도 매수 판단/발주는 한 번만 수행된다"""
    bot = AsyncTradingBot(is_demo=True, client=MockKiwoomClient(), db=MockDatabaseManager(),
                          portfolio=AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5))
    calls = []

    async def slow_impl(code, price, volume, raw_data=None):
        calls.append(code)
        await asyncio.sleep(0.05)

    bot._evaluate_buy_condition_impl = slow_impl
    await asyncio.gather(*[bot._evaluate_buy_condition("005930", 70000.0, 1000.0) for _ in range(5)])
    assert calls == ["005930"]
    assert "005930" not in bot._buy_inflight  # 종료 후 해제되어 다음 틱은 평가 가능
    await bot._evaluate_buy_condition("005930", 70000.0, 1000.0)
    assert len(calls) == 2


async def test_pending_buy_position_is_not_sold():
    """잔고로 확인되지 않은(접수만 된) 매수 종목은 손절가 아래여도 매도하지 않고, 잔고 동기화 후에는 정상 손절한다"""
    client = MockKiwoomClient()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=client, db=MockDatabaseManager(), portfolio=portfolio)
    await portfolio.add_position("035420", "NAVER", qty=100, buy_price=50000.0, confirmed=False)
    client.prices["035420"] = 47000.0  # -6%: 체결된 보유라면 손절 대상
    await bot.monitor_positions_and_exit()
    assert not [o for o in client.sent_orders if o["side"] == "SELL"]
    assert "035420" in portfolio.positions

    client.holdings["035420"] = {"name": "NAVER", "qty": 100, "buy_price": 50000.0}  # 체결되어 잔고에 반영
    await bot._sync_account_balance()
    assert not portfolio.positions["035420"].get("unconfirmed")
    await bot.monitor_positions_and_exit()
    assert [o for o in client.sent_orders if o["side"] == "SELL"]


def test_daily_entry_limits(monkeypatch):
    """같은 종목 하루 1회, 하루 진입 상한, 날짜가 바뀌면 초기화"""
    import main_rest_async
    from datetime import datetime
    from database import KST
    day = {"d": datetime(2026, 10, 6, 10, 0, tzinfo=KST)}
    monkeypatch.setattr(main_rest_async, "get_kst_now", lambda: day["d"])
    bot = AsyncTradingBot(is_demo=True, client=MockKiwoomClient(), db=MockDatabaseManager(),
                          portfolio=AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5))
    bot.one_entry_per_day, bot.max_daily_entries = True, 2
    assert bot._entry_allowed_today("A")
    bot._entered_today.add("A")
    assert not bot._entry_allowed_today("A")  # 같은 종목 재진입 차단
    assert bot._entry_allowed_today("B")
    bot._entered_today.add("B")
    assert not bot._entry_allowed_today("C")  # 상한 2 도달
    day["d"] = datetime(2026, 10, 7, 9, 30, tzinfo=KST)
    assert bot._entry_allowed_today("A")  # 다음 날 초기화
    bot.one_entry_per_day, bot.max_daily_entries = False, 0
    bot._entered_today.update({"A", "B", "C"})
    assert bot._entry_allowed_today("A")  # 둘 다 끄면 기존 동작


async def test_buy_held_when_core_indicators_missing(monkeypatch):
    """ADX/VWAP 미산출(0)이면 진입 보류, 시간필터 skip 모드(데모/테스트)는 기존 동작"""
    import strategy
    from datetime import datetime
    from database import KST
    monkeypatch.setattr(strategy, "get_kst_now", lambda: datetime(2026, 10, 5, 10, 0, tzinfo=KST))  # 장중 고정
    s = AdaptiveVolatilityBreakoutStrategy()
    ok, reason = await s.check_buy_signal("005930", 70000.0, 1000.0, ind={"open": 69000.0})
    assert not ok and "지표_미산출" in reason
    _, reason = await s.check_buy_signal("005930", 70000.0, 1000.0, ind={"open": 69000.0, "skip_time_filter": True})
    assert "지표_미산출" not in reason


def test_secret_keys_masked_set():
    assert {"accpwd", "appkey", "secretkey"} <= AsyncKiwoomClient._SECRET_KEYS


async def test_order_request_does_not_retry(monkeypatch):
    """주문 발송은 재시도 1회(=재전송 없음)로 요청된다"""
    client = AsyncKiwoomClient(is_demo=True)
    seen = {}

    async def fake_request(api_id, url, payload, priority=None, headers_override=None, retries=3):
        seen[api_id] = retries
        return {"rt_cd": "0", "ord_no": "1"}, {}

    monkeypatch.setattr(client, "request", fake_request)
    await client.send_order("005930", 1, 70000, side="BUY")
    await client.send_order("005930", 1, 70000, order_type="03", side="SELL")
    assert seen == {"kt10000": 1, "kt10001": 1}


def test_krx_holiday_calendar():
    """달력 파일 연도는 KRX 목록을 따르고(추석·대체공휴일 포함), 없는 연도는 고정 휴일 로직으로 폴백"""
    from datetime import datetime
    h = AsyncTradingBot.is_korean_market_holiday
    assert h(datetime(2026, 9, 25)) is True    # 추석 (음력, 고정 로직으로는 못 잡음)
    assert h(datetime(2026, 10, 5)) is True    # 개천절 대체공휴일 (월요일)
    assert h(datetime(2026, 10, 6)) is False   # 평일
    assert h(datetime(2026, 12, 31)) is True   # 연말 휴장
    assert h(datetime(2027, 1, 1)) is True     # 달력에 없는 연도: 고정 휴일 폴백
    assert h(datetime(2027, 2, 9)) is False    # 폴백 한계(설날 미반영)를 명시


async def test_account_balance_error_responses_keep_positions(monkeypatch):
    """잔고 TR이 모두 업무 오류(HTTP 200 + return_code≠0)면 None을 반환하고, 포지션은 비워지지 않음"""
    client = AsyncKiwoomClient(is_demo=True)

    async def fake_request(api_id, url, payload, priority=None, headers_override=None, retries=3):
        return {"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"}, {}

    monkeypatch.setattr(client, "request", fake_request)
    assert await client.get_account_balance() is None

    pm = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    await pm.add_position("018880", "한온시스템", 2, 3575.0)
    await pm.sync_positions({"return_code": 5, "return_msg": "오류", "output2": []})
    assert "018880" in pm.positions


async def test_total_asset_uses_estimated_deposit_asset():
    """총자산은 추정예탁자산(prsm_dpst_aset_amt)을 그대로 쓰고, 흔들리는 D+2 필드·주식평가금(tot_evlt_amt)과 섞지 않는다"""
    client = MockKiwoomClient()
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=client, db=MockDatabaseManager(), portfolio=portfolio)

    async def fake_balance(priority=None):  # 10/6 운영 로그 사례: 보유 31,440원인데 D+2가 매수 전 값 112,073원
        return {"return_code": 0, "tot_evlt_amt": "31440", "prsm_dpst_aset_amt": "109500",
                "d2_entra": "112073", "output2": [{"stk_cd": "232080", "stk_nm": "TIGER 코스닥150",
                                                  "rmnd_qty": "2", "pur_pric": "15720", "cur_prc": "15720"}]}

    async def fake_deposit(priority=None):
        return {"return_code": 0, "entr": "36712"}

    client.get_account_balance = fake_balance
    client.get_deposit_info = fake_deposit
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()
    assert snap["total_asset"] == 109500  # 이전 로직이면 112,073 + 31,440 = 143,513

    async def no_prsm(priority=None):  # 필드가 없으면 기존 방식(예수금+평가금)으로 폴백
        d = await fake_balance()
        d.pop("prsm_dpst_aset_amt")
        return d

    client.get_account_balance = no_prsm
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()
    assert snap["total_asset"] == 112073 + 31440


async def test_order_cash_uses_d2_only_and_keeps_sign():
    """주문가능은 100% 주문가능금액, 없으면 D+2 추정예수금만 사용: 주문가능현금(ord_alowa)과 섞지 않고, 미수(음수)는 0으로 본다"""
    client = MockKiwoomClient()
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=client, db=MockDatabaseManager(), portfolio=portfolio)
    balance = {"return_code": 0, "prsm_dpst_aset_amt": "000000107480", "output2": []}

    async def fake_balance(priority=None):
        return dict(balance)

    async def fake_deposit(priority=None):  # 10/6 15:55 운영 로그 값
        return {"return_code": 0, "ord_alowa": "000000000469", "d2_entra": "000000107480", "entr": "000000036712"}

    client.get_account_balance = fake_balance
    client.get_deposit_info = fake_deposit
    await bot._sync_account_balance()
    assert portfolio.current_capital == 107480  # ord_alowa(469)가 아님

    async def with_order_limit(priority=None):  # 10/7 15:40 운영 로그 값: MTS 주문가능금액 = 100stk_ord_alow_amt
        return {"return_code": 0, "ord_alowa": "000000000469", "d2_entra": "000000104655",
                "100stk_ord_alow_amt": "000000000104518", "entr": "000000036712"}

    client.get_deposit_info = with_order_limit
    await bot._sync_account_balance()
    assert portfolio.current_capital == 104518  # D+2(104,655)보다 100% 주문가능금액 우선

    async def minus_deposit(priority=None):  # 미수: 이전 파서는 '-'를 지워 +50,000원으로 읽었음
        return {"return_code": 0, "ord_alowa": "000000000000", "d2_entra": "-00000050000", "entr": "000000001000"}

    client.get_deposit_info = minus_deposit
    await bot._sync_account_balance()
    assert portfolio.current_capital == 0


async def test_account_balance_merges_only_ok_responses(monkeypatch):
    """정상 TR이 하나라도 있으면 그 응답만 병합 (오류 TR의 return_code가 섞이지 않음)"""
    client = AsyncKiwoomClient(is_demo=True)

    async def fake_request(api_id, url, payload, priority=None, headers_override=None, retries=3):
        if api_id == "kt00005":
            return {"return_code": 0, "stk_cntr_remn": [{"stk_cd": "018880", "stk_nm": "한온시스템", "cur_qty": "2"}]}, {}
        return {"return_code": 5, "return_msg": "오류"}, {}

    monkeypatch.setattr(client, "request", fake_request)
    data = await client.get_account_balance()
    assert data["return_code"] == 0
    assert [h["stk_cd"] for h in data["output2"]] == ["018880"]


def test_trade_metrics_subtract_cancelled_orders_and_realized_mdd():
    """취소된 매수 접수분은 FIFO에서 빠지고, 누적 실현손익 곡선 낙폭을 원 단위로 산출"""
    from database import compute_trade_metrics
    orders = [
        {"code": "A", "side": "BUY", "qty": 2, "price": 1000},
        {"code": "A", "side": "BUY", "qty": 3, "price": 9999},   # 미체결 후 취소된 접수
        {"code": "A", "side": "CANCEL_BUY", "qty": 3, "price": 0},
        {"code": "A", "side": "SELL", "qty": 2, "price": 1100},  # 1000원 매수분과 매칭되어야 함
        {"code": "B", "side": "BUY", "qty": 1, "price": 1000},
        {"code": "B", "side": "SELL", "qty": 1, "price": 800},
    ]
    m = compute_trade_metrics(orders)
    assert m["total_trades"] == 2
    a, b = m["recent_closed_trades"][1], m["recent_closed_trades"][0]
    assert a["buy_price"] == 1000 and a["pnl"] > 0
    assert b["pnl"] < 0
    assert m["cumulative_realized_pnl"] == a["pnl"] + b["pnl"]
    assert m["realized_mdd_amount"] == b["pnl"]  # 고점(A 청산 후) 대비 B 손실만큼 하락


def test_parse_unexecuted_orders_ka10075_schema():
    """ka10075 응답(oso 목록, oso_qty, io_tp_nm '+매수'/'-매도') 파싱, 구분 불명은 None"""
    from async_kiwoom_client import parse_unexecuted_orders
    data = {"return_code": 0, "oso": [
        {"ord_no": "0001234", "stk_cd": "005930", "stk_nm": "삼성전자", "ord_qty": "10", "oso_qty": "7", "io_tp_nm": "+매수"},
        {"ord_no": "0001235", "stk_cd": "A000660", "oso_qty": "2", "io_tp_nm": "-매도"},
        {"ord_no": "0001236", "stk_cd": "035420", "oso_qty": "1"},
        {"ord_no": "0001237", "stk_cd": "035720", "oso_qty": "0", "io_tp_nm": "+매수"},
    ]}
    assert parse_unexecuted_orders(data) == [
        {"ord_no": "0001234", "code": "005930", "name": "삼성전자", "side": "BUY", "qty": 7},
        {"ord_no": "0001235", "code": "000660", "name": "000660", "side": "SELL", "qty": 2},
        {"ord_no": "0001236", "code": "035420", "name": "035420", "side": None, "qty": 1},
    ]


def test_orderbook_imbalance_ka10004_schema():
    """ka10004 총잔량 키(tot_buy_req/tot_sel_req)와 부호 붙은 호가 처리"""
    from indicators import TechnicalIndicators
    ob = {"return_code": 0, "tot_buy_req": "300", "tot_sel_req": "100",
          "sel_fpr_bid": "+275500", "buy_fpr_bid": "-275000"}
    r = TechnicalIndicators.calculate_orderbook_imbalance(ob)
    assert (r["total_bid_qty"], r["total_ask_qty"], r["bid_ask_spread"]) == (300.0, 100.0, 500.0)
    assert r["imbalance_ratio"] == 0.5


class _FakeResp:
    def __init__(self, data):
        self.status, self._data, self.headers = 200, data, {}

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Authorization 헤더가 'Bearer new'일 때만 정상, 아니면 8005 (10/8 운영 응답 형식)"""
    closed = False

    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer new":
            return _FakeResp({"return_code": 0, "ord_no": "1"})
        return _FakeResp({"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"})


async def test_token_invalid_reissues_once_and_resends():
    """8005면 토큰을 한 번만 재발급하고 각 요청을 새 토큰으로 1회 재전송 (동시 요청 포함, 주문도 재시도 1회 설정과 무관)"""
    from async_kiwoom_client import QueuedRequest, is_token_invalid
    client = AsyncKiwoomClient(is_demo=True)
    client.session, client.access_token = _FakeSession(), "old"
    issued = []

    async def fake_issue():
        issued.append(1)
        await asyncio.sleep(0.01)  # 발급 중 다른 요청이 잠금에서 기다리게
        client.access_token = "new"
        return "new"

    client.get_access_token = fake_issue
    loop = asyncio.get_running_loop()
    reqs = [QueuedRequest(priority=1, timestamp=0, api_id=a, url="u", payload={}, future=loop.create_future(), retries=r)
            for a, r in (("kt10000", 1), ("kt00018", 3))]
    await asyncio.gather(*[client._execute_request(r) for r in reqs])
    assert len(issued) == 1
    assert [r.future.result()[0]["return_code"] for r in reqs] == [0, 0]
    assert client.session.calls.count("Bearer new") == 2

    # 재발급해도 새 토큰을 못 받으면 같은 토큰으로 재전송하지 않고 '응답 없음'(None)을 돌려줌
    client.access_token, client._next_token_try = "still-bad", 0
    tries = []

    async def failing_issue():
        tries.append(1)

    client.get_access_token = failing_issue
    before = len(client.session.calls)
    r = QueuedRequest(priority=1, timestamp=0, api_id="kt00018", url="u", payload={}, future=loop.create_future(), retries=1)
    await client._execute_request(r)
    assert r.future.result() == (None, None)
    assert len(tries) == 1 and len(client.session.calls) - before == 1  # 발급 1회 시도, 전송은 처음 1회뿐

    # 토큰이 아예 없으면 'Bearer None'으로 보내지 않음
    client.access_token, client._next_token_try = None, 0
    r = QueuedRequest(priority=1, timestamp=0, api_id="kt10000", url="u", payload={}, future=loop.create_future(), retries=1)
    await client._execute_request(r)
    assert r.future.result() == (None, None) and "Bearer None" not in client.session.calls


def test_token_invalid_detection_and_expiry_parse():
    """정상 응답 문구 속 숫자(18005 등)는 토큰 무효가 아님, expires_dt는 KST로 해석"""
    from async_kiwoom_client import is_token_invalid, _parse_expires
    assert is_token_invalid({"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"})
    assert not is_token_invalid({"return_code": 0, "return_msg": "매수주문 18005원 [8005:정상]"})
    assert not is_token_invalid({"return_code": 5, "return_msg": "주문가능금액 18005원 부족"})
    from datetime import datetime, timezone
    assert _parse_expires("20261009183713") == datetime(2026, 10, 9, 9, 37, 13, tzinfo=timezone.utc).timestamp()  # KST 18:37 = UTC 09:37
    assert _parse_expires(None) is None


async def test_deposit_info_skips_error_response(monkeypatch):
    """kt00001 첫 응답이 오류여도 뒤의 정상 응답을 쓴다"""
    client = AsyncKiwoomClient(is_demo=True)

    async def fake_request(api_id, url, payload, priority=None, headers_override=None, retries=3):
        if payload["qry_tp"] == "3":
            return {"return_code": 5, "return_msg": "오류"}, {}
        return {"return_code": 0, "entr": "1000"}, {}

    monkeypatch.setattr(client, "request", fake_request)
    assert (await client.get_deposit_info())["entr"] == "1000"


async def test_cancel_order_sends_cncl_qty(monkeypatch):
    """kt10003 취소는 필수 필드 cncl_qty('0'=잔량 전부)로 보낸다 (ord_qty로 보내 1511 거절되던 문제)"""
    client = AsyncKiwoomClient(is_demo=True)
    seen = {}

    async def fake_request(api_id, url, payload, priority=None, headers_override=None, retries=3):
        seen.update(payload)
        return {"return_code": 0, "ord_no": "0000141"}, {}

    monkeypatch.setattr(client, "request", fake_request)
    await client.cancel_order("0173923", "058470", 1)
    assert seen["cncl_qty"] == "0" and seen["orig_ord_no"] == "0173923" and "ord_qty" not in seen


async def test_rejected_cancel_stops_after_three_and_no_market_resell():
    """취소가 계속 거절되면 3회 뒤 재추적을 멈추고, 매도 취소 실패 시 시장가 재발주를 하지 않는다 (10/8 899회 반복·800033 사례)"""
    client, db = MockKiwoomClient(), MockDatabaseManager()
    bot = AsyncTradingBot(is_demo=True, client=client, db=db,
                          portfolio=AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5))
    mgr = bot.order_timeout_mgr

    async def reject(order_no, code, qty, priority=None):
        return {"return_code": 2, "return_msg": "입력 값 오류입니다[1511:필수입력 파라미터=cncl_qty]"}

    async def still_open(code="", priority=None):
        return {"return_code": 0, "oso": [{"ord_no": "0173923", "stk_cd": "058470", "oso_qty": "1", "io_tp_nm": "+매수"}]}

    attempts = []

    async def no_response(order_no, code, qty, priority=None):  # 일시 장애(응답 없음)는 거절 횟수에 안 셈
        attempts.append(order_no)
        return None

    async def counted_reject(order_no, code, qty, priority=None):
        attempts.append(order_no)
        return await reject(order_no, code, qty)

    async def cycle():
        await bot.cleanup_unexecuted_orders()  # 미체결 목록에서 재추적
        for info in mgr.tracked_orders.values():
            info["timestamp"] -= 3600  # 타임아웃 경과로 만듦
        await bot.cleanup_unexecuted_orders()  # 타임아웃 → 취소 시도

    client.get_unexecuted_orders = still_open
    client.cancel_order = no_response
    await cycle()
    assert mgr.cancel_failures.get("0173923", 0) == 0

    client.cancel_order = counted_reject
    for _ in range(5):
        await cycle()
    assert mgr.cancel_failures["0173923"] == 3
    assert len(attempts) == 1 + 3  # 3회 거절 뒤에는 더 취소하지 않음
    assert "058470" in mgr.get_pending_buy_codes()  # 살아 있는 주문이라 미체결로는 계속 집계
    assert sum("취소 반복 실패" in l["message"] for l in db.logs) == 1
    assert not any("취소 완료" in l["message"] for l in db.logs)

    async def gone(code="", priority=None):  # 체결·장 종료로 미체결 목록에서 사라지면 추적 해제
        return {"return_code": 0, "oso": []}

    client.get_unexecuted_orders = gone
    await bot.cleanup_unexecuted_orders()
    assert "0173923" not in mgr.tracked_orders

    await mgr.track_order("0200001", "005930", "삼성전자", "SELL", 1, 70000)
    mgr.tracked_orders["0200001"]["timestamp"] -= 3600
    sells_before = [o for o in client.sent_orders if o["side"] == "SELL"]
    await mgr.check_and_resolve_timeouts()
    assert [o for o in client.sent_orders if o["side"] == "SELL"] == sells_before  # 원 매도 주문 유지, 시장가 재발주 없음


class _StateDB(MockDatabaseManager):
    """오늘 매수 종목·bot_state를 흉내 내는 DB"""
    def __init__(self, codes=(), state=None):
        super().__init__()
        self.codes, self.state, self.saved = set(codes), dict(state or {}), []

    async def get_today_buy_codes(self):
        return set(self.codes)

    async def ensure_bot_state(self):
        pass

    async def load_bot_state(self, state_date):
        return dict(self.state)

    async def save_bot_state(self, state_date, values):
        self.saved.append((state_date, dict(values)))


def _clock(monkeypatch, d):
    import main_rest_async
    monkeypatch.setattr(main_rest_async, "get_kst_now", lambda: d["d"])


async def test_restart_restores_entries_and_daily_loss_baseline(monkeypatch):
    """장중 재시작: 오늘 매수 종목·시작 자산·실현손익을 복원하고, 웜업이 시작 자산을 현재 값으로 덮어쓰지 않는다"""
    from datetime import datetime
    from database import KST
    d = {"d": datetime(2026, 10, 12, 11, 0, tzinfo=KST)}
    _clock(monkeypatch, d)
    db = _StateDB(codes={"005930"}, state={"daily_start_capital": 110_000, "highest_total_asset": 111_000,
                                           "daily_realized_pnl": -2_000, "circuit_breaker": 1, "breach_count": 3})
    client = MockKiwoomClient(deposit=100_000)
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=client, db=db, portfolio=portfolio)
    bot.one_entry_per_day = True
    await bot._restore_today_state()
    assert not bot._entry_allowed_today("005930")  # 재시작 전에 산 종목은 다시 사지 않음
    assert bot.daily_start_capital == 110_000 and portfolio.daily_realized_pnl == -2_000
    assert bot.daily_circuit_breaker and bot.mdd_shutdown  # 재시작 전 손실 한도 차단 유지
    await bot._sync_account_balance()  # 웜업 1회차
    assert bot.daily_start_capital == 110_000  # 현재 자산으로 덮어쓰지 않음
    assert db.saved and db.saved[-1][0] == "2026-10-12" and db.saved[-1][1]["daily_realized_pnl"] == -2_000
    n = len(db.saved)
    await bot._sync_account_balance()
    assert len(db.saved) == n  # 값이 그대로면 다시 저장하지 않음


async def test_date_change_resets_daily_state(monkeypatch):
    """날짜가 바뀐 뒤 첫 동기화에서 하루 단위 값 초기화(타임컷 플래그·연속 위반 포함), 같은 날 재호출은 무시"""
    from datetime import datetime
    from database import KST
    d = {"d": datetime(2026, 10, 12, 15, 40, tzinfo=KST)}
    _clock(monkeypatch, d)
    db = _StateDB()
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=MockKiwoomClient(deposit=100_000), db=db, portfolio=portfolio)
    await bot._sync_account_balance()  # API 서버 기동 동기화(복원 전) → 저장하지 않음
    assert db.saved == []
    await bot._restore_today_state()
    bot.time_cut_executed, bot.circuit_breaker_breach_count = True, 2
    portfolio.daily_realized_pnl, bot.daily_start_capital = -5_000, 120_000

    d["d"] = datetime(2026, 10, 13, 0, 30, tzinfo=KST)  # 봇이 재시작 없이 자정을 넘김
    await bot._sync_account_balance()
    assert not bot.time_cut_executed and bot.circuit_breaker_breach_count == 0
    assert portfolio.daily_realized_pnl == 0 and bot.daily_start_capital == 100_000  # 첫 동기화 자산이 새 기준
    assert db.saved[-1][0] == "2026-10-13" and db.saved[-1][1]["daily_realized_pnl"] == 0
    bot.time_cut_executed = True
    bot._reset_for_new_day()  # 같은 날 재호출은 무시
    assert bot.time_cut_executed

    # 새벽 동기화로 잡힌 기준·웜업은 08:50 장전 준비에서 다시 잡음 (서킷 브레이커도 해제)
    bot.mdd_shutdown = bot.daily_circuit_breaker = True
    d["d"] = datetime(2026, 10, 13, 8, 50, tzinfo=KST)
    await bot._prepare_market_open()
    assert not bot.mdd_shutdown and bot.daily_start_capital == 0 and bot.sync_warmup_count == 0
    assert bot.time_cut_executed  # 날짜 초기화는 이미 자정에 했으므로 다시 하지 않음


async def test_restore_mid_warmup_continues_calibration(monkeypatch):
    """웜업 도중 저장된 기준값이면 남은 웜업을 이어서 진행(max 보정 유지), 저장 실패는 다음 동기화에 재시도"""
    from datetime import datetime
    from database import KST
    d = {"d": datetime(2026, 10, 12, 9, 1, tzinfo=KST)}
    _clock(monkeypatch, d)
    db = _StateDB(state={"daily_start_capital": 90_000, "highest_total_asset": 90_000, "warmup": 1})
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=MockKiwoomClient(deposit=100_000), db=db, portfolio=portfolio)
    await bot._restore_today_state()
    assert bot.sync_warmup_count == 1 and not bot._state_restored
    await bot._sync_account_balance()
    assert bot.daily_start_capital == 100_000  # 남은 웜업에서 현재 자산으로 보정

    calls = []

    async def failing_save(state_date, values):
        calls.append(1)
        return False

    db.save_bot_state = failing_save
    portfolio.daily_realized_pnl = -1_000
    await bot._sync_account_balance()
    await bot._sync_account_balance()
    assert len(calls) == 2  # 실패한 저장은 '저장됨'으로 표시하지 않고 다음에 다시 시도


async def test_balance_not_saved_when_all_tr_error():
    """잔고·예수금 TR이 모두 업무 오류면 DB 잔고를 저장하지 않고 기존 자산값 유지 (10/8 17,205원 오기록 사례)"""
    client, db = MockKiwoomClient(), MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=100_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, client=client, db=db, portfolio=portfolio)
    saved = []

    async def record(*a):
        saved.append(a)

    async def no_balance(priority=None):
        return None

    async def token_error(priority=None):
        return {"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"}

    db.update_balance = record
    client.get_account_balance, client.get_deposit_info = no_balance, token_error
    before = (await portfolio.get_snapshot())["total_asset"]
    await bot._sync_account_balance()
    assert saved == []
    assert (await portfolio.get_snapshot())["total_asset"] == before

    async def deposit_ok(priority=None):  # 잔고 TR만 실패해도 메모리 포지션으로 총자산을 만들지 않음
        return {"return_code": 0, "entr": "17205", "d2_entra": "17205"}

    client.get_deposit_info = deposit_ok
    await bot._sync_account_balance()
    assert saved == []


async def test_derivative_etf_rejection_blocks_code_for_today(monkeypatch):
    """509247(파생상품 ETF 거래신청 미등록) 거부 종목은 DB에 남기고 다음 날·재시작 후에도 매수 제외, 다른 종목은 허용"""
    import main_rest_async
    from datetime import datetime
    from database import KST
    day = {"d": datetime(2026, 10, 12, 10, 0, tzinfo=KST)}
    monkeypatch.setattr(main_rest_async, "get_kst_now", lambda: day["d"])
    client, db = MockKiwoomClient(), MockDatabaseManager()
    bot = AsyncTradingBot(is_demo=True, client=client, db=db,
                          portfolio=AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5))

    async def reject(*a, **k):
        return {"return_code": 2000, "return_msg": "[2000](509247:파생상품 ETF 거래신청 등록 후 주문이 가능합니다.)"}

    blocked = {}

    async def add_buy_block(code, name, reason):
        blocked[code] = reason

    async def get_buy_blocklist():
        return set(blocked)

    async def ensure_buy_blocklist():
        pass

    db.add_buy_block, db.get_buy_blocklist, db.ensure_buy_blocklist = add_buy_block, get_buy_blocklist, ensure_buy_blocklist
    client.send_order = reject
    await bot._execute_smart_buy("114800", "KODEX 인버스", 1, 5000.0)
    assert not bot._entry_allowed_today("114800")
    assert bot._entry_allowed_today("005930")
    assert any("매수 제외" in l["message"] for l in db.logs) and "509247" in blocked["114800"]
    day["d"] = datetime(2026, 10, 13, 9, 30, tzinfo=KST)
    assert not bot._entry_allowed_today("114800")  # 다음 날도 제외

    bot2 = AsyncTradingBot(is_demo=True, client=client, db=db,
                           portfolio=AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5))
    await bot2._restore_today_state()  # 재시작: DB에서 다시 읽음
    assert not bot2._entry_allowed_today("114800")
