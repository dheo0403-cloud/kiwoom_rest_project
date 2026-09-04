"""
Gate Info:
- Importers/callers: Direct execution (`uvicorn api_server:app --port 8000`), Dashboard / Web Client
- Affected API: FastAPI REST Endpoints & WebSocket Broadcaster
- Data schemas: Portfolio Snapshot, Watchlist, Manual Order, Bot Control, Live Logs
- User's verbatim instruction: "진행해줘"
"""
import asyncio
import json
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from async_kiwoom_client import AsyncKiwoomClient, RequestPriority
from async_portfolio import AsyncPortfolioManager
from database import DatabaseManager
from main_rest_async import AsyncTradingBot

# ================= Pydantic 요청/응답 모델 =================

class ManualOrderRequest(BaseModel):
    code: str = Field(..., description="종목코드 (예: 005930)")
    side: str = Field(..., description="주문구분 (BUY / SELL)")
    qty: int = Field(..., gt=0, description="주문수량")
    price: Optional[int] = Field(0, description="주문가격 (0: 시장가)")

class BotControlRequest(BaseModel):
    action: str = Field(..., description="제어 명령 (START / STOP / REFRESH)")

# ================= WebSocket 연결 관리자 =================

class ConnectionManager:
    """웹소켓 다중 클라이언트 브로드캐스트 관리자"""
    def __init__(self):
        self.portfolio_connections: List[WebSocket] = []
        self.log_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect_portfolio(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.portfolio_connections.append(websocket)

    async def disconnect_portfolio(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.portfolio_connections:
                self.portfolio_connections.remove(websocket)

    async def connect_log(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.log_connections.append(websocket)

    async def disconnect_log(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.log_connections:
                self.log_connections.remove(websocket)

    async def broadcast_portfolio(self, message: Dict[str, Any]):
        """포트폴리오 스냅샷 브로드캐스트"""
        async with self._lock:
            for ws in list(self.portfolio_connections):
                try:
                    await ws.send_json(message)
                except Exception:
                    self.portfolio_connections.remove(ws)

    async def broadcast_log(self, message: Dict[str, Any]):
        """실시간 로그 브로드캐스트"""
        async with self._lock:
            for ws in list(self.log_connections):
                try:
                    await ws.send_json(message)
                except Exception:
                    self.log_connections.remove(ws)

ws_manager = ConnectionManager()

# ================= 전역 인스턴스 및 백그라운드 태스크 =================

class ServerContext:
    def __init__(self):
        self.client: Optional[AsyncKiwoomClient] = None
        self.db: Optional[DatabaseManager] = None
        self.portfolio: Optional[AsyncPortfolioManager] = None
        self.bot: Optional[AsyncTradingBot] = None
        self.bot_task: Optional[asyncio.Task] = None
        self.ws_broadcast_task: Optional[asyncio.Task] = None

ctx = ServerContext()

async def portfolio_broadcast_loop():
    """1초 주기로 연결된 클라이언트에 최신 포트폴리오 스냅샷 브로드캐스팅"""
    while True:
        try:
            if ctx.portfolio and ws_manager.portfolio_connections:
                snapshot = await ctx.portfolio.get_snapshot()
                await ws_manager.broadcast_portfolio({
                    "type": "PORTFOLIO_UPDATE",
                    "data": snapshot
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"WS Broadcast Error: {e}")
        await asyncio.sleep(1.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 기동 및 종료 생명주기 관리"""
    # 1. 컴포넌트 초기화
    ctx.client = AsyncKiwoomClient()
    await ctx.client.start()

    ctx.db = DatabaseManager()
    await ctx.db.init_pool()

    ctx.portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    ctx.bot = AsyncTradingBot(
        is_demo=True,
        initial_capital=10_000_000,
        client=ctx.client,
        portfolio=ctx.portfolio,
        db=ctx.db
    )

    # 2. 포트폴리오 브로드캐스트 태스크 시작
    ctx.ws_broadcast_task = asyncio.create_task(portfolio_broadcast_loop())

    yield

    # 3. 종료 정리
    if ctx.bot_task and not ctx.bot_task.done():
        ctx.bot.running = False
        ctx.bot_task.cancel()
        try:
            await ctx.bot_task
        except asyncio.CancelledError:
            pass

    if ctx.ws_broadcast_task:
        ctx.ws_broadcast_task.cancel()

    if ctx.client:
        await ctx.client.stop()
    if ctx.db:
        await ctx.db.close_pool()

# ================= FastAPI 애플리케이션 정의 =================

app = FastAPI(
    title="Kiwoom Async Quant Trading API Server",
    description="키움증권 완전 비동기 퀀트 자동매매 및 실시간 모니터링 백엔드",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= REST 엔드포인트 =================

@app.get("/api/health")
async def health_check():
    """서버 및 핵심 컴포넌트 헬스체크"""
    return {
        "status": "healthy",
        "bot_running": ctx.bot.running if ctx.bot else False,
        "db_connected": (getattr(ctx.db, 'pool', None) is not None) if ctx.db else False,
        "mode": "DEMO" if (ctx.client and ctx.client.is_demo) else "REAL"
    }

@app.get("/api/status")
async def get_bot_status():
    """트레이딩 봇 상세 운영 상태 조회"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    circuit_open = False
    if ctx.client and hasattr(ctx.client, 'circuit_breaker') and ctx.client.circuit_breaker:
        circuit_open = ctx.client.circuit_breaker.is_open()

    return {
        "running": ctx.bot.running,
        "is_demo": ctx.bot.is_demo,
        "market_filter_passed": ctx.bot.market_filter_passed,
        "kodex200_change_rate": ctx.bot.kodex200_change_rate,
        "watchlist_count": len(ctx.bot.watchlist),
        "active_positions_count": len(ctx.portfolio.positions) if ctx.portfolio else 0,
        "circuit_breaker_open": circuit_open
    }

@app.get("/api/portfolio")
async def get_portfolio():
    """현재 포트폴리오 및 자산 스냅샷 조회"""
    if not ctx.portfolio:
        raise HTTPException(status_code=503, detail="포트폴리오 관리자가 초기화되지 않았습니다.")
    snapshot = await ctx.portfolio.get_snapshot()
    return snapshot

@app.get("/api/watchlist")
async def get_watchlist():
    """감시 종목 목록 및 피보나치 레벨 조회"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")
    return {
        "count": len(ctx.bot.watchlist),
        "items": ctx.bot.watchlist
    }

@app.post("/api/order/manual")
async def create_manual_order(req: ManualOrderRequest):
    """대시보드 수동 주문 접수 (HIGH 우선순위 발주)"""
    if not ctx.bot or not ctx.client:
        raise HTTPException(status_code=503, detail="서버 엔진이 준비되지 않았습니다.")

    order_type = "03" if req.price == 0 else "00"
    res = await ctx.client.send_order(
        code=req.code,
        qty=req.qty,
        price=req.price or 0,
        order_type=order_type,
        side=req.side.upper(),
        priority=RequestPriority.HIGH
    )

    log_msg = f"[수동주문] {req.side} {req.code}: {req.qty}주 @ {req.price if req.price > 0 else '시장가'}"
    if ctx.db:
        await ctx.db.log_message("MANUAL_ORDER", log_msg)

    await ws_manager.broadcast_log({
        "type": "LOG",
        "level": "MANUAL_ORDER",
        "message": log_msg
    })

    return {
        "result": "success",
        "order": req.model_dump(),
        "response": res
    }

@app.post("/api/bot/control")
async def control_bot(req: BotControlRequest):
    """트레이딩 봇 제어 (시작, 정지, 잔고/감시목록 갱신)"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    action = req.action.upper()
    if action == "START":
        if not ctx.bot.running:
            ctx.bot.running = True
            ctx.bot_task = asyncio.create_task(ctx.bot.run_daily_trading_loop())
            return {"status": "started", "message": "트레이딩 루프가 시작되었습니다."}
        return {"status": "already_running", "message": "트레이딩 봇이 이미 실행 중입니다."}

    elif action == "STOP":
        if ctx.bot.running:
            ctx.bot.running = False
            if ctx.bot_task:
                ctx.bot_task.cancel()
            return {"status": "stopped", "message": "트레이딩 루프가 정지되었습니다."}
        return {"status": "not_running", "message": "트레이딩 봇이 실행 중이지 않습니다."}

    elif action == "REFRESH":
        await ctx.bot.sync_account_and_portfolio()
        await ctx.bot.prepare_morning_universe()
        return {"status": "refreshed", "message": "계좌 잔고 및 감시 유니버스가 갱신되었습니다."}

    else:
        raise HTTPException(status_code=400, detail=f"알 수 없는 제어 액션: {req.action}")

# ================= WebSocket 엔드포인트 =================

@app.websocket("/ws/portfolio")
async def ws_portfolio_endpoint(websocket: WebSocket):
    """실시간 포트폴리오 스냅샷 WebSocket 스트리밍"""
    await ws_manager.connect_portfolio(websocket)
    try:
        # 최초 연결 시 즉시 스냅샷 1회 전송
        if ctx.portfolio:
            snapshot = await ctx.portfolio.get_snapshot()
            await websocket.send_json({"type": "PORTFOLIO_INIT", "data": snapshot})
        while True:
            # 클라이언트로부터 ping 또는 메시지 수신 대기
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect_portfolio(websocket)
    except Exception:
        await ws_manager.disconnect_portfolio(websocket)

@app.websocket("/ws/logs")
async def ws_logs_endpoint(websocket: WebSocket):
    """실시간 로그 스트리밍 WebSocket"""
    await ws_manager.connect_log(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect_log(websocket)
    except Exception:
        await ws_manager.disconnect_log(websocket)
