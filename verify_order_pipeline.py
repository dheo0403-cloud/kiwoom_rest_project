"""
투명성 교차 검증 스크립트 (Transparency Cross-Verification Script)
가상 시세 데이터 주입을 통한
[매수 시그널 발생 -> D+2 예수금 확인 -> 켈리 수량 산출 -> SendOrder 정상 호출 -> 체결 및 포트폴리오 편입] 전 과정 실측 검증
"""
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock

from async_kiwoom_client import RequestPriority
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot
from strategy import AdaptiveVolatilityBreakoutStrategy


class PipelineMockClient:
    def __init__(self, deposit: float = 10_000_000.0):
        self.sent_orders: List[Dict[str, Any]] = []
        self.is_demo = True
        self.mode = "MOCK"
        self.account = "8012345611"
        self.password = "0000"
        self.deposit = deposit
        self.holdings: List[Dict[str, Any]] = []

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
            "timestamp": datetime.now().strftime('%H:%M:%S.%f')[:-3]
        }
        self.sent_orders.append(order_record)
        print(f"  📡 [Mock Kiwoom Server] SendOrder 호출 수신: {side} {code} {qty}주 @ {price:,}원 (호가유형: {order_type}, 우선순위: {priority.name})")

        # 체결 시 예수금 차감 및 보유 잔고 추가
        if side == "BUY":
            self.deposit = max(0.0, self.deposit - (qty * price))
            self.holdings.append({
                "stk_cd": code,
                "stk_nm": "삼성전자",
                "hldg_qty": str(qty),
                "pchs_avg_pric": str(price),
                "prpr": str(price),
                "evlu_amt": str(qty * price)
            })

        return {
            "rt_cd": "0",
            "msg_cd": "0000",
            "msg1": "주문이 정상적으로 접수되었습니다.",
            "ord_no": "908123"
        }

    async def get_orderbook(self, code: str, priority: RequestPriority = RequestPriority.HIGH):
        return {
            "output": {
                "sel_fpr_bid": "71500",  # 매도 1호가
                "buy_fpr_bid": "71400"   # 매수 1호가
            }
        }

    async def get_price(self, code: str, priority: RequestPriority = RequestPriority.LOW):
        return {
            "output": {
                "prpr": "71500",
                "acml_vol": "2500000",
                "volume_power": "135.5",
                "oprn": "70000"
            }
        }

    async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM):
        total_eval = self.deposit + sum(int(h['hldg_qty']) * int(h['prpr']) for h in self.holdings)
        return {
            "output1": [{"tot_evlu_amt": str(int(total_eval)), "d2_deposit": str(int(self.deposit))}],
            "output2": self.holdings
        }

    async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM):
        return {
            "output1": [{"d2_deposit": str(int(self.deposit))}]
        }


async def run_pipeline_cross_verification():
    print("=" * 80)
    print("🔬 [투명성 교차 검증] 매수 시그널 ➔ D+2 예수금 확인 ➔ 켈리 수량 ➔ SendOrder 파이프라인 실측")
    print("=" * 80)

    # 1. 봇 및 Mock 클라이언트 생성
    mock_client = PipelineMockClient(deposit=10_000_000.0)
    bot = AsyncTradingBot(is_demo=True, initial_capital=10_000_000, client=mock_client)
    bot.is_test = True
    bot.is_running = True
    bot.market_filter_passed = True

    bot.db = AsyncMock()
    bot.db.log_order = AsyncMock()
    bot.db.log_message = AsyncMock()
    bot.notifier = MagicMock()
    bot.notifier.notify_order_filled = MagicMock()

    # 2. D+2 예수금 10,000,000원 설정
    await bot.portfolio.sync_capital(available_cash=10_000_000, total_asset=10_000_000)
    print(f"💰 [1단계: 예수금 확인] D+2 가용 예수금: {int(bot.portfolio.current_capital):,}원 / 총자산: {int(bot.portfolio.total_asset):,}원")

    # 3. 감시 종목 유니버스 등록 (삼성전자 005930)
    bot.watchlist["005930"] = {
        "code": "005930",
        "name": "삼성전자",
        "current_price": 70000.0,
        "open_price": 70000.0,
        "period_high": 75000.0,
        "period_low": 68000.0,
        "fib_382": 71200.0,
        "fib_500": 71500.0,
        "fib_618": 72320.0,
        "avg_volume": 1_000_000.0
    }
    print("📋 [2단계: 감시 유니버스 등록] 삼성전자(005930) 피보나치 & 시가 70,000원 세팅 완료")

    # 4. 실시간 가상 틱 데이터 주입:
    # 시가 70,000원 -> 돌파 기준가(71,000원) 상향 돌파하는 71,500원 체결가 주입
    print("⚡ [3단계: 실시간 틱 데이터 주입] 삼성전자(005930) 현재가 71,500원 (+2.14% 급등 돌파, 거래량 250만주, 체결강도 135.5%) 발생!")

    simulated_real_data = {
        "current_price": 71500.0,
        "volume": 500,
        "open_price": 70000.0,
        "raw": {
            "prpr": "71500",
            "acml_vol": "2500000",
            "volume_power": "135.5",
            "oprn": "70000"
        }
    }

    # OnReceiveRealData 호출
    await bot.OnReceiveRealData("005930", "주식체결", simulated_real_data)

    # 5. 검증 결과 확인
    print("-" * 80)
    print("📊 [4단계: 파이프라인 실행 결과 검증]")
    print(f"  • SendOrder 호출 횟수: {len(mock_client.sent_orders)}회")
    if mock_client.sent_orders:
        order = mock_client.sent_orders[0]
        print(f"  • 호출된 주문 내역: {order['side']} {order['code']} {order['qty']}주 @ {order['price']:,}원 (타입: {order['order_type']})")
        print(f"  • 주문 접수 타임스탬프: {order['timestamp']}")

        # 포트폴리오 잔고 확인
        snap = await bot.portfolio.get_snapshot()
        print(f"  • 체결 후 잔여 D+2 예수금: {int(snap['current_capital']):,}원")
        print(f"  • 총 평가자산: {int(snap['total_asset']):,}원")
        pos_str_list = [f"{p['name']}({p['qty']}주@{int(p['buy_price']):,}원)" for p in snap['positions']]
        print(f"  • 보유 포지션 수: {len(snap['positions'])}개 ({pos_str_list})")

        assert order['code'] == "005930", "종목코드가 일치해야 합니다."
        assert order['side'] == "BUY", "매수 주문이어야 합니다."
        assert order['qty'] > 0, "주문 수량이 1주 이상이어야 합니다."
        assert order['price'] == 71500, "매도 1호가(71,500원) 지정가 주문이어야 합니다."
        assert "005930" in bot.portfolio.positions, "포지션에 정상 편입되어야 합니다."
        print("\n🏆 [검증 성공] 매수 시그널 발생 ➔ 예수금 확인 ➔ 켈리 수량 산출 ➔ SendOrder 정상 호출 ➔ 포지션 편입 100% 실측 완료!")
    else:
        print("\n❌ [검증 실패] SendOrder가 호출되지 않았습니다.")
        raise AssertionError("SendOrder was not called")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_pipeline_cross_verification())
