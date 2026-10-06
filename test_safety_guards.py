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
