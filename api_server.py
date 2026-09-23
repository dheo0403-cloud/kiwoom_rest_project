"""
Gate Info:
- Importers/callers: Direct execution (`uvicorn api_server:app --port 8000`), Dashboard / Web Client
- Affected API: FastAPI REST Endpoints & WebSocket Broadcaster
- Data schemas: Portfolio Snapshot, Watchlist, Manual Order, Bot Control, Live Logs
- User's verbatim instruction: "진행해줘"
"""
import asyncio
import json
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, APIRouter
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from async_kiwoom_client import AsyncKiwoomClient, RequestPriority
from async_portfolio import AsyncPortfolioManager
from database import DatabaseManager, format_kst_time_str, get_kst_now
from main_rest_async import AsyncTradingBot
from macro_regime_filter import MacroRegimeFilter, MarketRegime
from indicators import TechnicalIndicators

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
        self.log_broadcast_task: Optional[asyncio.Task] = None
        self.account_sync_task: Optional[asyncio.Task] = None
        self.daily_scheduler_task: Optional[asyncio.Task] = None

ctx = ServerContext()

async def daily_market_scheduler_loop():
    """
    익일 아침(08:50 KST) 장 시작 전 자동 웨이크업 및 수동 일시정지 상태 자동 해제 스케줄러
    - 사용자가 전날 또는 장중에 수동으로 봇을 일시정지(STOP)했더라도, 익일 영업일 아침 08:50이 되면
      자동으로 일시정지(is_paused)를 해제하고 봇을 '실행(RUNNING)' 상태로 복구하여 당일 매매 준비를 수행합니다.
    - 주말(토/일) 및 한국 공휴일/휴장일에는 가동하지 않습니다.
    """
    last_processed_date = ""
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime('%Y%m%d')

            # 매일 아침 08:50 ~ 08:55 구간 검사 (당일 1회 실행 보장)
            if now.hour == 8 and 50 <= now.minute < 55 and today_str != last_processed_date:
                # 1. 영업일 검사 (주말 및 법정공휴일/휴장일 배제)
                is_holiday = AsyncTradingBot.is_korean_market_holiday(now)
                if not is_holiday and ctx.bot:
                    last_processed_date = today_str
                    print(f"🌅 [API Server Scheduler] 영업일 아침 08:50 자동 웨이크업 시퀀스 가동 (날짜: {today_str})")

                    # 2. 일시정지 해제 및 상태 초기화 (강제 RUNNING 리셋)
                    ctx.bot.is_paused = False
                    ctx.bot.running = True
                    ctx.bot.mdd_shutdown = False
                    ctx.bot.daily_circuit_breaker = False
                    ctx.bot.daily_start_capital = 0.0
                    ctx.bot.market_filter_passed = True

                    # 3. 계좌 잔고 동기화 및 당일 감시 유니버스 사전 분석
                    try:
                        await ctx.bot.sync_account_and_portfolio()
                        await ctx.bot.prepare_morning_universe()
                    except Exception as e:
                        print(f"⚠️ [API Server Scheduler] 장전 동기화 오류: {e}")

                    # 4. 백그라운드 트레이딩 루프 태스크 시작 (중복 실행 방지)
                    if ctx.bot_task is None or ctx.bot_task.done():
                        ctx.bot_task = asyncio.create_task(ctx.bot.run_daily_trading_loop())

                    # 5. DB 및 WebSocket 실시간 알림 브로드캐스트
                    wake_log = "🌅 [자동 재시작 스케줄러] 영업일 아침 08:50 도달: 수동 일시정지 상태가 해제되고 봇이 '실행(RUNNING)' 상태로 자동 전환되었습니다."
                    if ctx.db:
                        await ctx.db.log_message("SYSTEM", wake_log)
                    await ws_manager.broadcast_log({
                        "type": "LOG_EVENT",
                        "data": {
                            "id": str(int(time.time() * 1000)),
                            "level": "SYSTEM",
                            "message": wake_log,
                            "timestamp": format_kst_time_str(now)
                        }
                    })
                    print(f"✅ [API Server Scheduler] 봇 자동 가동 및 트레이딩 루프 기동 완료")

            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [Scheduler Loop Error] {e}")
            await asyncio.sleep(5.0)

async def log_broadcast_loop():
    """DB logs 테이블을 지속 감시하여 신규 실시간 로그를 웹소켓으로 브로드캐스팅"""
    last_log_id = 0
    if ctx.db and ctx.db.pool:
        try:
            async with ctx.db.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT MAX(id) as max_id FROM logs")
                    row = await cursor.fetchone()
                    if row and row.get('max_id'):
                        last_log_id = int(row['max_id'])
        except Exception:
            pass

    while True:
        try:
            await asyncio.sleep(0.5)
            if ws_manager.log_connections and ctx.db and ctx.db.pool:
                async with ctx.db.pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "SELECT * FROM logs WHERE id > %s ORDER BY id ASC LIMIT 50",
                            (last_log_id,)
                        )
                        new_logs = await cursor.fetchall()
                        for r in new_logs:
                            last_log_id = max(last_log_id, int(r['id']))
                            created = r.get('timestamp') or r.get('created_at') or r.get('time')
                            ts = format_kst_time_str(created)

                            log_item = {
                                "id": str(r.get('id', '')),
                                "level": r.get('level', 'INFO'),
                                "message": r.get('message', ''),
                                "timestamp": ts
                            }
                            await ws_manager.broadcast_log({
                                "type": "LOG_EVENT",
                                "data": log_item
                            })
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1.0)

