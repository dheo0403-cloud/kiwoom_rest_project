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
