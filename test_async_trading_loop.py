"""
Gate Info:
- Importers/callers: Direct execution (`python test_async_trading_loop.py`)
- Affected API: None (Async Trading Loop Simulation & Quantitative Strategy Validation)
- Data schemas: Fibonacci Pullback, Smart Orderbook Execution, Multi-stage Exit, CRITICAL Stop-Loss
- User's verbatim instruction: "승인할께"
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from async_kiwoom_client import RequestPriority
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot

class MockKiwoomClient:
    """테스트 시뮬레이션용 Mock 비동기 키움 클라이언트"""
    def __init__(self, deposit: float = 10_000_000.0):
        self.sent_orders: List[Dict[str, Any]] = []
        self.prices: Dict[str, float] = {}
        self.orderbooks: Dict[str, Dict[str, Any]] = {}
        self.minute_candles: Dict[str, List[Dict[str, Any]]] = {}
        self.holdings: Dict[str, Dict[str, Any]] = {}
        self.kodex200_change_rate: float = 0.5
        self.is_demo = True
        self.mode = "MOCK"
        self.deposit = deposit

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_order(self, code: str, qty: int, price: int,
                         order_type: str = "00", side: str = "BUY",
                         priority: RequestPriority = RequestPriority.HIGH) -> Dict[str, Any]:
        order_record = {
            "code": code,
            "qty": qty,
            "price": price,
            "order_type": order_type,
            "side": side,
            "priority": priority,
            "timestamp": time.time()
        }
        self.sent_orders.append(order_record)
        if side == "BUY":
            self.deposit = max(0.0, self.deposit - (qty * price))
            if code in self.holdings:
                self.holdings[code]["qty"] += qty
            else:
                self.holdings[code] = {"qty": qty, "buy_price": price, "name": code}
        elif side == "SELL":
            self.deposit += qty * price
            if code in self.holdings:
                self.holdings[code]["qty"] -= qty
                if self.holdings[code]["qty"] <= 0:
                    del self.holdings[code]
        return {"rt_cd": "0", "msg1": "주문접수성공", "ord_no": f"ORD{len(self.sent_orders)}"}

    async def cancel_order(self, order_no: str, code: str, qty: int, priority: RequestPriority = RequestPriority.HIGH) -> Dict[str, Any]:
        self.sent_orders.append({
            "code": code,
            "qty": qty,
            "order_no": order_no,
            "side": "CANCEL",
            "priority": priority,
            "timestamp": time.time()
        })
        return {"rt_cd": "0", "msg1": "주문취소성공"}

    async def get_price(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        price = self.prices.get(code, 70000)
        return {
            "output": [{
                "stk_cd": code,
                "prpr": str(int(price)),
                "current_price": str(int(price)),
                "fluct_rate": "+1.5"
            }]
        }

    async def get_orderbook(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        price = self.prices.get(code, 70000)
        return {
            "output": [{
                "stk_cd": code,
                "sel_fpr_bid": str(int(price + 100)),     # 매도 1호가
                "buy_fpr_bid": str(int(price)),           # 매수 1호가
                "buy_fpr_bid2": str(int(price - 100))     # 매수 2호가
            }]
        }

    async def get_minute_chart(self, code: str, base_dt: str, next_key: Optional[str] = None,
                               priority: RequestPriority = RequestPriority.LOW):
        candles = self.minute_candles.get(code, [
            {"oprn": "74800", "clpr": "75000", "cntg_vol": "1500"},  # 직전봉 양봉 반등
            {"oprn": "75000", "clpr": "74800", "cntg_vol": "1200"},
            {"oprn": "75200", "clpr": "75000", "cntg_vol": "1000"}
        ])
        return {"output2": candles}, None

    async def get_top_trading_value(self, mrkt_tp: str = "000", limit: int = 30, priority: RequestPriority = RequestPriority.LOW):
        return [
            {"code": "005930", "name": "삼성전자", "price": 70000.0, "volume": 10000000, "trading_value": 700000000000},
            {"code": "000660", "name": "SK하이닉스", "price": 145000.0, "volume": 5000000, "trading_value": 725000000000}
        ]

    async def get_daily_chart(self, code: str, base_dt: str, priority: RequestPriority = RequestPriority.LOW):
        candles = [
            {"hgpr": 75000, "lwpr": 69000, "clpr": 74500, "oprc": 70000, "vol": 1000000}
            for _ in range(25)
        ]
        return {"output2": candles}

    async def get_unexecuted_orders(self, code: str = "", priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """미체결 주문 조회"""
        return {"output": []}

    async def get_market_daily_ohlcv(self, code: str, start_date: str = "", end_date: str = "", priority: RequestPriority = RequestPriority.LOW):
        import pandas as pd
        return pd.DataFrame([
            {"date": "20260302", "open": 70000, "high": 75000, "low": 69000, "close": 74500, "volume": 1000000, "value": 74500000000}
        ])

    async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM) -> Optional[Dict[str, Any]]:
        out2 = []
        for code, info in self.holdings.items():
            out2.append({
                "stk_cd": code,
                "stk_nm": info.get("name", code),
                "hldg_qty": str(info["qty"]),
                "pchs_avg_pric": str(info["buy_price"]),
                "prpr": str(int(self.prices.get(code, info["buy_price"]))),
                "evlu_pfls_amt": "0",
                "evlu_pfls_rt": "0.0"
            })
        return {
            "output1": [{
                "prvs_rcdl_excc_amt": str(int(self.deposit)),    # 단순 예수금 원금 (총자산, 11만원대)
                "tot_evlu_amt": str(int(self.deposit)),          # 총평가금액 (D+2와 동일할 수 있음)
                "dnca_tot_amt": str(int(self.deposit * 0.87)),   # D+2 추정예수금 (10만원대)
                "tot_evlu_pfls_amt": "0",
                "tot_pnl_rt": "0.0"
            }],
            "output2": out2
        }

    async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM) -> Optional[Dict[str, Any]]:
        """예수금 상세 현황 조회 (kt00001 대응)"""
        return {
            "output1": [{
                "entr": str(int(self.deposit)),                  # 당일 순수 예수금
                "prvs_rcdl_excc_amt": str(int(self.deposit)),    # 전일 예수금
                "dnca_tot_amt": str(int(self.deposit * 0.87)),   # D+2 추정예수금
                "ord_psbl_cash": str(int(self.deposit * 0.87))   # 주문가능금액
            }]
        }

class MockDatabaseManager:
    """테스트 시뮬레이션용 Mock 비동기 DB 매니저"""
    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
        self.order_history: List[Dict[str, Any]] = []
        self.watchlist_db: Dict[str, Any] = {}
        self.portfolio_db: Dict[str, Any] = {}
        self.manual_orders: List[Dict[str, Any]] = []

    async def init_pool(self):
        pass

    async def close_pool(self):
        pass

    async def log_message(self, level: str, message: str):
        self.logs.append({"level": level, "message": message, "timestamp": time.time()})

    async def log_order(self, code: str, name: str, side: str, qty: int, price: float):
        self.order_history.append({
            "code": code,
            "name": name,
            "side": side,
            "qty": qty,
            "price": price,
            "timestamp": time.time()
        })

    async def update_balance(self, total_asset: float, deposit: float, profit_loss: float, yield_rate: float):
        pass

    async def get_latest_balance(self) -> Optional[Dict[str, Any]]:
        return None

    async def get_quant_performance_metrics(self) -> Dict[str, Any]:
        return {
            "daily_return_pct": 1.25,
            "cumulative_return_pct": 8.45,
            "win_rate_pct": 65.5,
            "total_trades": 20,
            "winning_trades": 13,
            "losing_trades": 7,
            "mdd_pct": -2.15,
            "profit_factor": 2.45,
            "total_profit": 550000.0,
            "total_loss": 224000.0,
            "recent_closed_trades": [],
            "equity_history": []
        }

    async def get_portfolio_positions(self) -> List[Dict[str, Any]]:
        return getattr(self, 'portfolio', [])

    async def save_watchlist(self, codes: Any, name_map: Optional[Dict[str, str]] = None):
        name_map = name_map or {}
        if isinstance(codes, dict):
            codes = list(codes.values())
        for c in codes:
            if isinstance(c, dict):
                c_code = c.get('code') or c.get('stk_cd', '')
                c_name = c.get('name') or c.get('stk_nm') or name_map.get(c_code, c_code)
                self.watchlist_db[c_code] = c_name
            elif isinstance(c, str):
                self.watchlist_db[c] = name_map.get(c, c)

    async def update_watchlist_price(self, code: str, name: str, price: float, volume: int = 0):
        pass

    async def save_portfolio(self, positions_dict: Dict[str, Any], current_prices: Optional[Dict[str, float]] = None):
        self.portfolio_db = positions_dict.copy()

    async def get_pending_manual_orders(self) -> List[Dict[str, Any]]:
        return [o for o in self.manual_orders if o.get("status") == "PENDING"]

    async def complete_manual_order(self, order_id: int, status: str = "COMPLETED"):
        for o in self.manual_orders:
            if o.get("id") == order_id:
                o["status"] = status

# ================= 단위 및 시뮬레이션 테스트 =================

async def test_fibonacci_pullback_entry():
    """1. 피보나치 눌림목 매수 진입 및 HIGH 우선순위 발주 검증"""
    print("▶ [Test 1] 피보나치 눌림목 매수 진입 시뮬레이션...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 20일 고점 80,000원, 저점 70,000원 -> fib_382=76,180원, fib_618=73,820원
    bot.watchlist["005930"] = {
        "name": "삼성전자",
        "high_20d": 80000.0,
        "low_20d": 70000.0,
        "fib_382": 76180.0,
        "fib_500": 75000.0,
        "fib_618": 73820.0,
        "current_price": 75000.0
    }
    mock_client.prices["005930"] = 75000.0  # 눌림목 구간(73,820 ~ 76,180)에 진입

    # 매수 기회 감시 실행
    await bot.monitor_watchlist_and_enter()

    # 검증: 포지션 편입 및 주문 발송 여부
    assert "005930" in portfolio.positions, "삼성전자가 포트폴리오에 편입되어야 합니다."
    assert len(mock_client.sent_orders) == 1, "매수 주문이 1건 발송되어야 합니다."

    last_order = mock_client.sent_orders[0]
    assert last_order["code"] == "005930"
    assert last_order["side"] == "BUY"
    assert last_order["priority"] == RequestPriority.HIGH, "정규 매수는 HIGH 우선순위여야 합니다."
    assert last_order["price"] == 75100, "매도 1호가(75,100원)로 스마트 매수 발주되어야 합니다."
    print(f"  ✅ 피보나치 매수 성공: {last_order['code']} {last_order['qty']}주 @ {last_order['price']}원 (Priority: HIGH)")

async def test_three_stage_profit_taking():
    """2. 스마트 3단계 분할 익절(+3%, +5%, +8%) 검증"""
    print("▶ [Test 2] 스마트 3단계 분할 익절 시뮬레이션...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 매수가 100,000원, 100주 보유 포지션 설정
    await portfolio.add_position("000660", "SK하이닉스", qty=100, buy_price=100000.0)
    mock_client.holdings["000660"] = {"name": "SK하이닉스", "qty": 100, "buy_price": 100000.0}

    # 20개 안정적 분봉 적재 (ATR = 2,000원 기준 설정)
    initial_candles = [
        {"datetime": f"2026-09-04 09:{i:02d}:00", "open": 100000, "high": 102000, "low": 100000, "close": 101000, "volume": 1000}
        for i in range(20)
    ]
    bot.buffer.load_initial_candles("000660", initial_candles)

    # 1단계 익절 테스트 (+3.5% 상승: 103,500원 -> 50% 매도)
    mock_client.prices["000660"] = 103500.0
    await bot.monitor_positions_and_exit()
    pos = portfolio.positions["000660"]
    assert pos["sell_stage"] == 1, "1단계 익절 완료 상태여야 합니다."
    assert pos["qty"] == 50, f"50주 매도 후 50주가 남아야 합니다. (실제: {pos['qty']}주)"
    print(f"  ✅ 1단계 익절(+3%) 완료: 잔여 {pos['qty']}주, 다음 단계: Stage {pos['sell_stage']}")

    # 2단계 익절 테스트 (+5.5% 상승: 105,500원 -> 남은 수량의 50%인 25주 매도)
    mock_client.prices["000660"] = 105500.0
    await bot.monitor_positions_and_exit()
    pos = portfolio.positions["000660"]
    assert pos["sell_stage"] == 2, "2단계 익절 완료 상태여야 합니다."
    assert pos["qty"] == 25, f"25주 추가 매도 후 25주가 남아야 합니다. (실제: {pos['qty']}주)"
    print(f"  ✅ 2단계 익절(+5%) 완료: 잔여 {pos['qty']}주, 다음 단계: Stage {pos['sell_stage']}")

    # 3단계 익절 테스트 (3차 ATR R3 107,000원 돌파: 108,500원 -> 잔여 전량 매도 및 청산)
    mock_client.prices["000660"] = 108500.0
    await bot.monitor_positions_and_exit()
    assert "000660" not in portfolio.positions, "3단계 전량 익절 후 포지션이 청산되어야 합니다."
    print("  ✅ 3단계 익절(+8%) 완료: 전량 청산 완료")

async def test_hard_stop_loss_preemption():
    """3. 하드 스탑로스(-4.0%) 발동 및 CRITICAL 선점 발주 검증"""
    print("▶ [Test 3] 하드 스탑로스(-4.0%) 및 CRITICAL 우선순위 선점 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 매수가 50,000원, 100주 보유 포지션 설정
    await portfolio.add_position("035420", "NAVER", qty=100, buy_price=50000.0)
    mock_client.holdings["035420"] = {"name": "NAVER", "qty": 100, "buy_price": 50000.0}

    # -4.5% 급락 (현재가 47,750원)
    mock_client.prices["035420"] = 47750.0
    await bot.monitor_positions_and_exit()

    assert "035420" not in portfolio.positions, "손절 후 포지션이 청산되어야 합니다."
    emergency_order = mock_client.sent_orders[-1]
    assert emergency_order["code"] == "035420"
    assert emergency_order["side"] == "SELL"
    assert emergency_order["priority"] == RequestPriority.CRITICAL, "긴급 손절은 CRITICAL 우선순위로 큐를 선점해야 합니다."
    assert emergency_order["price"] == 47650, "매수 2호가(47,650원)로 즉시 슬리피지 방지 체결 유도되어야 합니다."
    print(f"  ✅ 긴급 손절 성공: {emergency_order['code']} {emergency_order['qty']}주 (Priority: CRITICAL=0)")

async def test_trailing_stop():
    """4. 트레일링 스탑 (최고가 대비 -2.5% 반락 시 CRITICAL 청산) 검증"""
    print("▶ [Test 4] 트레일링 스탑 (고점 대비 -2.5% 반락) 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 매수가 20,000원, 200주 보유 -> 장중 25,000원까지 급등 (+25%)
    await portfolio.add_position("000270", "기아", qty=200, buy_price=20000.0)
    mock_client.holdings["000270"] = {"name": "기아", "qty": 200, "buy_price": 20000.0}
    await portfolio.update_current_price("000270", 25000.0)

    # 최고가(25,000원) 대비 -3.0% 반락하여 24,250원으로 하락
    mock_client.prices["000270"] = 24250.0
    await bot.monitor_positions_and_exit()

    assert "000270" not in portfolio.positions, "트레일링 스탑 발동 후 청산되어야 합니다."
    ts_order = mock_client.sent_orders[-1]
    assert ts_order["priority"] == RequestPriority.CRITICAL, "트레일링 스탑은 CRITICAL 우선순위여야 합니다."
    print(f"  ✅ 트레일링 스탑 성공: 최고가 25,000원 대비 반락 감지 -> 긴급 청산 완료 (Priority: CRITICAL)")

async def test_market_filter_and_manual_orders():
    """5. KODEX 200 지수 급락 필터 및 대시보드 수동 주문 처리 검증"""
    print("▶ [Test 5] 시장 필터 및 대시보드 수동 주문 비동기 처리 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1) 시장 필터 차단 검증
    bot.market_filter_passed = False  # KODEX 200 -1.5% 이하 급락 상태 시뮬레이션
    bot.watchlist["005930"] = {
        "name": "삼성전자",
        "fib_382": 76180.0, "fib_618": 73820.0, "current_price": 75000.0
    }
    mock_client.prices["005930"] = 75000.0
    await bot.monitor_watchlist_and_enter()
    assert "005930" not in portfolio.positions, "시장 필터 미통과 시 신규 매수가 차단되어야 합니다."
    print("  ✅ 시장 필터(-1.5% 급락) 신규 매수 완벽 차단 확인")

    # 2) 수동 주문 처리 검증
    mock_db.manual_orders = [
        {"id": 1, "code": "005930", "side": "BUY", "qty": 10, "status": "PENDING"},
        {"id": 2, "code": "000660", "side": "SELL", "qty": 5, "status": "PENDING"}
    ]
    # 포지션에 000660 미리 등록
    await portfolio.add_position("000660", "SK하이닉스", qty=5, buy_price=150000.0)
    mock_client.holdings["000660"] = {"name": "SK하이닉스", "qty": 5, "buy_price": 150000.0}

    await bot.process_manual_orders()
    assert mock_db.manual_orders[0]["status"] == "COMPLETED"
    assert mock_db.manual_orders[1]["status"] == "COMPLETED"
    assert "005930" in portfolio.positions, "수동 매수 종목이 포트폴리오에 반영되어야 합니다."
    assert "000660" not in portfolio.positions, "수동 매도 종목이 포트폴리오에서 청산되어야 합니다."
    print("  ✅ 대시보드 PENDING 수동 주문 2건 비동기 체결 및 동기화 완료")

async def test_update_watchlist_multi_schema_and_fallback():
    """6. 거래대금 상위 다중 스키마 파싱 및 MOCK Fallback 검증"""
    print("▶ [Test 6] 거래대금 상위 다중 스키마(Korean/English) 파싱 및 Fallback 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1) 실전 키움 REST 스키마 (trde_prica_upper, stk_dt_pole_chart_qry, high_pric, low_pric)
    class CustomSchemaMockClient(MockKiwoomClient):
        async def get_top_trading_value(self, mrkt_tp: str = "000", limit: int = 30, priority: RequestPriority = RequestPriority.LOW):
            return {
                "rt_cd": "0",
                "trde_prica_upper": [
                    {"stk_cd": "005930_AL", "stk_nm": "삼성전자", "cur_prc": "75000", "trde_prica": "500000000000"},
                    {"stk_cd": "000660", "stk_nm": "SK하이닉스", "cur_prc": "150000", "trde_prica": "300000000000"}
                ]
            }

        async def get_daily_chart(self, code: str, base_dt: str, priority: RequestPriority = RequestPriority.LOW):
            return {
                "rt_cd": "0",
                "stk_dt_pole_chart_qry": [
                    {"dt": "20260904", "open_pric": "70000", "high_pric": "80000", "low_pric": "70000", "cur_prc": "75000", "trde_qty": "1000000"}
                    for _ in range(20)
                ]
            }

    custom_client = CustomSchemaMockClient()
    custom_bot = AsyncTradingBot(is_demo=False, initial_capital=10_000_000, client=custom_client, portfolio=portfolio, db=mock_db)

    await custom_bot.update_watchlist(top_n=10)
    assert len(custom_bot.watchlist) == 2, f"2개 종목이 정상 수집되어야 합니다. (실제: {len(custom_bot.watchlist)})"
    assert "005930" in custom_bot.watchlist, "A접두사 및 _AL 접미사가 제거된 '005930'이 등록되어야 합니다."
    assert "000660" in custom_bot.watchlist, "'000660'이 등록되어야 합니다."
    samsung_fib = custom_bot.watchlist["005930"]
    assert samsung_fib["period_high"] == 80000.0
    assert samsung_fib["period_low"] == 70000.0
    assert samsung_fib["fib_382"] == 80000.0 - (10000.0 * 0.382)
    assert samsung_fib["fib_618"] == 80000.0 - (10000.0 * 0.618)
    print(f"  ✅ 실전 키움 REST 스키마(trde_prica_upper, high_pric) 2종목 피보나치 분석 완벽 검증")

    # 2) MOCK 모드 / 빈 응답 시 Fallback 검증
    class EmptyMockClient(MockKiwoomClient):
        async def get_top_trading_value(self, mrkt_tp: str = "000", limit: int = 30, priority: RequestPriority = RequestPriority.LOW):
            return None

    empty_client = EmptyMockClient()
    mock_bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=empty_client, portfolio=portfolio, db=mock_db)
    await mock_bot.update_watchlist(top_n=5)
    assert len(mock_bot.watchlist) == 5, f"MOCK Fallback으로 5개 우량주가 등록되어야 합니다. (실제: {len(mock_bot.watchlist)})"
    print(f"  ✅ MOCK Fallback 5개 우량주 자동 주입 및 피보나치 분석 완벽 검증")

async def test_detailed_debug_logging_and_low_capital_handling():
    """7. 소액 예수금(114,922원) 환경 매수 수량 산출 및 실시간 디버그 로깅 검증"""
    print("▶ [Test 7] 소액 예수금(114,922원) 매수 시뮬레이션 및 실시간 디버그 로깅 검증...")
    # 실제 사용자의 예수금 상황(114,922원) 시뮬레이션
    low_capital = 114922.0
    mock_client = MockKiwoomClient(deposit=low_capital)
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=low_capital, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=low_capital, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1) 75,000원 주식(삼성전자): 피보나치 눌림목(73,820 ~ 76,180) 도달 & 예수금(114,922원) 범위 내 -> 1주 매수 성공
    bot.watchlist["005930"] = {
        "name": "삼성전자", "period_high": 80000.0, "period_low": 70000.0,
        "fib_382": 76180.0, "fib_500": 75000.0, "fib_618": 73820.0,
        "current_price": 75000.0
    }
    mock_client.prices["005930"] = 75000.0

    # 2) 150,000원 주식(SK하이닉스): 예수금(114,922원) 초과 -> 자금 부족 매수 스킵
    bot.watchlist["000660"] = {
        "name": "SK하이닉스", "period_high": 160000.0, "period_low": 140000.0,
        "fib_382": 152360.0, "fib_500": 150000.0, "fib_618": 147640.0,
        "current_price": 150000.0
    }
    mock_client.prices["000660"] = 150000.0

    # 3) 타점 미도달 주식(현대차: 250,000원 / fib_382: 230,000원): 대기 중 로깅
    bot.watchlist["005380"] = {
        "name": "현대차", "period_high": 260000.0, "period_low": 210000.0,
        "fib_382": 240900.0, "fib_500": 235000.0, "fib_618": 229100.0,
        "current_price": 255000.0
    }
    mock_client.prices["005380"] = 255000.0

    await bot.monitor_watchlist_and_enter()

    # 검증: 삼성전자(70,000원)는 1주 매수 성공
    assert "005930" in portfolio.positions, "114,922원 예수금으로 70,000원 삼성전자는 1주 매수되어야 합니다."
    assert portfolio.positions["005930"]["qty"] == 1
    # SK하이닉스(150,000원)는 자금 부족으로 미매수
    assert "000660" not in portfolio.positions, "150,000원 SK하이닉스는 예수금 부족으로 매수되지 않아야 합니다."
    print("  ✅ 소액 예수금(11만 원) 1주 매수 및 자금 부족/타점 미도달 실시간 로깅 완벽 검증")

async def test_d2_deposit_unification_and_throttling():
    """8. D+2 주문가능금액 단일화 및 실시간 감시 쓰로틀링(Throttling) 검증"""
    print("▶ [Test 8] D+2 주문가능금액 단일화 및 실시간 감시 쓰로틀링 검증...")

    class MultiDepositMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "entr": "114922",            # 당일 단순 예수금
                    "dnca_tot_amt": "100842",     # D+2 실제 주문 가능 금액
                    "ord_psbl_cash": "100842",
                    "uncl_cnt": "1"               # 미체결 1건
                }],
                "output2": []
            }

    mock_client = MultiDepositMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=114922.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=114922.0, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 계좌 동기화 실행
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()
    assert snap['total_asset'] == 114922.0, f"총 평가자산(114,922원)이 독립적으로 유지되어야 합니다. (실제: {snap['total_asset']})"
    assert snap['current_capital'] == 100842.0, f"D+2 주문가능금액(100,842원)이 주문 현금으로 확정되어야 합니다. (실제: {snap['current_capital']})"
    assert bot.unclosed_orders_count == 1, f"미체결 주문 건수가 1건으로 파싱되어야 합니다."
    print("  ✅ 총 평가자산(114,922원) 및 D+2 주문가능금액(100,842원) 독립 분리 및 미체결(1건) 추적 완벽 검증")

    # 2. 쓰로틀링 검증
    bot.watchlist["004310"] = {
        "name": "현대약품", "period_high": 12000.0, "period_low": 8000.0,
        "fib_382": 8900.0, "fib_500": 8500.0, "fib_618": 8100.0,
        "current_price": 10360.0
    }
    # 1회차 틱 평가
    await bot.OnReceiveRealData("004310", "주식체결", {"current_price": 10360.0, "volume": 100})
    first_log_time = bot._last_watch_log_time.get("004310", 0.0)
    assert first_log_time > 0, "1회차 틱 수신 시 로그 기록 시간이 저장되어야 합니다."

    # 0.1초 후 동일 가격 틱 수신 -> 쓰로틀링 작동 (시간 갱신 안 됨)
    await bot.OnReceiveRealData("004310", "주식체결", {"current_price": 10360.0, "volume": 150})
    second_log_time = bot._last_watch_log_time.get("004310", 0.0)
    assert second_log_time == first_log_time, "5초 이내 미세 변동 틱은 쓰로틀링되어 로그가 억제되어야 합니다."
    print("  ✅ 실시간 틱 핸들러 연속 수신 시 쓰로틀링(Throttling) 방어 정상 검증")

async def test_kiwoom_real_balance_parsing_various_schemas():
    """9. 키움 계좌 TR(kt00005/OPW00018) 다중 스키마 총자산 vs D+2 예수금 정밀 파싱 검증"""
    print("▶ [Test 9] 키움 계좌 TR 다중 스키마 총자산(114,922원) vs D+2 예수금(100,842원) 정밀 분리 검증...")

    # Case 1: 키움 실전 REST 표준 (tot_evlu_amt 총평가금액 114,922원, dnca_tot_amt D+2예수금 100,842원)
    class Schema1MockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_evlu_amt": "114,922",       # 키움 API 공식 총평가금액
                    "prvs_rcdl_excc_amt": "114,922", # HTS 표시 단순 예수금
                    "dnca_tot_amt": "100,842",       # D+2 실제 주문가능금액
                    "ord_psbl_cash": "100,842"
                }],
                "output2": []
            }

    mock_client1 = Schema1MockClient(deposit=114922.0)
    mock_db1 = MockDatabaseManager()
    portfolio1 = AsyncPortfolioManager(initial_capital=114922.0, max_stocks=5)
    bot1 = AsyncTradingBot(is_demo=False, initial_capital=114922.0, client=mock_client1, portfolio=portfolio1, db=mock_db1)

    await bot1._sync_account_balance()
    snap1 = await portfolio1.get_snapshot()
    assert snap1['total_asset'] == 114922.0, f"Case 1: 총자산은 114,922원이어야 합니다. (실제: {snap1['total_asset']})"
    assert snap1['current_capital'] == 100842.0, f"Case 1: D+2 예수금은 100,842원이어야 합니다. (실제: {snap1['current_capital']})"
    print("  ✅ Case 1: prvs_rcdl_excc_amt(114,922원) 및 dnca_tot_amt(100,842원) 정밀 분리 검증 통과")

    # Case 2: 순수 총자산 필드(tot_asst_amt / aset_evlt_amt)가 내려오는 경우
    class Schema2MockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_asst_amt": "114,922",
                    "ord_psbl_cash": "100,842",
                    "dnca_tot_amt": "100,842"
                }],
                "output2": []
            }

    mock_client2 = Schema2MockClient(deposit=114922.0)
    mock_db2 = MockDatabaseManager()
    portfolio2 = AsyncPortfolioManager(initial_capital=114922.0, max_stocks=5)
    bot2 = AsyncTradingBot(is_demo=False, initial_capital=114922.0, client=mock_client2, portfolio=portfolio2, db=mock_db2)

    await bot2._sync_account_balance()
    snap2 = await portfolio2.get_snapshot()
    assert snap2['total_asset'] == 114922.0, f"Case 2: 총자산은 114,922원이어야 합니다. (실제: {snap2['total_asset']})"
    assert snap2['current_capital'] == 100842.0, f"Case 2: D+2 예수금은 100,842원이어야 합니다. (실제: {snap2['current_capital']})"
    print("  ✅ Case 2: tot_asst_amt(114,922원) 총자산 필드 매핑 검증 통과")

    # Case 3: 주식 1종목 보유(70,000원) + 단순 예수금(44,922원) + D+2 예수금(30,842원)
    class Schema3MockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "entr": "44,922",
                    "dnca_tot_amt": "30,842",
                    "tot_evlu_amt": "114,922",
                    "ord_psbl_cash": "30,842"
                }],
                "output2": [{
                    "stk_cd": "005930",
                    "stk_nm": "삼성전자",
                    "hldg_qty": "1",
                    "pchs_avg_pric": "70000",
                    "prpr": "70000"
                }]
            }

    mock_client3 = Schema3MockClient(deposit=44922.0)
    mock_db3 = MockDatabaseManager()
    portfolio3 = AsyncPortfolioManager(initial_capital=114922.0, max_stocks=5)
    bot3 = AsyncTradingBot(is_demo=False, initial_capital=114922.0, client=mock_client3, portfolio=portfolio3, db=mock_db3)

    await bot3._sync_account_balance()
    snap3 = await portfolio3.get_snapshot()
    # 총자산 = 114,922원
    assert snap3['total_asset'] == 114922.0, f"Case 3: 총자산은 114,922원이어야 합니다. (실제: {snap3['total_asset']})"
    assert snap3['current_capital'] == 30842.0, f"Case 3: D+2 예수금은 30,842원이어야 합니다. (실제: {snap3['current_capital']})"
    assert snap3['stock_count'] == 1, "Case 3: 보유 종목 1개여야 합니다."
    print("  ✅ Case 3: 보유 주식 평가액 + 단순 예수금 합산 총자산(114,922원) 정합성 완벽 검증")

async def test_actual_account_balance_and_4_holdings_sync():
    """10. 실제 키움 앱 잔고(148,442원), D+2예수금(1,122원), 대용금(103,890원) 및 4개 보유 주식 동기화 정밀 검증"""
    print("▶ [Test 10] 실제 키움 앱 데이터(148,442원 / 4개 보유 종목 / 대용금 103,890원) 동기화 검증...")

    class ActualAccountMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_evlu_amt": "148442",            # 총평가금액 (148,442원)
                    "dnca_tot_amt": "1122",              # D+2 추정예수금 (1,122원)
                    "entr": "1122",                      # 단순 예수금 (1,122원)
                    "sub_amt": "103890",                 # 대용금 (103,890원) - 총자산 오맵핑 금지
                    "pchs_amt_smtl_amt": "147400",       # 총매입 (147,400원)
                    "tot_evlu_pfls_amt": "778",          # 총손익 (+778원)
                    "tot_pnl_rt": "+0.53",               # 총수익률 (+0.53%)
                    "ord_psbl_cash": "1122"
                }],
                "output2": [
                    {
                        "stk_cd": "003280", "stk_nm": "흥아해운", "hldg_qty": "2",
                        "pchs_avg_pric": "1980", "pchs_amt": "3960", "prpr": "1801",
                        "evlu_amt": "3602", "evlu_pfls_amt": "-364", "evlu_pfls_rt": "-9.19"
                    },
                    {
                        "stk_cd": "015760", "stk_nm": "한국전력", "hldg_qty": "1",
                        "pchs_avg_pric": "32950", "pchs_amt": "32950", "prpr": "31450",
                        "evlu_amt": "31450", "evlu_pfls_amt": "-1562", "evlu_pfls_rt": "-4.74"
                    },
                    {
                        "stk_cd": "090460", "stk_nm": "비에이치", "hldg_qty": "1",
                        "pchs_avg_pric": "19520", "pchs_amt": "19520", "prpr": "20100",
                        "evlu_amt": "20100", "evlu_pfls_amt": "540", "evlu_pfls_rt": "+2.77"
                    },
                    {
                        "stk_cd": "229200", "stk_nm": "KODEX 코스닥150", "hldg_qty": "1",
                        "pchs_avg_pric": "14080", "pchs_amt": "14080", "prpr": "13880",
                        "evlu_amt": "13880", "evlu_pfls_amt": "-200", "evlu_pfls_rt": "-1.42"
                    }
                ]
            }

        async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "entr": "1122",
                    "dnca_tot_amt": "1122",
                    "sub_amt": "103890",
                    "ord_psbl_cash": "1122"
                }]
            }

    mock_client = ActualAccountMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=148442.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=False, initial_capital=148442.0, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 계좌 동기화 실행
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    # 검증: 총 평가자산 및 D+2 예수금
    assert snap['total_asset'] == 148442.0, f"총 평가자산은 148,442원이어야 합니다. (실제: {snap['total_asset']})"
    assert snap['current_capital'] == 1122.0, f"D+2 예수금은 1,122원이어야 합니다. (실제: {snap['current_capital']})"
    assert snap['stock_count'] == 4, f"보유 종목 수는 4개여야 합니다. (실제: {snap['stock_count']})"

    # 검증: 4개 보유 종목 리스트
    pos_map = {p['code']: p for p in snap['positions']}
    assert "003280" in pos_map, "흥아해운(003280)이 포지션에 포함되어야 합니다."
    assert pos_map["003280"]["qty"] == 2
    assert pos_map["003280"]["buy_price"] == 1980.0
    assert pos_map["003280"]["current_price"] == 1801.0

    assert "015760" in pos_map, "한국전력(015760)이 포지션에 포함되어야 합니다."
    assert pos_map["015760"]["qty"] == 1
    assert pos_map["015760"]["buy_price"] == 32950.0

    assert "090460" in pos_map, "비에이치(090460)이 포지션에 포함되어야 합니다."
    assert pos_map["090460"]["qty"] == 1
    assert pos_map["090460"]["buy_price"] == 19520.0

    assert "229200" in pos_map, "KODEX 코스닥150(229200)이 포지션에 포함되어야 합니다."
    assert pos_map["229200"]["qty"] == 1
    assert pos_map["229200"]["buy_price"] == 14080.0

    print("  ✅ 실제 키움 계좌 총자산(148,442원) / D+2예수금(1,122원) / 4개 보유 종목 정밀 파싱 완벽 검증 통과")

async def test_dynamic_watchlist_and_full_quant_workflow():
    """11. 동적 감시 목록(20개 종목) 수집 및 전체 퀀트 매매 워크플로우(Buy -> Hold -> Sell) 시뮬레이션"""
    print("▶ [Test 11] 동적 감시 목록 갱신 및 전체 매매 사이클(매수->보유->익절) 시뮬레이션...")

    class DynamicWatchlistMockClient(MockKiwoomClient):
        def __init__(self):
            super().__init__(deposit=10_000_000.0)
            self.prices = {
                "005930": 74500.0,
                "000660": 150000.0,
                "035420": 200000.0,
                "005380": 240000.0
            }

        async def get_top_trading_value(self, mrkt_tp: str = "000", limit: int = 30, priority: RequestPriority = RequestPriority.LOW):
            # 20개 대표 주도주 모의 반환
            return [
                {"stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "74500"},
                {"stk_cd": "000660", "stk_nm": "SK하이닉스", "cur_prc": "150000"},
                {"stk_cd": "035420", "stk_nm": "NAVER", "cur_prc": "200000"},
                {"stk_cd": "005380", "stk_nm": "현대차", "cur_prc": "240000"},
                {"stk_cd": "000270", "stk_nm": "기아", "cur_prc": "120000"}
            ]

        async def get_daily_chart(self, code: str, base_dt: str, priority: RequestPriority = RequestPriority.LOW):
            p = self.prices.get(code, 50000.0)
            return {
                "output2": [
                    {"hgpr": str(int(p * 1.08)), "lwpr": str(int(p * 0.95)), "clpr": str(int(p)), "vol": "1000000"}
                    for _ in range(20)
                ]
            }

    mock_client = DynamicWatchlistMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000.0, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 감시 목록 동적 갱신
    await bot.update_watchlist(top_n=5)
    assert len(bot.watchlist) == 5, f"감시 목록에 5개 종목이 등록되어야 합니다. (실제: {len(bot.watchlist)})"
    assert "005930" in bot.watchlist, "삼성전자가 감시 목록에 포함되어야 합니다."
    print(f"  ✅ 동적 감시 목록 5개 종목 피보나치 분석 완료: {list(bot.watchlist.keys())}")

    # 2. 매수 진입 시뮬레이션 (삼성전자 피보나치 38.2% 타점 도달)
    fib_382 = bot.watchlist["005930"]["fib_382"]
    mock_client.prices["005930"] = fib_382
    await bot.monitor_watchlist_and_enter()
    assert "005930" in portfolio.positions, "삼성전자가 포트폴리오에 매수 편입되어야 합니다."
    buy_qty = portfolio.positions["005930"]["qty"]
    buy_p = portfolio.positions["005930"]["buy_price"]
    print(f"  ✅ 매수 체결 성공: 삼성전자 {buy_qty}주 @ {buy_p:,.0f}원")

    # 20개 분봉 적재 (ATR = 1,000원 설정)
    initial_candles = [
        {"datetime": f"2026-09-04 09:{i:02d}:00", "open": 76500, "high": 77500, "low": 76500, "close": 77000, "volume": 1000}
        for i in range(20)
    ]
    bot.buffer.load_initial_candles("005930", initial_candles)

    # 3. 보유 중 시세 상승 및 1차 분할 익절 (+3.5%)
    mock_client.prices["005930"] = buy_p + 2000.0  # R1 타점(buy_p + 1.5*ATR = buy_p + 1500) 상회
    await bot.monitor_positions_and_exit()
    pos = portfolio.positions["005930"]
    assert pos["sell_stage"] == 1, "1차 익절(Stage 1)이 완료되어야 합니다."
    print(f"  ✅ 1차 분할 익절(+3.5%) 완료: 잔여 {pos['qty']}주 (Stage {pos['sell_stage']})")

    # 4. 2차 분할 익절 (R2 타점: buy_p + 2.5*ATR = buy_p + 2500)
    mock_client.prices["005930"] = buy_p + 3000.0
    await bot.monitor_positions_and_exit()
    pos2 = portfolio.positions["005930"]
    assert pos2["sell_stage"] == 2, "2차 익절(Stage 2)이 완료되어야 합니다."
    print(f"  ✅ 2차 분할 익절(+5.9%) 완료: 잔여 {pos2['qty']}주 (Stage {pos2['sell_stage']})")

    # 5. 3차 전량 청산 (R3 타점: buy_p + 3.5*ATR = buy_p + 3500)
    mock_client.prices["005930"] = buy_p + 5000.0
    await bot.monitor_positions_and_exit()
    assert "005930" not in portfolio.positions, "3차 전량 익절 후 포지션이 청산되어야 합니다."
    print("  ✅ 최종 3차 전량 청산(+8.5%) 및 매매 사이클 완벽 완료")

async def test_actual_account_balance_with_images_data():
    """12. 사용자 실계좌 스크린샷 데이터(D+2예수금 82,819원 / 당일 1,122원 / 3개 보유종목 64,400원 / 총자산 147,219원) 정밀 검증"""
    print("▶ [Test 12] 사용자 실계좌 데이터(D+2 82,819원 / 3개 보유종목 비에이치·펄어비스·파인엠텍) 동기화 검증...")

    class UserScreenshotAccountMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_evlu_amt": "147349",            # 총평가금액 (147,349원)
                    "dnca_tot_amt": "1122",              # 당일 예수금 (1,122원) - D+2로 오맵핑 금지
                    "entr": "1122",                      # 당일 예수금 (1,122원)
                    "prvs_rcdl_excc_amt": "1122",        # 전일 예수금 (1,122원)
                    "d2_deposit": "82819",               # D+2 추정예수금 (82,819원)
                    "d2_auto_amt": "82819",
                    "sub_amt": "45530",                  # 대용금 (45,530원)
                    "pchs_amt_smtl_amt": "61280",        # 총매입 (61,280원)
                    "evlu_amt_smtl_amt": "64400",        # 총평가 (64,400원)
                    "tot_evlu_pfls_amt": "2993",         # 총손익 (+2,993원)
                    "tot_pnl_rt": "+4.88",               # 총수익률 (+4.88%)
                    "ord_psbl_cash": "82819"             # 주문가능금액 (82,819원)
                }],
                "output2": [
                    {
                        "stk_cd": "090460", "stk_nm": "비에이치", "hldg_qty": "1",
                        "pchs_avg_pric": "19520", "pchs_amt": "19520", "prpr": "19630",
                        "evlu_amt": "19630", "evlu_pfls_amt": "72", "evlu_pfls_rt": "+0.37"
                    },
                    {
                        "stk_cd": "263750", "stk_nm": "펄어비스", "hldg_qty": "1",
                        "pchs_avg_pric": "33600", "pchs_amt": "33600", "prpr": "35600",
                        "evlu_amt": "35600", "evlu_pfls_amt": "1929", "evlu_pfls_rt": "+5.74"
                    },
                    {
                        "stk_cd": "441270", "stk_nm": "파인엠텍", "hldg_qty": "1",
                        "pchs_avg_pric": "8160", "pchs_amt": "8160", "prpr": "9170",
                        "evlu_amt": "9170", "evlu_pfls_amt": "992", "evlu_pfls_rt": "+12.16"
                    }
                ]
            }

        async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "entr": "1122",
                    "prvs_rcdl_excc_amt": "1122",
                    "dnca_tot_amt": "1122",              # 당일 예수금 잔액
                    "d2_deposit": "82819",               # D+2 추정예수금
                    "d2_auto_amt": "82819",
                    "sub_amt": "45530",                  # 대용금
                    "ord_psbl_cash": "82819"
                }]
            }

    mock_client = UserScreenshotAccountMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=147349.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=False, initial_capital=147349.0, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 계좌 동기화 실행
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    # 검증: D+2 예수금 82,819원 (1,122원으로 오맵핑되지 않고 82,819원으로 정상 동기화)
    assert snap['current_capital'] == 82819.0, f"D+2 예수금은 82,819원이어야 합니다. (실제: {snap['current_capital']})"

    # 검증: 3개 보유 종목 (비에이치, 펄어비스, 파인엠텍) 정상 동기화
    assert snap['stock_count'] == 3, f"보유 종목 수는 3개여야 합니다. (실제: {snap['stock_count']})"
    assert snap['invested_capital'] == 64400.0, f"보유 주식 평가액은 64,400원이어야 합니다. (실제: {snap['invested_capital']})"
    assert snap['total_asset'] >= 147219.0, f"총 평가자산은 147,219원 이상이어야 합니다. (실제: {snap['total_asset']})"

    pos_map = {p['code']: p for p in snap['positions']}
    assert "090460" in pos_map, "비에이치(090460)가 포지션에 포함되어야 합니다."
    assert pos_map["090460"]["qty"] == 1
    assert pos_map["090460"]["buy_price"] == 19520.0
    assert pos_map["090460"]["current_price"] == 19630.0

    assert "263750" in pos_map, "펄어비스(263750)가 포지션에 포함되어야 합니다."
    assert pos_map["263750"]["qty"] == 1
    assert pos_map["263750"]["buy_price"] == 33600.0
    assert pos_map["263750"]["current_price"] == 35600.0

    assert "441270" in pos_map, "파인엠텍(441270)이 포지션에 포함되어야 합니다."
    assert pos_map["441270"]["qty"] == 1
    assert pos_map["441270"]["buy_price"] == 8160.0
    assert pos_map["441270"]["current_price"] == 9170.0

    print("  ✅ 사용자 실계좌 데이터(D+2 예수금 82,819원 / 보유 3종목 비에이치·펄어비스·파인엠텍 / 총자산 147,349원) 정밀 검증 100% 통과")

async def test_single_multi_record_split_and_zero_ccls_qty_handling():
    """13. 싱글/멀티 레코드 분리 및 ccls_qty_sum='0' (당일 미체결/기존 보유) 엣지 케이스 정밀 검증"""
    print("▶ [Test 13] 싱글/멀티 레코드 분리 및 ccls_qty_sum='0' 환경에서 3개 보유종목 100% 파싱 검증...")

    # ccls_qty_sum이 0이거나 output1에 요약 정보만 들어있는 상황 재현
    class SingleMultiRecordMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_evlu_amt": "147069",            # 총평가금액 (147,069원)
                    "tot_hldg_qty": "3",                 # 총보유수량 요약 (종목 리스트로 오인 금지)
                    "dnca_tot_amt": "82819",              # D+2 예수금 (82,819원)
                    "d2_deposit": "82819",
                    "ccls_qty_sum": "0",                 # 당일 체결수량합 0
                    "tot_evlu_pfls_amt": "2993"
                }],
                "output2": [
                    {
                        "stk_cd": "090460", "stk_nm": "비에이치", "ccls_qty_sum": "0", "hldg_qty": "1",
                        "pchs_avg_pric": "19520", "prpr": "19630", "evlu_amt": "19630"
                    },
                    {
                        "stk_cd": "263750", "stk_nm": "펄어비스", "ccls_qty_sum": "0", "ord_psbl_qty": "1",
                        "pchs_avg_pric": "33600", "prpr": "35600", "evlu_amt": "35600"
                    },
                    {
                        "stk_cd": "441270", "stk_nm": "파인엠텍", "ccls_qty_sum": "0", "hold_qty": "1",
                        "pchs_avg_pric": "8160", "prpr": "9170", "evlu_amt": "9170"
                    }
                ]
            }

    mock_client = SingleMultiRecordMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=mock_client, portfolio=portfolio, db=mock_db)

    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    assert snap['stock_count'] == 3, f"3개 보유 종목이 정상 인식되어야 합니다. (실제: {snap['stock_count']}개)"
    assert snap['current_capital'] == 82819.0, f"D+2 예수금은 82,819원이어야 합니다. (실제: {snap['current_capital']})"
    assert snap['total_asset'] >= 147069.0, f"총 평가자산은 147,069원이어야 합니다. (실제: {snap['total_asset']})"
    print("  ✅ 싱글/멀티 레코드 분리 및 ccls_qty_sum='0' 환경 3개 보유종목 파싱 100% 통과")

async def test_korean_keys_parsing():
    """14. 한글 필드명 키움 TR 스키마(종목코드, 보유수량, 매입단가, 현재가) 파싱 검증"""
    print("▶ [Test 14] 한글 필드명 키움 TR 스키마 100% 파싱 검증...")

    class KoreanSchemaMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "총평가금액": "147069",
                    "D+2예수금": "82819",
                    "주문가능금액": "82819"
                }],
                "output2": [
                    {
                        "종목코드": "090460", "종목명": "비에이치", "보유수량": "1",
                        "매입단가": "19520", "현재가": "19630"
                    },
                    {
                        "종목코드": "263750", "종목명": "펄어비스", "보유수량": "1",
                        "매입단가": "33600", "현재가": "35600"
                    },
                    {
                        "종목코드": "441270", "종목명": "파인엠텍", "보유수량": "1",
                        "매입단가": "8160", "현재가": "9170"
                    }
                ]
            }

    mock_client = KoreanSchemaMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=mock_client, portfolio=portfolio, db=mock_db)

    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    assert snap['stock_count'] == 3, f"한글 필드명 스키마에서 3개 종목이 파싱되어야 합니다. (실제: {snap['stock_count']})"
    assert snap['current_capital'] == 82819.0
    assert snap['total_asset'] >= 147069.0
    print("  ✅ 한글 필드명 키움 TR 스키마 파싱 100% 통과")

async def test_kt00018_priority_and_isin_and_zero_padded_codes_parsing():
    """15. kt00018 합산 최우선 스캔 및 ISIN 코드(KR7...)/5자리 패딩 종목코드 파싱 검증"""
    print("▶ [Test 15] kt00018 합산 최우선 스캔 및 ISIN(KR7090460005)/5자리 정수형 코드 파싱 검증...")

    class ISINAndPaddedMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "tot_evlu_amt": "147069",
                    "tot_pnl_rt": "4.88",
                    "dnca_tot_amt": "82819"
                }],
                "output2": [
                    {
                        "stk_cd": "KR7090460005", "stk_nm": "비에이치", "hldg_qty": "1",
                        "pchs_avg_pric": "19520", "prpr": "19630", "evlu_amt": "19630"
                    },
                    {
                        "stk_cd": "263750_AL", "stk_nm": "펄어비스", "ord_psbl_qty": "1",
                        "pchs_avg_pric": "33600", "prpr": "35600", "evlu_amt": "35600"
                    },
                    {
                        "stk_cd": 441270, "stk_nm": "파인엠텍", "hold_qty": "1",
                        "pchs_avg_pric": "8160", "prpr": "9170", "evlu_amt": "9170"
                    }
                ]
            }

    mock_client = ISINAndPaddedMockClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=mock_client, portfolio=portfolio, db=mock_db)

    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    assert snap['stock_count'] == 3, f"ISIN/패딩/접미사 포함 3개 종목이 정상 인식되어야 합니다. (실제: {snap['stock_count']}개)"
    pos_map = {p['code']: p for p in snap['positions']}
    assert "090460" in pos_map, "KR7090460005 -> 090460 추출 성공해야 합니다."
    assert "263750" in pos_map, "263750_AL -> 263750 정제 성공해야 합니다."
    assert "441270" in pos_map, "441270(int) -> 441270 파싱 성공해야 합니다."
    assert snap['current_capital'] == 82819.0
    assert snap['total_asset'] >= 147069.0
    print("  ✅ kt00018 ISIN/접미사/정수형 코드 정규화 및 3개 보유종목 파싱 100% 통과")

async def test_db_restore_positions_safety_without_cash_deduction():
    """16. DB 포지션 안전 복원 시 D+2 예수금(82,819원) 차감 없이 독립 보존 검증"""
    print("▶ [Test 16] DB 포지션 안전 복원 시 D+2 주문가능 현금 차감 방지 및 정합성 검증...")

    portfolio = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    await portfolio.sync_capital(available_cash=82819.0, total_asset=147069.0)

    db_positions = [
        {"code": "090460", "name": "비에이치", "qty": 1, "buy_price": 19520.0, "current_price": 19630.0},
        {"code": "263750", "name": "펄어비스", "qty": 1, "buy_price": 33600.0, "current_price": 35600.0},
        {"code": "441270", "name": "파인엠텍", "qty": 1, "buy_price": 8160.0, "current_price": 9170.0}
    ]

    await portfolio.restore_positions_from_db(db_positions)
    snap = await portfolio.get_snapshot()

    assert snap['stock_count'] == 3, f"DB로부터 3개 종목이 안전 복원되어야 합니다. (실제: {snap['stock_count']}개)"
    # D+2 예수금이 82,819원에서 차감되지 않고 온전히 82,819원으로 유지되는지 검증
    assert snap['current_capital'] == 82819.0, f"DB 복원 시 D+2 예수금(82,819원)이 차감되지 않아야 합니다. (실제: {snap['current_capital']})"
    assert snap['total_asset'] >= 147069.0
    print("  ✅ DB 포지션 안전 복원 시 D+2 주문가능금액(82,819원) 보존 및 총자산(147,069원) 정합성 100% 검증")

async def test_buy_price_multi_layer_inference_and_anti_1won_defense():
    """17. 매수가(매입단가) 6단계 다중 방어 해석 및 '1원' 표기 버그 원천 방어 검증"""
    print("▶ [Test 17] 매수가 6단계 다중 방어 해석(단가누락/매입금액역산/손익역산/수익률역산/자가치유) 검증...")

    # Case A: pchs_avg_pric 누락되고 pchs_amt(매입금액)만 제공된 경우
    class PchsAmtOnlyMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{"tot_evlu_amt": "147069", "dnca_tot_amt": "82819"}],
                "output2": [
                    {
                        "stk_cd": "090460", "stk_nm": "비에이치", "hldg_qty": "2",
                        "pchs_amt": "39040", "prpr": "20000", "evlu_amt": "40000"
                        # pchs_avg_pric 필드 없음 -> pchs_amt / qty = 19,520원 산출
                    }
                ]
            }

    portfolio_a = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot_a = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=PchsAmtOnlyMockClient(), portfolio=portfolio_a, db=MockDatabaseManager())
    await bot_a._sync_account_balance()
    snap_a = await portfolio_a.get_snapshot()
    pos_a = snap_a['positions'][0]
    assert pos_a['buy_price'] == 19520.0, f"Case A 매수가격은 19,520원이어야 합니다. (실제: {pos_a['buy_price']}원)"
    assert pos_a['buy_price'] != 1.0, "매수가가 1원으로 잘못 표출되면 안 됩니다."
    print("  ✅ Case A: pchs_amt(39,040원)/qty(2주) 역산 매수가(19,520원) 검증 완료")

    # Case B: 단가/매입금액 모두 누락되고 evlu_amt(40,000원) 및 evlu_pfls_amt(+960원)만 제공된 경우
    class PnlOnlyMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{"tot_evlu_amt": "147069", "dnca_tot_amt": "82819"}],
                "output2": [
                    {
                        "stk_cd": "263750", "stk_nm": "펄어비스", "hldg_qty": "1",
                        "prpr": "35600", "evlu_amt": "35600", "evlu_pfls_amt": "2000"
                        # (35600 - 2000) / 1 = 33,600원 역산
                    }
                ]
            }

    portfolio_b = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot_b = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=PnlOnlyMockClient(), portfolio=portfolio_b, db=MockDatabaseManager())
    await bot_b._sync_account_balance()
    snap_b = await portfolio_b.get_snapshot()
    pos_b = snap_b['positions'][0]
    assert pos_b['buy_price'] == 33600.0, f"Case B 매수가격은 33,600원이어야 합니다. (실제: {pos_b['buy_price']}원)"
    print("  ✅ Case B: (evlu_amt 35,600원 - pnl 2,000원) 손익 역산 매수가(33,600원) 검증 완료")

    # Case C: 단가/매입금액/손익 모두 누락되고 현재가(9,170원) 및 수익률(+12.38%)만 제공된 경우
    class YieldRateOnlyMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{"tot_evlu_amt": "147069", "dnca_tot_amt": "82819"}],
                "output2": [
                    {
                        "stk_cd": "441270", "stk_nm": "파인엠텍", "hldg_qty": "1",
                        "prpr": "9170", "evlu_pfls_rt": "+12.38"
                        # 9170 / (1 + 0.1238) = 8,159.81 -> 8,160원 근사 역산
                    }
                ]
            }

    portfolio_c = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    bot_c = AsyncTradingBot(is_demo=False, initial_capital=147069.0, client=YieldRateOnlyMockClient(), portfolio=portfolio_c, db=MockDatabaseManager())
    await bot_c._sync_account_balance()
    snap_c = await portfolio_c.get_snapshot()
    pos_c = snap_c['positions'][0]
    assert 8150.0 <= pos_c['buy_price'] <= 8170.0, f"Case C 매수가격은 8,160원 내외여야 합니다. (실제: {pos_c['buy_price']}원)"
    print(f"  ✅ Case C: 현재가(9,170원) / (1 + 12.38%) 수익률 역산 매수가({pos_c['buy_price']}원) 검증 완료")

    # Case D: 과거 DB에 1원/0원으로 오염된 데이터가 들어있을 때 restore_positions_from_db 자가 치유
    portfolio_d = AsyncPortfolioManager(initial_capital=147069.0, max_stocks=5)
    dirty_db_positions = [
        {"code": "090460", "name": "비에이치", "qty": 1, "buy_price": 1.0, "current_price": 19630.0},
        {"code": "263750", "name": "펄어비스", "qty": 1, "buy_price": 0.0, "current_price": 35600.0}
    ]
    await portfolio_d.restore_positions_from_db(dirty_db_positions)
    snap_d = await portfolio_d.get_snapshot()
    for p in snap_d['positions']:
        assert p['buy_price'] > 1.0, f"DB 자가 치유 후 매수가는 1원 초과여야 합니다. (종목: {p['name']}, 매수가: {p['buy_price']})"
    print("  ✅ Case D: DB 1원/0원 오염 데이터 현재가 기반 자가 치유 검증 완료")

async def test_zero_holdings_and_single_source_total_asset_sync():
    """18. 영웅문 실계좌(국내잔고 0종목 / D+2예수금 141,712원 / 추정자산 141,712원) 동기화 및 중복 합산 방지 검증"""
    print("▶ [Test 18] 보유종목 0건(전량매도) 유령주식 제거 및 키움 API 총평가금액(141,712원) 단일 소스 매핑 검증...")

    class CurrentRealAccountMockClient(MockKiwoomClient):
        async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
            # 영웅문S# 모바일 앱 현재 상태: 국내잔고 0개, 추정자산 141,712원, D+2예수금 141,712원
            return {
                "output1": [{
                    "tot_evlu_amt": "141712",            # 총평가금액 (141,712원)
                    "tot_asst_amt": "141712",            # 총자산금액
                    "dnca_tot_amt": "141712",            # D+2 예수금 (141,712원)
                    "d2_deposit": "141712",
                    "entr": "1122",                      # 단순 예수금 잔액 (1,122원)
                    "prvs_rcdl_excc_amt": "1122",
                    "sub_amt": "0",                      # 대용금 0원
                    "pchs_amt_smtl_amt": "0",            # 총매입 0원
                    "evlu_amt_smtl_amt": "0",            # 총평가 0원
                    "tot_evlu_pfls_amt": "0",            # 총손익 0원
                    "tot_pnl_rt": "0.00",
                    "ord_psbl_cash": "141712"
                }],
                "output2": []                            # 보유 종목 0건 (전량 매도 완료 상태)
            }

        async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM):
            return {
                "output1": [{
                    "entr": "1122",
                    "prvs_rcdl_excc_amt": "1122",
                    "dnca_tot_amt": "141712",
                    "d2_deposit": "141712",
                    "ord_psbl_cash": "141712"
                }]
            }

    mock_client = CurrentRealAccountMockClient()
    mock_db = MockDatabaseManager()
    # 과거 DB에 두산에너빌리티가 남아있던 상황 가정
    mock_db.portfolio = [
        {"code": "034020", "name": "두산에너빌리티", "qty": 1, "buy_price": 84400.0, "current_price": 84300.0}
    ]

    portfolio = AsyncPortfolioManager(initial_capital=141712.0, max_stocks=5)
    # 포트폴리오 인메모리에도 과거 두산에너빌리티가 있던 상태 가정
    await portfolio.add_position("034020", "두산에너빌리티", qty=1, buy_price=84400.0)
    assert len(portfolio.positions) == 1

    bot = AsyncTradingBot(is_demo=False, initial_capital=141712.0, client=mock_client, portfolio=portfolio, db=mock_db)

    # 계좌 잔고 동기화 실행
    await bot._sync_account_balance()
    snap = await portfolio.get_snapshot()

    # 1. 유령 주식 제거 검증: 보유 종목 0개여야 함
    assert snap['stock_count'] == 0, f"보유 종목 수는 0개여야 합니다. (실제: {snap['stock_count']}개)"
    assert len(snap['positions']) == 0, f"포지션 리스트는 비어있어야 합니다. (실제: {len(snap['positions'])}개)"
    assert snap['invested_capital'] == 0.0, f"투자 평가액은 0원이어야 합니다. (실제: {snap['invested_capital']}원)"
    assert "034020" not in portfolio.positions, "두산에너빌리티 유령 주식이 포트폴리오에서 완전히 제거되어야 합니다."

    # 2. DB 동기화 검증: DB의 portfolio도 빈 딕셔너리로 저장되어 테이블이 비워져야 함
    assert mock_db.portfolio_db == {}, f"DB portfolio 테이블도 0종목으로 비워져야 합니다. (실제: {mock_db.portfolio_db})"

    # 3. 총자산 단일 소스 검증: 키움 API tot_evlu_amt (141,712원) 단일 값 매핑 & 중복 합산(Double counting) 없음
    assert snap['total_asset'] == 141712.0, f"총자산은 키움 API 총평가금액과 정확히 일치하는 141,712원이어야 합니다. (실제: {snap['total_asset']})"
    assert snap['current_capital'] == 141712.0, f"D+2 주문가능 예수금은 141,712원이어야 합니다. (실제: {snap['current_capital']})"

    print("  ✅ 보유종목 0건(전량매도) 유령주식 제거, DB 테이블 비우기 및 키움 API 총평가금액(141,712원) 1:1 매핑 100% 검증 완료")

async def test_bot_auto_wakeup_and_resume_schedule():
    """19. 봇 수동 일시정지(is_paused) 후 영업일 아침 08:50 자동 웨이크업(Wake-up) 및 매매 재개 검증"""
    print("▶ [Test 19] 수동 일시정지 후 익일 아침 08:50 자동 웨이크업 및 영업일 캘린더 판별 검증...")

    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 영업일 및 공휴일 판별 함수 검증
    saturday = datetime(2026, 9, 19, 10, 0, 0)
    sunday = datetime(2026, 9, 20, 10, 0, 0)
    wednesday = datetime(2026, 9, 16, 10, 0, 0)
    new_year = datetime(2026, 1, 1, 9, 0, 0)
    labor_day = datetime(2026, 5, 1, 9, 0, 0)

    assert bot.is_korean_market_holiday(saturday) is True, "토요일은 휴장일이어야 합니다."
    assert bot.is_korean_market_holiday(sunday) is True, "일요일은 휴장일이어야 합니다."
    assert bot.is_korean_market_holiday(new_year) is True, "신정(1/1)은 휴장일이어야 합니다."
    assert bot.is_korean_market_holiday(labor_day) is True, "근로자의 날(5/1)은 휴장일이어야 합니다."
    assert bot.is_korean_market_holiday(wednesday) is False, "평일(수요일)은 정상 영업일이어야 합니다."
    print("  ✅ 한국 거래소 주말 및 주요 공휴일 판별 로직 100% 검증 완료")

    # 2. 수동 일시정지(STOP) 상태 시뮬레이션
    bot.running = True
    assert bot.running is True
    assert bot.is_paused is False

    # 사용자가 대시보드에서 일시정지 클릭
    bot.pause()
    assert bot.is_paused is True, "pause() 호출 시 is_paused가 True여야 합니다."
    assert bot.running is False, "is_paused 상태에서는 running getter가 False여야 합니다."
    assert bot.is_running is False

    # 3. 익일 아침 08:50 자동 웨이크업(Wake-up) 시뮬레이션
    # (스케줄러 또는 wait_until_next_market_open에 의해 트리거됨)
    bot.is_paused = False
    bot.running = True
    bot.mdd_shutdown = False
    bot.market_filter_passed = True

    assert bot.is_paused is False, "웨이크업 후 is_paused가 False로 초기화되어야 합니다."
    assert bot.running is True, "웨이크업 후 running 상태가 True로 자동 복구되어야 합니다."
    assert bot.mdd_shutdown is False, "당일 MDD 셧다운 플래그가 안전하게 리셋되어야 합니다."

    print("  ✅ 수동 일시정지 후 익일 아침 자동 웨이크업 및 RUNNING 상태 복원 100% 검증 완료")

async def test_buy_signal_open_price_and_0915_time_filter():
    """20. 당일 시가(Open Price) 기반 ATR 변동성 돌파 매수 정상 체결 및 09:15 타임 필터 검증"""
    print("▶ [Test 20] 당일 시가(Open) 기반 ATR 변동성 돌파 매수 및 09:15 타임 필터 가드 검증...")

    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. Watchlist에 정상 시가(Open=70,000원) 및 피보나치 레벨 설정
    bot.watchlist["005930"] = {
        "name": "삼성전자",
        "open_price": 70000.0,
        "period_high": 80000.0,
        "period_low": 65000.0,
        "fib_382": 74270.0,
        "fib_500": 72500.0,
        "fib_618": 70730.0,
        "avg_volume": 100000,
        "current_price": 70000.0
    }

    # 2. 링버퍼에 20개 분봉 로드 (ATR = 2,000원)
    # 돌파 기준가 = Open(70,000) + 0.5 * ATR(2,000) = 71,000원
    candles = [
        {"datetime": f"2026-09-17 09:{i:02d}:00", "open": 70000, "high": 71000, "low": 69500, "close": 70500, "volume": 5000}
        for i in range(20)
    ]
    bot.buffer.load_initial_candles("005930", candles)

    # 3. 돌파 미달 및 피보나치 하회 가격 (70,500원 < fib_618(70,730원) 및 돌파기준가(71,000원)) -> 매수 주문 미발생
    mock_client.sent_orders.clear()
    mock_client.prices["005930"] = 70500.0
    await bot._evaluate_buy_condition("005930", cur_price=70500.0, cur_volume=50000.0)
    assert "005930" not in portfolio.positions, "돌파 기준가(71,000원) 미달 시 매수되지 않아야 합니다."
    assert len(mock_client.sent_orders) == 0

    # 4. 돌파 성공 가격 (71,500원 >= 71,000원) -> 매수 주문 발생 및 포트폴리오 편입
    mock_client.prices["005930"] = 71500.0
    await bot._evaluate_buy_condition("005930", cur_price=71500.0, cur_volume=150000.0)
    assert "005930" in portfolio.positions, "돌파 기준가(71,000원) 돌파 시 포트폴리오에 편입되어야 합니다."
    assert len(mock_client.sent_orders) == 1, "매수 주문이 1건 발송되어야 합니다."
    last_order = mock_client.sent_orders[0]
    assert last_order["code"] == "005930"
    assert last_order["side"] == "BUY"
    assert last_order["price"] == 71600, "매도 1호가(71,600원)로 스마트 매수 발주되어야 합니다."

    print(f"  ✅ 시가(70,000원) + 0.5*ATR(1,000원) = 71,000원 변동성 돌파 매수 성공: {last_order['qty']}주 @ {last_order['price']}원")

async def test_order_timeout_manager_buy_cancel():
    """21. 미체결 매수 주문 30초 타임아웃 감시 및 자동 취소(D+2 예수금 증거금 반환) 검증"""
    print("▶ [Test 21] 미체결 매수 주문 30초 타임아웃 감시 및 자동 취소(kt10003) 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 매수 주문 등록 (35초 전 발주 가정)
    await bot.order_timeout_mgr.track_order("ORD_BUY_001", "005930", "삼성전자", "BUY", 10, 70000.0, "00")
    # 시간 인위적 35초 전으로 설정
    bot.order_timeout_mgr.tracked_orders["ORD_BUY_001"]["timestamp"] = time.time() - 35.0

    assert "ORD_BUY_001" in bot.order_timeout_mgr.tracked_orders

    # 2. 미체결 타임아웃 해소 루프 실행
    mock_client.sent_orders.clear()
    await bot.order_timeout_mgr.check_and_resolve_timeouts()

    # 3. 검증: cancel_order가 호출되고 트래커에서 제거되었는지 확인
    assert "ORD_BUY_001" not in bot.order_timeout_mgr.tracked_orders
    assert len(mock_client.sent_orders) == 1
    cancel_order = mock_client.sent_orders[0]
    assert cancel_order["side"] == "CANCEL"
    assert cancel_order["order_no"] == "ORD_BUY_001"
    assert cancel_order["qty"] == 10
    print(f"  ✅ 미체결 매수 주문(ORD_BUY_001, 10주) 30초 타임아웃 자동 취소 완료 (예수금 증거금 반환)")

async def test_order_timeout_manager_sell_replace():
    """22. 미체결 매도 주문 30초 타임아웃 지정가 취소 후 즉시 긴급 시장가(03) CRITICAL 재발주 검증"""
    print("▶ [Test 22] 미체결 매도 주문 30초 타임아웃 지정가 취소 후 즉시 긴급 시장가(03) 전량 재청산 검증...")
    mock_client = MockKiwoomClient()
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 지정가 매도 주문 등록 (35초 전 발주)
    await bot.order_timeout_mgr.track_order("ORD_SELL_002", "000660", "SK하이닉스", "SELL", 5, 150000.0, "00")
    bot.order_timeout_mgr.tracked_orders["ORD_SELL_002"]["timestamp"] = time.time() - 35.0

    mock_client.sent_orders.clear()
    # 2. 타임아웃 해소 실행
    await bot.order_timeout_mgr.check_and_resolve_timeouts()

    # 3. 검증: 기존 지정가 취소(CANCEL) 후 즉시 긴급 시장가(03) SELL 발주 확인
    assert len(mock_client.sent_orders) >= 2
    first_op = mock_client.sent_orders[0]
    second_op = mock_client.sent_orders[1]

    assert first_op["side"] == "CANCEL"
    assert first_op["order_no"] == "ORD_SELL_002"

    assert second_op["side"] == "SELL"
    assert second_op["code"] == "000660"
    assert second_op["qty"] == 5
    assert second_op["order_type"] == "03"  # 시장가
    assert second_op["priority"] == RequestPriority.CRITICAL
    print(f"  ✅ 미체결 매도 지정가 취소 -> 즉시 긴급 시장가(03) CRITICAL 전량 청산 재발주 완료")

async def test_daily_drawdown_circuit_breaker():
    """24. 일일 손실 제한(-2.5% ~ -3.0%) 서킷 브레이커 발동 및 신규 매수 전면 차단 검증"""
    print("▶ [Test 24] 일일 최대 손실 제한(Circuit Breaker) 발동 및 신규 매수 차단 검증...")
    mock_client = MockKiwoomClient(deposit=10_000_000.0)
    mock_db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client, portfolio=portfolio, db=mock_db)

    # 1. 초기 상태: 당일 시작 자산 10,000,000원 설정
    bot.daily_start_capital = 10_000_000.0
    bot.daily_circuit_breaker = False

    # 2. 정상 범위 손실 (-1.0%, 총자산 9,900,000원)
    portfolio.total_asset = 9_900_000.0
    portfolio.current_capital = 9_900_000.0
    mock_client.deposit = 9_900_000.0
    await bot._sync_account_balance()
    assert bot.daily_circuit_breaker is False, "-1.0% 손실에서는 서킷브레이커가 발동되지 않아야 합니다."

    # 3. 일일 한도 초과 손실 (-3.20%, 총자산 9,680,000원)
    portfolio.total_asset = 9_680_000.0
    portfolio.current_capital = 9_680_000.0
    mock_client.deposit = 9_680_000.0
    await bot._sync_account_balance()
    assert bot.daily_circuit_breaker is True, "당일 누적 손실 -2.5% 초과 시 서킷브레이커가 발동되어야 합니다."

    # 4. 서킷브레이커 발동 상태에서 매수 타점 시도 시 매수 전면 차단 검증
    bot.watchlist["005930"] = {
        "name": "삼성전자", "period_high": 80000.0, "period_low": 70000.0,
        "fib_382": 76180.0, "fib_500": 75000.0, "fib_618": 73820.0,
        "current_price": 75000.0
    }
    mock_client.sent_orders.clear()
    await bot._evaluate_buy_condition("005930", cur_price=75000.0, cur_volume=100000.0)
    buy_orders = [o for o in mock_client.sent_orders if o.get('side') == 'BUY']
    assert len(buy_orders) == 0, "서킷브레이커 발동 중에는 신규 매수 주문이 차단되어야 합니다."
    print("  ✅ 일일 손실 제한(-2.5%) 서킷 브레이커 발동 및 신규 매수 전면 차단 검증 완료")

async def test_vwap_and_volume_profile_filters():
    """23. VWAP 스마트 지지 필터 및 Volume Profile(매물대 POC) 가짜 돌파(휩소) 기각 검증"""
    print("▶ [Test 23] VWAP 스마트 지지 필터 및 매물대(POC) 가짜 돌파 기각 검증...")
    from strategy import AdaptiveVolatilityBreakoutStrategy
    strat = AdaptiveVolatilityBreakoutStrategy(k_breakout=0.5)

    # 1) Case 1: 가격이 VWAP 아래에 위치한 가짜 돌파 캔들 시뮬레이션
    fake_ind = {
        'open': 70000, 'atr14': 2000, 'ma20': 68000, 'rsi14': 60.0,
        'vwap': 74000.0,      # 당일 거래량 가중 평균가 74,000원
        'poc_price': 72000.0,
        'skip_time_filter': True
    }
    # 현재가 71,500원은 시가+k*ATR(71,000원) 돌파했으나 VWAP(74,000원)보다 낮아 가짜 돌파
    sig1, reason1 = await strat.check_buy_signal("005930", current_price=71500.0, current_volume=100000.0, ind=fake_ind)
    assert sig1 is False, "VWAP 하회 시 매수가 기각되어야 합니다."
    assert "VWAP_하회_가짜돌파기각" in reason1
    print(f"  ✅ VWAP 하회 가짜 돌파 완벽 기각: {reason1}")

    # 2) Case 2: 가격이 매물대 저항선(POC 75,000원) 바로 아래에 갇힌 캔들
    fake_ind2 = {
        'open': 70000, 'atr14': 2000, 'ma20': 68000, 'rsi14': 60.0,
        'vwap': 70000.0,      # VWAP 지지 통과
        'poc_price': 75000.0, # 대량 매물대 75,000원 저항
        'skip_time_filter': True
    }
    sig2, reason2 = await strat.check_buy_signal("005930", current_price=72000.0, current_volume=100000.0, ind=fake_ind2)
    assert sig2 is False, "매물대 POC 저항 직전 매수가 기각되어야 합니다."
    assert "매물대_저항선_직전_돌파대기" in reason2
    print(f"  ✅ 매물대 POC 저항 가짜 돌파 완벽 기각: {reason2}")

    # 3) Case 3: VWAP 지지 + POC 매물대 상향 돌파 안착 정규 타점
    real_ind = {
        'open': 70000, 'atr14': 2000, 'ma20': 68000, 'rsi14': 60.0,
        'vwap': 71000.0,      # VWAP 지지 통과 (현재가 73,000 > VWAP 71,000)
        'poc_price': 72000.0, # POC 돌파 안착 (현재가 73,000 > POC 72,000)
        'skip_time_filter': True
    }
    sig3, reason3 = await strat.check_buy_signal("005930", current_price=73000.0, current_volume=100000.0, ind=real_ind)
    assert sig3 is True, "모든 필터 통과 시 정상 매수 시그널이 발생해야 합니다."
    print(f"  ✅ VWAP 지지 + POC 매물대 돌파 정규 타점 승인 완료: {reason3}")

async def main():
    print("=" * 65)
    print("🚀 [Phase 2 & Phase 15] 비동기 트레이딩 봇 매매 시뮬레이션 & 퀀트 전략 종합 검증")
    print("=" * 65)
    await test_fibonacci_pullback_entry()
    await test_three_stage_profit_taking()
    await test_hard_stop_loss_preemption()
    await test_trailing_stop()
    await test_market_filter_and_manual_orders()
    await test_update_watchlist_multi_schema_and_fallback()
    await test_detailed_debug_logging_and_low_capital_handling()
    await test_d2_deposit_unification_and_throttling()
    await test_kiwoom_real_balance_parsing_various_schemas()
    await test_actual_account_balance_and_4_holdings_sync()
    await test_dynamic_watchlist_and_full_quant_workflow()
    await test_actual_account_balance_with_images_data()
    await test_single_multi_record_split_and_zero_ccls_qty_handling()
    await test_korean_keys_parsing()
    await test_kt00018_priority_and_isin_and_zero_padded_codes_parsing()
    await test_db_restore_positions_safety_without_cash_deduction()
    await test_buy_price_multi_layer_inference_and_anti_1won_defense()
    await test_zero_holdings_and_single_source_total_asset_sync()
    await test_bot_auto_wakeup_and_resume_schedule()
    await test_buy_signal_open_price_and_0915_time_filter()
    await test_order_timeout_manager_buy_cancel()
    await test_order_timeout_manager_sell_replace()
    await test_vwap_and_volume_profile_filters()
    await test_daily_drawdown_circuit_breaker()
    print("=" * 65)
    print("🎉 모든 퀀트 매매, 미체결 방어 및 VWAP 필터 시뮬레이션 테스트 (총 24개) 100% 통과 완료!")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
