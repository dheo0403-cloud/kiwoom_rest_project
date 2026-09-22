"""
미체결 누적 방지 & 가용 자금(증거금 락) 기반 매수 통제 투명성 실측 검증 스크립트
1. 총 예수금 120,000원 환경에서 미체결 매수 주문이 증거금을 점유할 때 신규 매수가 철저히 차단되는지 검증
2. 30초 타임아웃 경과 시 kt10003 자동 취소 발송 및 묶인 예수금 즉각 반환 검증
3. 예수금 반환 후 신규 정상 매수가 매끄럽게 체결되는 라이프사이클 실측
"""
import asyncio
import time
from datetime import datetime
from async_kiwoom_client import RequestPriority
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot, OrderTimeoutManager
from strategy import AdaptiveVolatilityBreakoutStrategy


class MockKiwoomTradingClient:
    def __init__(self, deposit: float = 120_000.0):
        self.deposit = deposit
        self.sent_orders = []
        self.prices = {}
        self.holdings = {}
        self.is_demo = True
        self.mode = "MOCK"

    async def send_order(self, code: str, qty: int, price: int,
                         order_type: str = "00", side: str = "BUY",
                         priority: RequestPriority = RequestPriority.HIGH):
        record = {
            "code": code, "qty": qty, "price": price,
            "order_type": order_type, "side": side, "priority": priority,
            "timestamp": time.time()
        }
        self.sent_orders.append(record)
        ord_no = f"ORD_{len(self.sent_orders)}"
        return {"rt_cd": "0", "msg1": "주문접수성공", "ord_no": ord_no}

    async def cancel_order(self, order_no: str, code: str, qty: int, priority: RequestPriority = RequestPriority.HIGH):
        self.sent_orders.append({
            "code": code, "qty": qty, "order_no": order_no, "side": "CANCEL", "priority": priority,
            "timestamp": time.time()
        })
        return {"rt_cd": "0", "msg1": "주문취소성공"}

    async def get_price(self, code: str, priority: RequestPriority = RequestPriority.LOW):
        price = self.prices.get(code, 50000.0)
        return {"output": [{"stk_cd": code, "prpr": str(int(price)), "current_price": str(int(price))}]}

    async def get_orderbook(self, code: str, priority: RequestPriority = RequestPriority.LOW):
        price = self.prices.get(code, 50000.0)
        return {"output": [{"sel_fpr_bid": str(int(price)), "buy_fpr_bid": str(int(price - 100))}]}

    async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
        # 키움 TR은 미체결 상태일 때 주문 직후 원래 예수금(120,000원)을 그대로 반환하는 실전 특성 모사
        return {"output1": [{"tot_evlu_amt": str(int(self.deposit)), "dnca_tot_amt": str(int(self.deposit))}], "output2": []}

    async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM):
        return {"output1": [{"dnca_tot_amt": str(int(self.deposit))}], "output2": []}

    async def get_unexecuted_orders(self, code: str = None, priority: RequestPriority = RequestPriority.LOW):
        return {"output": []}


class MockDatabaseManager:
    async def log_order(self, *args, **kwargs): pass
    async def log_message(self, *args, **kwargs): pass
    async def save_portfolio(self, *args, **kwargs): pass
    async def update_balance(self, *args, **kwargs): pass
    async def save_watchlist(self, *args, **kwargs): pass
    async def get_pending_manual_orders(self): return []