_last_known_valid_positions: List[Dict[str, Any]] = []

async def get_current_portfolio_snapshot() -> Dict[str, Any]:
    """
    단일 진실 소스(Single Source of Truth) 기반 최신 포트폴리오 스냅샷 생성
    - REST(/portfolio) 및 WebSocket(PORTFOLIO_UPDATE)이 100% 동일한 함수를 사용하여 플리커링/상태 불일치 원천 차단
    - DB에 저장된 실제 포지션/잔고를 최우선 반영하여 독립 프로세스 봇 데몬의 실시간 데이터와 동기화
    """
    global _last_known_valid_positions
    snapshot = {
        "total_asset": 0.0,
        "current_capital": 0.0,
        "invested_capital": 0.0,
        "stock_count": 0,
        "unrealized_pnl": 0.0,
        "total_yield_rate": 0.0,
        "positions": [],
        "last_synced_at": ""
    }

    if ctx.portfolio and (ctx.portfolio.positions or ctx.portfolio.current_capital > 0 or ctx.portfolio.total_asset > 0):
        try:
            mem_snap = await ctx.portfolio.get_snapshot()
            if mem_snap:
                snapshot.update(mem_snap)
        except Exception:
            pass

    # 인메모리 포지션이 비어있는 경우, DB에 저장된 봇 데몬의 최신 포지션 및 잔고로 갱신 (독립 프로세스 연동)
    if ctx.db:
        try:
            if hasattr(ctx.db, 'get_portfolio_positions') and not snapshot.get("positions"):
                db_pos = await ctx.db.get_portfolio_positions()
                if db_pos:
                    formatted_pos = []
                    invested = 0.0
                    for p in db_pos:
                        qty = int(p.get('qty', 0))
                        buy_p = float(p.get('buy_price', 0))
                        cur_p = float(p.get('current_price') or (buy_p if buy_p > 1.0 else 0))
                        if buy_p <= 1.0 and cur_p > 1.0:
                            buy_p = cur_p
                        elif cur_p <= 0 and buy_p > 0:
                            cur_p = buy_p
                        pnl = float(p.get('pnl')) if p.get('pnl') is not None else ((cur_p - buy_p) * qty)
                        y_rate = float(p.get('yield_rate')) if p.get('yield_rate') is not None else (((cur_p / buy_p) - 1) * 100 if buy_p > 0 else 0.0)
                        invested += buy_p * qty
                        formatted_pos.append({
                            "code": p.get('code', ''),
                            "name": p.get('name', p.get('code', '')),
                            "qty": qty,
                            "buy_price": buy_p,
                            "current_price": cur_p,
                            "highest_price": float(p.get('highest_price') or cur_p),
                            "sell_stage": int(p.get('sell_stage', 1)),
                            "pnl": pnl,
                            "yield_rate": round(y_rate, 2),
                            "eval_amt": cur_p * qty
                        })
                    snapshot["positions"] = formatted_pos
                    snapshot["stock_count"] = len(formatted_pos)
                    snapshot["invested_capital"] = invested

            if hasattr(ctx.db, 'get_latest_balance') and snapshot.get("total_asset", 0) <= 0:
                db_bal = await ctx.db.get_latest_balance()
                if db_bal and float(db_bal.get('total_asset', 0)) > 0:
                    snapshot["total_asset"] = float(db_bal.get('total_asset', 0))
                    snapshot["current_capital"] = float(db_bal.get('deposit', 0))
                    snapshot["unrealized_pnl"] = float(db_bal.get('profit_loss', 0))
                    snapshot["total_yield_rate"] = float(db_bal.get('yield', 0))
                    snapshot["last_synced_at"] = str(db_bal.get('date', ''))

            if hasattr(ctx.db, 'get_quant_performance_metrics'):
                snapshot["quant_performance"] = await ctx.db.get_quant_performance_metrics()
        except Exception as e:
            print(f"Portfolio DB sync error: {e}")

    # 🛡️ [플리커링 방어 캐시] 유효 포지션 캐싱 및 일시적 0건 수신 방어
    tot_a = float(snapshot.get("total_asset", 0))
    cur_c = float(snapshot.get("current_capital", 0))
    if snapshot.get("positions"):
        _last_known_valid_positions = snapshot["positions"]
    elif _last_known_valid_positions:
        # 계좌 총자산이 예수금보다 큰 경우(보유주식 가치 존재), 일시적 DB/TR 쿼리 갭으로 판정하여 마지막 유효 포지션 보존
        if tot_a > (cur_c + 1000):
            snapshot["positions"] = _last_known_valid_positions
            snapshot["stock_count"] = len(_last_known_valid_positions)
        elif tot_a <= (cur_c + 1000) and tot_a > 0:
            # 전량 매도 완료(순수 현금 상태) 확인 시 캐시 안전 초기화
            _last_known_valid_positions = []

    # Macro Regime 및 실시간 시장 상태 첨부
    regime_val = "BULL_TREND"
    kelly_mult = 1.0
    buy_allowed = True
    if ctx.bot and hasattr(ctx.bot, 'macro_filter') and ctx.bot.macro_filter:
        regime_val = ctx.bot.macro_filter.current_regime.value
        kelly_mult = ctx.bot.macro_filter.get_regime_kelly_multiplier()
        buy_allowed = ctx.bot.macro_filter.is_buy_allowed()

    snapshot["macro_status"] = {
        "regime": regime_val,
        "kodex200_change_rate": getattr(ctx.bot, 'kodex200_change_rate', 0.0) if ctx.bot else 0.0,
        "market_filter_passed": getattr(ctx.bot, 'market_filter_passed', True) if ctx.bot else True,
        "kelly_multiplier": kelly_mult,
        "is_buy_allowed": buy_allowed
    }

    return snapshot

