"""
Gate Info:
- Importers/callers: Direct execution (`python test_api_server.py`)
- Affected API: FastAPI REST Endpoints & WebSocket Broadcaster Test Suite
- Data schemas: Portfolio Snapshot, Watchlist, Manual Order, Bot Control, Live WebSocket Streaming
- User's verbatim instruction: "파일 전체를 읽지 말고 최근 로그 50줄만 읽으면서 계속 진행해줘"
"""
import asyncio
from fastapi.testclient import TestClient
from api_server import app, ctx, ws_manager
from async_kiwoom_client import RequestPriority
from test_async_trading_loop import MockKiwoomClient, MockDatabaseManager
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot

def test_api_server_endpoints():
    """FastAPI REST 엔드포인트 및 수동 주문 / 봇 제어 기능 검증"""
    print("=" * 65)
    print("🚀 [Phase 3] FastAPI REST API 및 WebSocket 엔드포인트 종합 검증")
    print("=" * 65)

    # 1. 목(Mock) 컴포넌트로 서버 컨텍스트 초기화
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    ctx.client = mock_client
    ctx.db = mock_db
    ctx.portfolio = portfolio
    ctx.bot = bot

    client = TestClient(app)

    # 2. 헬스체크 (/api/health)
    print("▶ [Test 1] /api/health 헬스체크 검증...")
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["mode"] == "DEMO"
    print("  ✅ /api/health 정상 응답")

    # 3. 봇 상태 조회 (/api/status)
    print("▶ [Test 2] /api/status 봇 상태 조회 검증...")
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["running"] is False
    assert data["market_filter_passed"] is True
    print("  ✅ /api/status 정상 응답")

    # 4. 포트폴리오 스냅샷 조회 (/api/portfolio)
    print("▶ [Test 3] /api/portfolio 포트폴리오 스냅샷 조회 검증...")
    # 포지션 1개 사전 편입
    asyncio.run(portfolio.add_position("005930", "삼성전자", qty=10, buy_price=70000.0))
    res = client.get("/api/portfolio")
    assert res.status_code == 200
    data = res.json()
    assert data["stock_count"] == 1
    assert data["positions"][0]["code"] == "005930"
    print(f"  ✅ /api/portfolio 정상 응답 (보유종목: {data['positions'][0]['name']} {data['positions'][0]['qty']}주)")

    # 5. 감시종목 조회 (/api/watchlist)
    print("▶ [Test 4] /api/watchlist 감시종목 목록 조회 검증...")
    bot.watchlist["000660"] = {
        "name": "SK하이닉스",
        "fib_382": 150000.0,
        "fib_618": 140000.0,
        "current_price": 145000.0
    }
    res = client.get("/api/watchlist")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert "000660" in data["items"]
    print("  ✅ /api/watchlist 정상 응답")

    # 6. 대시보드 수동 주문 (/api/order/manual)
    print("▶ [Test 5] /api/order/manual 수동 발주 검증...")
    order_payload = {
        "code": "035420",
        "side": "BUY",
        "qty": 20,
        "price": 50000
    }
    res = client.post("/api/order/manual", json=order_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["result"] == "success"
    assert len(mock_client.sent_orders) == 1
    sent_order = mock_client.sent_orders[0]
    assert sent_order["code"] == "035420"
    assert sent_order["priority"] == RequestPriority.HIGH
    print(f"  ✅ 수동 주문 즉시 발주 완료: {sent_order['code']} {sent_order['qty']}주 (HIGH 우선순위)")

    # 7. 봇 제어 명령 (/api/bot/control)
    print("▶ [Test 6] /api/bot/control 봇 시작/정지 제어 검증...")
    # START 제어
    res = client.post("/api/bot/control", json={"action": "START"})
    assert res.status_code == 200
    assert res.json()["status"] == "started"
    assert ctx.bot.running is True

    # STOP 제어
    res = client.post("/api/bot/control", json={"action": "STOP"})
    assert res.status_code == 200
    assert res.json()["status"] == "stopped"
    assert ctx.bot.running is False
    print("  ✅ 봇 제어 명령(START, STOP) 정상 동작")

    # 8. 긴급 비상 킬스위치 (/api/bot/emergency-stop)
    print("▶ [Test 7] /api/bot/emergency-stop 비상 킬스위치 검증...")
    res = client.post("/api/bot/emergency-stop")
    assert res.status_code == 200
    assert res.json()["status"] == "emergency_shutdown"
    assert res.json()["liquidated_count"] == 1
    assert ctx.bot.mdd_shutdown is True
    print("  ✅ 비상 킬스위치 발동 및 전 포지션 시장가 청산 발주 완료")

    # 9. 무중단 파라미터 튜닝 (/api/bot/params)
    print("▶ [Test 8] /api/bot/params 파라미터 튜닝 검증...")
    res = client.post("/api/bot/params?k_breakout=0.6&kelly_fraction=0.5")
    assert res.status_code == 200
    assert res.json()["updated_params"]["k_breakout"] == 0.6
    assert res.json()["updated_params"]["kelly_fraction"] == 0.5
    print("  ✅ 런타임 무중단 파라미터 동적 튜닝 완료")

    # 10. WebSocket 포트폴리오 스트리밍 (/ws/portfolio)
    print("▶ [Test 9] /ws/portfolio WebSocket 연결 및 데이터 수신 검증...")
    with client.websocket_connect("/ws/portfolio") as websocket:
        init_data = websocket.receive_json()
        assert init_data["type"] == "PORTFOLIO_INIT"
        websocket.send_text("ping")
        pong = websocket.receive_text()
        assert pong == "pong"
    print("  ✅ WebSocket 포트폴리오 스트리밍 및 Ping-Pong 정상 응답")

    # 11. WebSocket 로그 스트리밍 (/ws/logs)
    print("▶ [Test 10] /ws/logs WebSocket 연결 및 Ping-Pong 검증...")
    with client.websocket_connect("/ws/logs") as websocket:
        websocket.send_text("ping")
        pong = websocket.receive_text()
        assert pong == "pong"
    print("  ✅ WebSocket 로그 스트리밍 연결 및 Ping-Pong 정상 응답")

    # 12. 실시간 차트 & 피보나치 조회 (/api/chart/{code})
    print("▶ [Test 11] /api/chart/{code} 실시간 캔들 및 피보나치 레벨 조회 검증...")
    chart_res = client.get("/api/chart/000660?period=1m")
    assert chart_res.status_code == 200
    chart_data = chart_res.json()
    assert chart_data["code"] == "000660"
    assert chart_data["name"] == "SK하이닉스"
    assert chart_data["fib_382"] == 150000.0
    print(f"  ✅ /api/chart/000660 정상 응답 (종목명: {chart_data['name']}, Fib 38.2%: {chart_data['fib_382']})")

    print("=" * 65)
    print("🎉 Phase 4 FastAPI, 킬스위치, WebSocket & 실시간 차트 모든 테스트 100% 통과 완료!")
    print("=" * 65)

if __name__ == "__main__":
    test_api_server_endpoints()