async def run_verification():
    print("=" * 85)
    print("🔬 [매수 미체결 타임아웃 자동 취소 & 가용 예수금 락(Lock) 투명성 실측 검증]")
    print("=" * 85)

    client = MockKiwoomTradingClient(deposit=120_000.0)
    db = MockDatabaseManager()
    portfolio = AsyncPortfolioManager(initial_capital=120_000.0, max_stocks=3)
    strategy = AdaptiveVolatilityBreakoutStrategy(k_breakout=0.5)
    bot = AsyncTradingBot(is_demo=True, initial_capital=120_000.0, client=client, portfolio=portfolio, db=db, strategy=strategy)
    bot.order_timeout_mgr.timeout_seconds = 30.0

    print(f"\n[초기 상태] 계좌 총 D+2 예수금: {int(portfolio.current_capital):,}원 | 최대 동시 보유/미체결 슬롯: {portfolio.max_stocks}개")

    # -----------------------------------------------------------------------------------------
    # 단계 1: 1번 종목(삼성전자 70,000원) 매수 발주 -> 미체결 상태 등록 및 증거금 락 가동
    # -----------------------------------------------------------------------------------------
    print("\n" + "─" * 85)
    print("▶ [단계 1] 종목 1 (삼성전자 70,000원 1주) 매수 발주 및 미체결 증거금 락")
    print("─" * 85)

    bot.watchlist["005930"] = {
        "name": "삼성전자", "open_price": 69000.0, "period_high": 80000.0, "period_low": 68000.0,
        "fib_382": 76180.0, "fib_500": 75000.0, "fib_618": 73820.0, "current_price": 70000.0
    }
    client.prices["005930"] = 70000.0

    # 가상 분봉 적재 (시가 69,000원, 고가 70,000원, 저가 68,500원, 종가 69,500원 -> ATR=1,000, 돌파선=69,500원)
    bot.buffer.load_initial_candles("005930", [
        {"datetime": "2026-09-22 09:15:00", "open": 69000, "high": 70000, "low": 68500, "close": 69500, "volume": 5000}
    ])

    await bot._evaluate_buy_condition("005930", cur_price=70000.0, cur_volume=10000.0)

    pending_amt = bot.order_timeout_mgr.get_pending_buy_amount()
    real_avail = portfolio.get_available_cash_with_pending_lock(pending_amt)
    pending_codes = bot.order_timeout_mgr.get_pending_buy_codes()

    print(f"  ✅ 삼성전자 1주(70,000원) 매수 발주 완료 (주문번호 ORD_1)")
    print(f"  🔒 [미체결 증거금 락 현황] 묶인 증거금: {int(pending_amt):,}원 (미체결 종목: {list(pending_codes)})")
    print(f"  💰 [실제 가용 주문금액] 총예수금 {int(portfolio.current_capital):,}원 - 미체결락 {int(pending_amt):,}원 = {int(real_avail):,}원")
    assert pending_amt == 70000.0
    assert real_avail == 50000.0

    # -----------------------------------------------------------------------------------------
    # 단계 2: 가용 자금(50,000원) 초과 종목(SK하이닉스 150,000원) 및 추가 주문 난사 100% 차단 검증
    # -----------------------------------------------------------------------------------------
    print("\n" + "─" * 85)
    print("▶ [단계 2] 가용 자금 부족 종목 매수 시도 -> 855056(증거금 부족) 방지 100% 차단 실측")
    print("─" * 85)

    bot.watchlist["000660"] = {
        "name": "SK하이닉스", "open_price": 148000.0, "period_high": 160000.0, "period_low": 140000.0,
        "fib_382": 152360.0, "fib_500": 150000.0, "fib_618": 147640.0, "current_price": 150000.0
    }
    client.prices["000660"] = 150000.0
    bot.buffer.load_initial_candles("000660", [
        {"datetime": "2026-09-22 09:15:00", "open": 148000, "high": 149500, "low": 147500, "close": 149000, "volume": 5000}
    ])

    prev_order_count = len(client.sent_orders)
    await bot._evaluate_buy_condition("000660", cur_price=150000.0, cur_volume=10000.0)
    assert len(client.sent_orders) == prev_order_count, "가용 자금(50,000원) 부족으로 SK하이닉스(150,000원) 매수가 차단되어야 합니다."
    print("  🚫 [매수 차단 성공] SK하이닉스(150,000원) > 가용 예수금(50,000원) ➔ 무지성 주문 전면 차단 완료!")

    # 2번 종목 (펄어비스 35,000원) 매수 발주 -> 가용 자금(50,000원) 내이므로 1주 발주 성공
    bot.watchlist["263750"] = {
        "name": "펄어비스", "open_price": 34000.0, "period_high": 40000.0, "period_low": 30000.0,
        "fib_382": 36180.0, "fib_500": 35000.0, "fib_618": 33820.0, "current_price": 35000.0
    }
    client.prices["263750"] = 35000.0
    bot.buffer.load_initial_candles("263750", [
        {"datetime": "2026-09-22 09:15:00", "open": 34000, "high": 34800, "low": 33500, "close": 34500, "volume": 5000}
    ])
    await bot._evaluate_buy_condition("263750", cur_price=35000.0, cur_volume=10000.0)

    pending_amt = bot.order_timeout_mgr.get_pending_buy_amount()
    real_avail = portfolio.get_available_cash_with_pending_lock(pending_amt)
    print(f"  ✅ 펄어비스 1주(35,000원) 매수 발주 완료 (주문번호 ORD_2)")
    print(f"  🔒 [미체결 증거금 락 갱신] 총 묶인 증거금: {int(pending_amt):,}원 (70,000 + 35,000원)")
    print(f"  💰 [잔여 가용 주문금액] 총예수금 {int(portfolio.current_capital):,}원 - 미체결락 {int(pending_amt):,}원 = {int(real_avail):,}원")
    assert pending_amt == 105000.0
    assert real_avail == 15000.0

    # 3번 종목 (파인엠텍 20,000원) 매수 시도 -> 가용 15,000원 부족으로 차단 실측
    bot.watchlist["441270"] = {
        "name": "파인엠텍", "open_price": 19000.0, "period_high": 25000.0, "period_low": 18000.0,
        "fib_382": 22326.0, "fib_500": 21500.0, "fib_618": 20674.0, "current_price": 20000.0
    }
    client.prices["441270"] = 20000.0
    bot.buffer.load_initial_candles("441270", [
        {"datetime": "2026-09-22 09:15:00", "open": 19000, "high": 19800, "low": 18500, "close": 19500, "volume": 5000}
    ])
    prev_orders = len(client.sent_orders)
    await bot._evaluate_buy_condition("441270", cur_price=20000.0, cur_volume=10000.0)
    assert len(client.sent_orders) == prev_orders, "가용 15,000원 부족으로 파인엠텍(20,000원) 매수가 차단되어야 합니다."
    print("  🚫 [매수 차단 성공] 파인엠텍(20,000원) > 가용 예수금(15,000원) ➔ 증거금 락에 의한 추가 매수 차단 확인!")

    prev_order_count = len(client.sent_orders)
    await bot._evaluate_buy_condition("000660", cur_price=150000.0, cur_volume=10000.0)
    assert len(client.sent_orders) == prev_order_count, "가용 자금(50,000원) 부족으로 SK하이닉스(150,000원) 매수가 차단되어야 합니다."
    print("  🚫 [매수 차단 성공] SK하이닉스(150,000원) > 가용 예수금(50,000원) ➔ 무지성 주문 전면 차단 완료!")

    # 2번 종목 (펄어비스 35,000원) 매수 발주 -> 가용 자금(50,000원) 내이므로 1주 발주 성공
    bot.watchlist["263750"] = {
        "name": "펄어비스", "open_price": 35000.0, "period_high": 40000.0, "period_low": 30000.0,
        "fib_382": 36180.0, "fib_500": 35000.0, "fib_618": 33820.0, "current_price": 35000.0
    }
    client.prices["263750"] = 35000.0
    bot.buffer.load_initial_candles("263750", [
        {"datetime": "2026-09-22 09:15:00", "open": 35000, "high": 36000, "low": 34000, "close": 35500, "volume": 5000}
    ])
    await bot._evaluate_buy_condition("263750", cur_price=35000.0, cur_volume=10000.0)

    pending_amt = bot.order_timeout_mgr.get_pending_buy_amount()
    real_avail = portfolio.get_available_cash_with_pending_lock(pending_amt)
    print(f"  ✅ 펄어비스 1주(35,000원) 매수 발주 완료 (주문번호 ORD_2)")
    print(f"  🔒 [미체결 증거금 락 갱신] 총 묶인 증거금: {int(pending_amt):,}원 (70,000 + 35,000원)")
    print(f"  💰 [잔여 가용 주문금액] 총예수금 {int(portfolio.current_capital):,}원 - 미체결락 {int(pending_amt):,}원 = {int(real_avail):,}원")
    assert pending_amt == 105000.0
    assert real_avail == 15000.0

    # 3번 종목 (파인엠텍 20,000원) 매수 시도 -> 가용 15,000원 부족으로 차단 실측
    bot.watchlist["441270"] = {
        "name": "파인엠텍", "open_price": 20000.0, "period_high": 25000.0, "period_low": 18000.0,
        "fib_382": 22326.0, "fib_500": 21500.0, "fib_618": 20674.0, "current_price": 20000.0
    }
    client.prices["441270"] = 20000.0
    bot.buffer.load_initial_candles("441270", [
        {"datetime": "2026-09-22 09:15:00", "open": 20000, "high": 21000, "low": 19000, "close": 20500, "volume": 5000}
    ])
    prev_orders = len(client.sent_orders)
    await bot._evaluate_buy_condition("441270", cur_price=20000.0, cur_volume=10000.0)
    assert len(client.sent_orders) == prev_orders, "가용 15,000원 부족으로 파인엠텍(20,000원) 매수가 차단되어야 합니다."
    print("  🚫 [매수 차단 성공] 파인엠텍(20,000원) > 가용 예수금(15,000원) ➔ 증거금 락에 의한 추가 매수 차단 확인!")

    # -----------------------------------------------------------------------------------------
    # 단계 3: 30초 타임아웃 경과 -> 미체결 주문 자동 취소 및 예수금 100% 반환
    # -----------------------------------------------------------------------------------------
    print("\n" + "─" * 85)
    print("▶ [단계 3] 30초 타임아웃 경과 -> kt10003 자동 취소 및 묶인 예수금 100% 반환 실측")
    print("─" * 85)

    # 35초 경과 시뮬레이션
    for ord_info in bot.order_timeout_mgr.tracked_orders.values():
        ord_info["timestamp"] = time.time() - 35.0

    print("  ⏱ [타임아웃 감시] 35초 경과 감지 -> OrderTimeoutManager.check_and_resolve_timeouts() 실행...")
    client.sent_orders.clear()
    await bot.order_timeout_mgr.check_and_resolve_timeouts()

    cancel_orders = [o for o in client.sent_orders if o.get('side') == 'CANCEL']
    print(f"  ✅ [자동 취소 완료] 총 {len(cancel_orders)}건 미체결 매수 취소 주문 발송:")
    for co in cancel_orders:
        print(f"     ├─ [취소 주문] 종목: {co['code']} | 취소수량: {co['qty']}주 | 대상주문번호: {co['order_no']}")

    assert len(cancel_orders) == 2
    assert bot.order_timeout_mgr.get_pending_buy_amount() == 0.0

    # 계좌 잔고 동기화 (미체결 취소로 예수금 100% 반환)
    await bot._sync_account_balance()
    restored_avail = portfolio.get_available_cash_with_pending_lock(0.0)
    print(f"  💰 [예수금 반환 확인] 미체결 해소 ➔ 가용 주문가능금액 {int(restored_avail):,}원 전액 복구 완료!")
    assert restored_avail == 120000.0

    # -----------------------------------------------------------------------------------------
    # 단계 4: 예수금 복구 후 신규 정상 매수 성공 검증
    # -----------------------------------------------------------------------------------------
    print("\n" + "─" * 85)
    print("▶ [단계 4] 예수금 복구 후 신규 정상 매수(비에이치 20,000원 3주 = 60,000원) 진입 실측")
    print("─" * 85)

    bot.watchlist["090460"] = {
        "name": "비에이치", "open_price": 19800.0, "period_high": 25000.0, "period_low": 18000.0,
        "fib_382": 22326.0, "fib_500": 21500.0, "fib_618": 20674.0, "current_price": 20100.0
    }
    client.prices["090460"] = 20100.0
    bot.buffer.load_initial_candles("090460", [
        {"datetime": "2026-09-22 09:15:00", "open": 19800, "high": 20000, "low": 19500, "close": 19900, "volume": 5000}
    ])

    client.sent_orders.clear()
    await bot._evaluate_buy_condition("090460", cur_price=20100.0, cur_volume=10000.0)

    buy_orders = [o for o in client.sent_orders if o.get('side') == 'BUY']
    assert len(buy_orders) == 1, "예수금 반환 후 비에이치 정상 매수 주문이 1건 발송되어야 합니다."
    last_buy = buy_orders[0]
    print(f"  🔥 [신규 매수 성공] 비에이치({last_buy['code']}) {last_buy['qty']}주 @ {last_buy['price']:,}원 (총 {last_buy['qty']*last_buy['price']:,}원 체결)")
    print("  🏁 전체 라이프사이클(자금부족 차단 -> 30초 타임아웃 취소 -> 예수금 반환 -> 신규 정상 매수) 100% 통과!")

    print("\n" + "=" * 85)
    print("🎉 [투명성 실측 검증 완료] 미체결 증거금 락 & 타임아웃 자동 취소 파이프라인 정상 작동 증명")
    print("=" * 85)


if __name__ == '__main__':
    asyncio.run(run_verification())