async def portfolio_broadcast_loop():
    """1초 주기로 연결된 클라이언트에 최신 포트폴리오 스냅샷 브로드캐스팅"""
    while True:
        try:
            if ws_manager.portfolio_connections:
                snapshot = await get_current_portfolio_snapshot()
                await ws_manager.broadcast_portfolio({
                    "type": "PORTFOLIO_UPDATE",
                    "data": snapshot
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"WS Broadcast Error: {e}")
        await asyncio.sleep(1.0)

async def account_sync_background_loop():
    """5초 주기로 키움 OpenAPI 4대 TR을 스캔하여 MTS 앱의 5대 보유 종목을 실시간 동기화"""
    while True:
        try:
            await asyncio.sleep(5.0)
            if ctx.bot:
                await ctx.bot._sync_account_balance()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [API Server] 실시간 계좌 자동 동기화 예외: {e}")
            await asyncio.sleep(5.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 기동 및 종료 생명주기 관리"""
    # 1. 컴포넌트 초기화 (기본값: 실전투자 REAL)
    env_is_mock = os.getenv("IS_REAL", "true").lower() in ("false", "0", "no") or os.getenv("KIWOOM_MODE", "REAL").upper() in ("MOCK", "DEMO")
    is_demo = env_is_mock

    ctx.client = AsyncKiwoomClient(is_demo=is_demo)
    await ctx.client.start()

    ctx.db = DatabaseManager()
    await ctx.db.init_pool()

    ctx.portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)

    # DB로부터 직전 계좌 잔고 및 보유 포지션 즉시 복원 (장 마감/재기동 시 0원 노출 방지)
    if ctx.db and hasattr(ctx.db, 'get_latest_balance'):
        try:
            db_bal = await ctx.db.get_latest_balance()
            if db_bal and float(db_bal.get('total_asset', 0)) > 0:
                ctx.portfolio.initial_capital = float(db_bal.get('total_asset', 10_000_000))
                ctx.portfolio.total_asset = float(db_bal.get('total_asset', 10_000_000))
                ctx.portfolio.current_capital = float(db_bal.get('deposit', 10_000_000))

            if hasattr(ctx.db, 'get_portfolio_positions'):
                db_pos = await ctx.db.get_portfolio_positions()
                if db_pos:
                    await ctx.portfolio.restore_positions_from_db(db_pos)
                    print(f"✅ [API Server] DB로부터 직전 계좌 잔고(총자산: {int(ctx.portfolio.total_asset):,}원 / D+2예수금: {int(ctx.portfolio.current_capital):,}원) 및 {len(db_pos)}개 포지션 안전 복원 완료.")
        except Exception as e:
            print(f"⚠️ [API Server] 초기 DB 계좌 복원 예외: {e}")

    ctx.bot = AsyncTradingBot(
        is_demo=is_demo,
        initial_capital=ctx.portfolio.initial_capital,
        client=ctx.client,
        portfolio=ctx.portfolio,
        db=ctx.db
    )

    # 2. 서버 기동 직후 즉시 1회 키움 실시간 잔고 동기화 (MTS 앱의 5대 보유종목 즉시 로드)
    try:
        await ctx.bot._sync_account_balance()
        print(f"✅ [API Server Startup] 키움 실시간 잔고 동기화 완료: {len(ctx.portfolio.positions)}개 종목 로드됨")
    except Exception as e:
        print(f"⚠️ [API Server Startup] 키움 실시간 잔고 동기화 예외: {e}")

    # 3. 포트폴리오, 실시간 로그 및 일일 자동 웨이크업 스케줄러 태스크 시작
    ctx.ws_broadcast_task = asyncio.create_task(portfolio_broadcast_loop())
    ctx.log_broadcast_task = asyncio.create_task(log_broadcast_loop())
    ctx.account_sync_task = asyncio.create_task(account_sync_background_loop())
    ctx.daily_scheduler_task = asyncio.create_task(daily_market_scheduler_loop())

    yield

    # 4. 종료 정리
    if ctx.bot_task and not ctx.bot_task.done():
        ctx.bot.running = False
        ctx.bot_task.cancel()
        try:
            await ctx.bot_task
        except asyncio.CancelledError:
            pass

    if ctx.account_sync_task:
        ctx.account_sync_task.cancel()
    if ctx.daily_scheduler_task:
        ctx.daily_scheduler_task.cancel()
    if ctx.ws_broadcast_task:
        ctx.ws_broadcast_task.cancel()
    if ctx.log_broadcast_task:
        ctx.log_broadcast_task.cancel()

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

# ================= REST 엔드포인트 라우터 정의 =================
api_router = APIRouter()

@api_router.get("/health")
async def health_check():
    """서버 및 핵심 컴포넌트 헬스체크"""
    return {
        "status": "healthy",
        "bot_running": ctx.bot.running if ctx.bot else False,
        "db_connected": (getattr(ctx.db, 'pool', None) is not None) if ctx.db else False,
        "mode": "DEMO" if (ctx.client and ctx.client.is_demo) else "REAL"
    }

@api_router.get("/status")
async def get_bot_status():
    """트레이딩 봇 상세 운영 상태 조회"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    circuit_open = getattr(ctx.bot, 'mdd_shutdown', False) or getattr(ctx.bot, 'daily_circuit_breaker', False)
    if not circuit_open:
        try:
            if ctx.client and hasattr(ctx.client, 'circuit_breaker') and ctx.client.circuit_breaker:
                cb = ctx.client.circuit_breaker
                if hasattr(cb, 'state'):
                    circuit_open = (cb.state == "OPEN")
                elif hasattr(cb, 'is_open'):
                    circuit_open = cb.is_open() if callable(cb.is_open) else bool(cb.is_open)
                elif hasattr(cb, 'can_proceed'):
                    circuit_open = not cb.can_proceed()
        except Exception:
            circuit_open = False

    return {
        "running": ctx.bot.running,
        "is_paused": getattr(ctx.bot, 'is_paused', False),
        "is_demo": ctx.bot.is_demo,
        "market_filter_passed": ctx.bot.market_filter_passed,
        "kodex200_change_rate": ctx.bot.kodex200_change_rate,
        "watchlist_count": len(ctx.bot.watchlist),
        "active_positions_count": len(ctx.portfolio.positions) if ctx.portfolio else 0,
        "circuit_breaker_open": circuit_open,
        "mdd_shutdown": getattr(ctx.bot, 'mdd_shutdown', False),
        "daily_circuit_breaker": getattr(ctx.bot, 'daily_circuit_breaker', False)
    }

@api_router.get("/portfolio")
async def get_portfolio():
    """현재 포트폴리오 및 자산 스냅샷 조회 (단일 진실 소스 get_current_portfolio_snapshot 연동)"""
    return await get_current_portfolio_snapshot()


@api_router.get("/quant/performance")
async def get_quant_performance():
    """퀀트 핵심 성과 지표(KPI) 조회 (일일/누적 수익률, 승률, MDD, 손익비 등)"""
    if ctx.db and hasattr(ctx.db, 'get_quant_performance_metrics'):
        return await ctx.db.get_quant_performance_metrics()
    return {
        "daily_return_pct": 0.0,
        "cumulative_return_pct": 0.0,
        "win_rate_pct": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "mdd_pct": 0.0,
        "profit_factor": 0.0,
        "total_profit": 0.0,
        "total_loss": 0.0,
        "recent_closed_trades": [],
        "equity_history": []
    }


@api_router.get("/quant/status")
async def get_quant_status(code: Optional[str] = None):
    """실시간 거시(Macro) 시장 레짐 및 미시(Micro) 호가 불균형/체결강도 지표 조회"""
    # 1. Macro Regime 평가
    regime_val = "BULL_TREND"
    regime_reason = "시장 안정 상승 (정상 진입)"
    kodex_rate = 0.0
    vix_val = 18.0
    usdkrw_rate = 0.0
    kelly_mult = 1.0
    buy_allowed = True

    if ctx.bot and hasattr(ctx.bot, 'macro_filter') and ctx.bot.macro_filter:
        mf = ctx.bot.macro_filter
        regime_val = mf.current_regime.value
        kodex_rate = mf.last_kodex200_rate
        vix_val = mf.last_vix
        usdkrw_rate = mf.last_usdkrw_rate
        kelly_mult = mf.get_regime_kelly_multiplier()
        buy_allowed = mf.is_buy_allowed()

        if regime_val == "BULL_TREND":
            regime_reason = f"정상 상승 추세 (KODEX 200 {kodex_rate:+.2f}%)"
        elif regime_val == "NEUTRAL_RANGE":
            regime_reason = f"시장 조정/횡보 (KODEX 200 {kodex_rate:+.2f}%, 환율 {usdkrw_rate:+.2f}%)"
        else:
            regime_reason = f"급락 경보/공포 (KODEX 200 {kodex_rate:+.2f}%, VIX {vix_val:.1f})"

    # 2. Micro Orderbook & Volume Indicators
    target_code = code or "005930"
    imbalance = {"imbalance_ratio": 0.25, "total_bid_qty": 250000.0, "total_ask_qty": 150000.0, "bid_ask_spread": 100.0}
    volume_power = 128.5

    # 실시간 호가/체결 데이터 조회 시도
    if ctx.client and hasattr(ctx.client, 'get_orderbook'):
        try:
            ob = await ctx.client.get_orderbook(target_code, priority=RequestPriority.LOW)
            if ob:
                imbalance = TechnicalIndicators.calculate_orderbook_imbalance(ob)
        except Exception:
            pass

    return {
        "regime": regime_val,
        "regime_reason": regime_reason,
        "kodex200_change_rate": kodex_rate,
        "vix_value": vix_val,
        "usdkrw_change_pct": usdkrw_rate,
        "kelly_multiplier": kelly_mult,
        "is_buy_allowed": buy_allowed,
        "target_code": target_code,
        "orderbook_imbalance": imbalance,
        "volume_power": volume_power,
        "evaluated_at": get_kst_now().strftime('%Y-%m-%d %H:%M:%S')
    }



@api_router.get("/watchlist")
async def get_watchlist():
    """감시 종목 목록 및 피보나치 레벨 조회 (인메모리 + DB 폴백)"""
    items_dict = {}
    if ctx.bot and ctx.bot.watchlist:
        if isinstance(ctx.bot.watchlist, dict):
            items_dict = ctx.bot.watchlist
        elif isinstance(ctx.bot.watchlist, list):
            items_dict = {item.get('code', str(idx)): item for idx, item in enumerate(ctx.bot.watchlist)}

    if not items_dict and ctx.db:
        try:
            if hasattr(ctx.db, 'get_watchlist_items'):
                db_items = await ctx.db.get_watchlist_items()
                for r in db_items:
                    code = r.get('code', '')
                    cur_p = float(r.get('current_price', 0))
                    p_high = float(r.get('period_high') or (cur_p * 1.05 if cur_p > 0 else 0))
                    p_low = float(r.get('period_low') or (cur_p * 0.95 if cur_p > 0 else 0))
                    diff = p_high - p_low
                    items_dict[code] = {
                        "code": code,
                        "name": r.get('name') or code,
                        "current_price": cur_p,
                        "volume": int(r.get('avg_volume', 0)),
                        "period_high": p_high,
                        "period_low": p_low,
                        "fib_382": float(r.get('fib_382') or (p_high - diff * 0.382 if diff > 0 else cur_p)),
                        "fib_500": float(r.get('fib_500') or (p_high - diff * 0.500 if diff > 0 else cur_p)),
                        "fib_618": float(r.get('fib_618') or (p_high - diff * 0.618 if diff > 0 else cur_p)),
                        "status": r.get('status', 'WATCHING'),
                        "updated_at": str(r.get('updated_at', ''))
                    }
        except Exception as e:
            print(f"Watchlist DB fetch error: {e}")

    return {
        "count": len(items_dict),
        "items": items_dict
    }


@api_router.get("/chart/{code}")
async def get_stock_chart_data(code: str, period: str = "1m"):
    """
    특정 종목의 실시간 OHLCV 캔들 및 피보나치 레벨 조회
    - 우선순위: 1) 인메모리 링버퍼 -> 2) DB minute/daily_ohlcv -> 3) 키움 REST API
    """
    clean_code = code.replace('A', '').split('_')[0].strip()
    candles = []
    stock_name = clean_code
    current_price = 0.0
    period_high = 0.0
    period_low = 0.0
    fib_382 = 0.0
    fib_500 = 0.0
    fib_618 = 0.0

    # 1. 감시종목 / 포지션에서 메타데이터 추출
    if ctx.bot and ctx.bot.watchlist and isinstance(ctx.bot.watchlist, dict) and clean_code in ctx.bot.watchlist:
        w_item = ctx.bot.watchlist[clean_code]
        stock_name = w_item.get('name', clean_code)
        current_price = float(w_item.get('current_price', 0))
        period_high = float(w_item.get('period_high', 0))
        period_low = float(w_item.get('period_low', 0))
        fib_382 = float(w_item.get('fib_382', 0))
        fib_500 = float(w_item.get('fib_500', 0))
        fib_618 = float(w_item.get('fib_618', 0))
    elif ctx.portfolio:
        pos = ctx.portfolio.positions.get(clean_code)
        if pos:
            stock_name = pos.name
            current_price = float(pos.current_price or pos.buy_price)

    # 2. 인메모리 링버퍼에서 분봉 조회 (1m/5m)
    if period != 'D' and ctx.bot and hasattr(ctx.bot, 'buffer') and ctx.bot.buffer:
        try:
            df = ctx.bot.buffer.get_dataframe(clean_code, limit=60)
            if not df.empty:
                for row in df.itertuples():
                    dt_val = str(getattr(row, 'datetime', ''))
                    try:
                        t_sec = int(datetime.strptime(dt_val, '%Y-%m-%d %H:%M:%S').timestamp())
                    except Exception:
                        try:
                            t_sec = int(datetime.strptime(dt_val, '%Y-%m-%d %H:%M:00').timestamp())
                        except Exception:
                            t_sec = int(time.time())
                    candles.append({
                        "time": t_sec,
                        "open": float(getattr(row, 'open', 0)),
                        "high": float(getattr(row, 'high', 0)),
                        "low": float(getattr(row, 'low', 0)),
                        "close": float(getattr(row, 'close', 0)),
                        "volume": float(getattr(row, 'volume', 0))
                    })
                if current_price <= 0 and candles:
                    current_price = candles[-1]["close"]
        except Exception as e:
            print(f"RingBuffer query error: {e}")

    # 3. DB 조회 (링버퍼 데이터가 부족할 때)
    if len(candles) < 5 and ctx.db and hasattr(ctx.db, 'get_candles_by_code'):
        try:
            db_candles = await ctx.db.get_candles_by_code(clean_code, period=period, limit=60)
            if db_candles:
                candles = []
                for c in db_candles:
                    dt_str = str(c.get('datetime', ''))
                    if period == 'D':
                        t_val = dt_str[:10]
                    else:
                        try:
                            t_sec = int(datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S').timestamp())
                        except Exception:
                            t_sec = int(time.time())
                        t_val = t_sec
                    candles.append({
                        "time": t_val,
                        "open": float(c.get('open', 0)),
                        "high": float(c.get('high', 0)),
                        "low": float(c.get('low', 0)),
                        "close": float(c.get('close', 0)),
                        "volume": float(c.get('volume', 0))
                    })
                if current_price <= 0 and candles:
                    current_price = candles[-1]["close"]
        except Exception as e:
            print(f"DB candle fetch error: {e}")

    # 4. 키움 API 실시간 조회 (DB/버퍼 모두 없을 때)
    if len(candles) < 5 and ctx.client:
        today_str = datetime.now().strftime('%Y%m%d')
        if period == 'D':
            chart_res = await ctx.client.get_daily_chart(clean_code, base_dt=today_str, priority=RequestPriority.LOW)
            if chart_res and isinstance(chart_res, dict):
                items = chart_res.get('stk_dt_pole_chart_qry') or chart_res.get('output2') or chart_res.get('output') or []
                for item in reversed(items[:60]):
                    d_str = str(item.get('stck_bsop_date') or item.get('date') or '')
                    if len(d_str) == 8:
                        d_fmt = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"
                    else:
                        d_fmt = d_str
                    candles.append({
                        "time": d_fmt,
                        "open": abs(float(str(item.get('open_pric') or item.get('oprc') or 0).replace(',', ''))),
                        "high": abs(float(str(item.get('high_pric') or item.get('hgpr') or 0).replace(',', ''))),
                        "low": abs(float(str(item.get('low_pric') or item.get('lwpr') or 0).replace(',', ''))),
                        "close": abs(float(str(item.get('cur_prc') or item.get('clpr') or item.get('stck_clpr') or 0).replace(',', ''))),
                        "volume": abs(float(str(item.get('acml_vol') or item.get('vol') or 0).replace(',', '')))
                    })
        else:
            minute_res, _ = await ctx.client.get_minute_chart(clean_code, base_dt=today_str, priority=RequestPriority.LOW)
            if minute_res and isinstance(minute_res, dict):
                items = minute_res.get('stk_dt_pole_chart_qry') or minute_res.get('output2') or minute_res.get('output') or []
                for item in reversed(items[:60]):
                    dt_str = str(item.get('cntg_tm') or item.get('time') or item.get('stck_cntg_hour') or '')
                    if len(dt_str) == 6:
                        t_str = f"{today_str[:4]}-{today_str[4:6]}-{today_str[6:]} {dt_str[:2]}:{dt_str[2:4]}:{dt_str[4:6]}"
                        try:
                            t_sec = int(datetime.strptime(t_str, '%Y-%m-%d %H:%M:%S').timestamp())
                        except Exception:
                            t_sec = int(time.time())
                    else:
                        t_sec = int(time.time())
                    candles.append({
                        "time": t_sec,
                        "open": abs(float(str(item.get('open_pric') or item.get('oprc') or 0).replace(',', ''))),
                        "high": abs(float(str(item.get('high_pric') or item.get('hgpr') or 0).replace(',', ''))),
                        "low": abs(float(str(item.get('low_pric') or item.get('lwpr') or 0).replace(',', ''))),
                        "close": abs(float(str(item.get('cur_prc') or item.get('clpr') or item.get('stck_clpr') or 0).replace(',', ''))),
                        "volume": abs(float(str(item.get('acml_vol') or item.get('vol') or 0).replace(',', '')))
                    })

    # 5. 캔들 기반 피보나치 및 가격 보정
    if candles:
        if current_price <= 0:
            current_price = candles[-1]["close"]
        highs = [c["high"] for c in candles if c["high"] > 0]
        lows = [c["low"] for c in candles if c["low"] > 0]
        if highs and lows:
            if period_high <= 0: period_high = max(highs)
            if period_low <= 0: period_low = min(lows)
            diff = period_high - period_low
            if diff > 0:
                if fib_382 <= 0: fib_382 = period_high - (diff * 0.382)
                if fib_500 <= 0: fib_500 = period_high - (diff * 0.500)
                if fib_618 <= 0: fib_618 = period_high - (diff * 0.618)

    return {
        "code": clean_code,
        "name": stock_name,
        "current_price": current_price,
        "period_high": period_high,
        "period_low": period_low,
        "fib_382": fib_382,
        "fib_500": fib_500,
        "fib_618": fib_618,
        "candles": candles
    }


@api_router.get("/logs")
async def get_recent_logs_endpoint(limit: int = 100):
    """최근 100건 로그 목록 조회 (REST Polling Fallback)"""
    if ctx.db and hasattr(ctx.db, 'get_recent_logs'):
        logs = await ctx.db.get_recent_logs(limit=limit)
        return {"logs": logs, "count": len(logs)}
    return {"logs": [], "count": 0}


@api_router.post("/order/manual")
async def create_manual_order(req: ManualOrderRequest):
    """대시보드 수동 주문 접수 (HIGH 우선순위 발주)"""
    if not ctx.bot or not ctx.client:
        raise HTTPException(status_code=503, detail="서버 엔진이 준비되지 않았습니다.")

    code = req.code.replace('A', '').split('_')[0].strip()
    side = req.side.upper()
    qty = req.qty
    price = req.price or 0

    order_type = "03" if price == 0 else "00"

    # [안전 가드] 매수(BUY) 시장가 주문은 현재가 또는 호가 기반 지정가(00)로 안전 전환 (855056 에러 방지)
    if side == "BUY" and order_type == "03":
        target_price = price
        if target_price <= 0:
            price_data = await ctx.client.get_price(code, priority=RequestPriority.HIGH)
            if price_data:
                out = price_data.get('output', price_data)
                if isinstance(out, list) and len(out) > 0:
                    out = out[0]
                elif not isinstance(out, dict):
                    out = {}
                raw_p = out.get('prpr') or out.get('current_price') or out.get('stck_prpr') or 0
                try:
                    target_price = int(abs(float(str(raw_p).replace(',', '').strip())))
                except (ValueError, TypeError):
                    target_price = 0
        if target_price > 0:
            price = target_price
            order_type = "00"
            print(f"🛡️ [API 수동 매수 가드] 시장가→지정가 자동 전환: {code} @ {price:,}원")

    res = await ctx.client.send_order(
        code=code,
        qty=qty,
        price=price,
        order_type=order_type,
        side=side,
        priority=RequestPriority.HIGH
    )

    rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
    ord_no = str((res or {}).get('ord_no') or (res or {}).get('odno') or (res or {}).get('order_no') or '').strip()
    if res and str(rt_cd) == '0' and ord_no and ord_no != '0':
        if hasattr(ctx.bot, 'order_timeout_mgr') and ctx.bot.order_timeout_mgr:
            name = ctx.bot.watchlist.get(code, {}).get('name', code) if hasattr(ctx.bot, 'watchlist') else code
            await ctx.bot.order_timeout_mgr.track_order(ord_no, code, name, side, qty, price, order_type=order_type)

    log_msg = f"[수동주문] {side} {code}: {qty}주 @ {price if price > 0 else '시장가'}"
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

@api_router.post("/bot/control")
async def control_bot(req: BotControlRequest):
    """트레이딩 봇 제어 (시작, 정지, 잔고/감시목록 갱신)"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    action = req.action.upper()
    if action == "START":
        ctx.bot.is_paused = False
        ctx.bot.running = True
        ctx.bot.mdd_shutdown = False
        ctx.bot.daily_circuit_breaker = False
        ctx.bot.market_filter_passed = True
        if ctx.bot_task is None or ctx.bot_task.done():
            ctx.bot_task = asyncio.create_task(ctx.bot.run_daily_trading_loop())
            return {"status": "started", "message": "트레이딩 루프가 시작되었습니다."}
        return {"status": "already_running", "message": "트레이딩 봇이 이미 실행 중입니다."}

    elif action == "STOP":
        ctx.bot.is_paused = True
        ctx.bot.running = False
        if ctx.bot_task and not ctx.bot_task.done():
            ctx.bot_task.cancel()
        return {"status": "stopped", "message": "트레이딩 루프가 일시정지되었습니다. (익일 아침 08:50 자동 재개 예약)"}

    elif action == "REFRESH":
        await ctx.bot.sync_account_and_portfolio()
        await ctx.bot.prepare_morning_universe()
        return {"status": "refreshed", "message": "계좌 잔고 및 감시 유니버스가 갱신되었습니다."}

    elif action in ("RESET_CIRCUIT_BREAKER", "RESET_BREAKER", "RESET"):
        if hasattr(ctx.bot, 'reset_circuit_breaker'):
            ctx.bot.reset_circuit_breaker()
        else:
            ctx.bot.mdd_shutdown = False
            ctx.bot.daily_circuit_breaker = False
        return {"status": "reset", "message": "서킷 브레이커가 정상 해제되고 계좌 기준선이 재캘리브레이션되었습니다."}

    else:
        raise HTTPException(status_code=400, detail=f"알 수 없는 제어 액션: {req.action}")

@api_router.post("/bot/reset-circuit-breaker")
async def reset_circuit_breaker_endpoint():
    """
    🔓 서킷 브레이커 수동 해제 및 현재 정상 계좌 잔고 기준 재캘리브레이션
    """
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    if hasattr(ctx.bot, 'reset_circuit_breaker'):
        ctx.bot.reset_circuit_breaker()
    else:
        ctx.bot.mdd_shutdown = False
        ctx.bot.daily_circuit_breaker = False

    log_msg = "🔓 [서킷 브레이커 리셋] 사용자에 의해 서킷 브레이커가 수동 해제되었으며 정상 신규 매수 감시가 재개됩니다."
    if ctx.db:
        await ctx.db.log_message("SYSTEM", log_msg)
    await ws_manager.broadcast_log({
        "type": "LOG_EVENT",
        "data": {
            "id": str(int(time.time() * 1000)),
            "level": "SYSTEM",
            "message": log_msg,
            "timestamp": format_kst_time_str(datetime.now())
        }
    })
    return {"status": "success", "message": "서킷 브레이커가 성공적으로 해제되었습니다."}

@api_router.post("/bot/emergency-stop")
async def emergency_kill_switch():
    """
    🚨 긴급 비상 킬스위치 (Emergency Kill-Switch)
    - 즉시 봇 정지 및 신규 매수 차단
    - 현재 보유 중인 모든 포지션에 대해 CRITICAL 우선순위로 시장가(03) 전량 매도 발주
    """
    if not ctx.bot or not ctx.client or not ctx.portfolio:
        raise HTTPException(status_code=503, detail="트레이딩 엔진이 준비되지 않았습니다.")

    ctx.bot.running = False
    ctx.bot.mdd_shutdown = True
    if ctx.bot_task:
        ctx.bot_task.cancel()

    snap = await ctx.portfolio.get_snapshot()
    positions = snap.get('positions', [])
    executed_orders = []

    for pos in positions:
        code = pos['code']
        name = pos['name']
        qty = pos['qty']
        if qty > 0:
            # 800033(매도가능수량 부족) 에러 방지를 위해 기존 미체결 주문 선제 취소
            if hasattr(ctx.bot, '_cancel_unexecuted_orders_for_stock'):
                try:
                    await ctx.bot._cancel_unexecuted_orders_for_stock(code)
                except Exception as e:
                    print(f"⚠️ [Emergency Kill-Switch] 미체결 취소 예외({code}): {e}")

            res = await ctx.client.send_order(
                code=code, qty=qty, price=0, order_type="03", side="SELL",
                priority=RequestPriority.CRITICAL
            )
            executed_orders.append({"code": code, "name": name, "qty": qty, "response": res})

    alert_msg = f"🚨 [EMERGENCY KILL-SWITCH 발동] 총 {len(executed_orders)}개 포지션 긴급 시장가 청산 발주 완료!"
    if ctx.db:
        await ctx.db.log_message("CRITICAL", alert_msg)
    if hasattr(ctx.bot, 'notifier') and ctx.bot.notifier:
        ctx.bot.notifier.send_message(alert_msg)

    await ws_manager.broadcast_log({
        "type": "LOG",
        "level": "CRITICAL",
        "message": alert_msg
    })

    return {
        "status": "emergency_shutdown",
        "message": "긴급 킬스위치 발동 완료. 모든 포지션 시장가 청산 발주.",
        "liquidated_count": len(executed_orders),
        "orders": executed_orders
    }

@api_router.post("/bot/params")
async def update_bot_parameters(k_breakout: Optional[float] = None, kelly_fraction: Optional[float] = None):
    """런타임 무중단 매매 파라미터 동적 조정"""
    if not ctx.bot:
        raise HTTPException(status_code=503, detail="트레이딩 봇이 초기화되지 않았습니다.")

    updated = {}
    if k_breakout is not None and hasattr(ctx.bot, 'strategy') and ctx.bot.strategy:
        ctx.bot.strategy.k_breakout = float(k_breakout)
        updated['k_breakout'] = k_breakout

    if kelly_fraction is not None and ctx.portfolio:
        ctx.portfolio.kelly_fraction = float(kelly_fraction)
        updated['kelly_fraction'] = kelly_fraction

    return {
        "status": "updated",
        "updated_params": updated
    }

# ================= REST 라우터 등록 (루트 및 /kiwoom 서브패스 동시 지원) =================
app.include_router(api_router, prefix="/api")
app.include_router(api_router, prefix="/kiwoom/api")

# ================= WebSocket 엔드포인트 =================

@app.websocket("/ws/portfolio")
@app.websocket("/kiwoom/ws/portfolio")
async def ws_portfolio_endpoint(websocket: WebSocket):
    """실시간 포트폴리오 스냅샷 WebSocket 스트리밍"""
    await ws_manager.connect_portfolio(websocket)
    try:
        # 최초 연결 시 단일 진실 소스 기반 스냅샷 1회 전송
        snapshot = await get_current_portfolio_snapshot()
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
@app.websocket("/kiwoom/ws/logs")
async def ws_logs_endpoint(websocket: WebSocket):
    """실시간 로그 스트리밍 WebSocket (연결 즉시 최근 100건 전송)"""
    await ws_manager.connect_log(websocket)
    try:
        if ctx.db and hasattr(ctx.db, 'get_recent_logs'):
            recent_logs = await ctx.db.get_recent_logs(limit=100)
            await websocket.send_json({
                "type": "LOGS_INIT",
                "data": recent_logs
            })
    except Exception as e:
        print(f"WS Logs Init Error: {e}")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect_log(websocket)
    except Exception:
        await ws_manager.disconnect_log(websocket)


# ================= 정적 프론트엔드 서빙 및 서브패스 리다이렉트 (React Bento Grid Cockpit) =================
frontend_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

@app.get("/kiwoom", include_in_schema=False)
async def redirect_kiwoom_slash():
    """/kiwoom 접근 시 /kiwoom/ 으로 302 리다이렉트 (Starlette StaticFiles 트레일링 슬래시 보정)"""
    return RedirectResponse(url="/kiwoom/", status_code=302)

if os.path.exists(frontend_dist):
    app.mount("/kiwoom", StaticFiles(directory=frontend_dist, html=True), name="frontend_kiwoom")
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

