"""
Gate Info:
- Importers/callers: Direct execution (`python test_async_core.py`)
- Affected API: None (Local Unit & Concurrency Tests)
- Data schemas: PriorityQueue ordering assertions, TokenBucket timing, Portfolio state validation
- User's verbatim instruction: "미안 AKS에 대한 부분은 삭제해줘 마이그레이션 후 별도로 문의할께"
"""
import asyncio
import time
from async_kiwoom_client import RequestPriority, QueuedRequest, TokenBucketRateLimiter
from async_portfolio import AsyncPortfolioManager

async def test_priority_queue_preemption():
    """우선순위 큐 검증: LOW 요청보다 나중에 들어온 CRITICAL 요청이 먼저 pop되는지 확인"""
    print("▶ [Test 1] 우선순위 큐 (PriorityQueue) 추월 검증 시작...")
    queue = asyncio.PriorityQueue()

    # 1. LOW(Priority 3) 요청 3건 등록
    for i in range(3):
        req = QueuedRequest(
            priority=int(RequestPriority.LOW),
            timestamp=time.time(),
            api_id=f"LOW_REQ_{i}",
            url="http://test",
            payload={}
        )
        await queue.put(req)
        await asyncio.sleep(0.01)

    # 2. 긴급 매도 CRITICAL(Priority 0) 및 정규 매수 HIGH(Priority 1) 요청 나중에 등록
    urgent_req = QueuedRequest(
        priority=int(RequestPriority.CRITICAL),
        timestamp=time.time(),
        api_id="URGENT_STOPLOSS_0",
        url="http://test",
        payload={}
    )
    high_req = QueuedRequest(
        priority=int(RequestPriority.HIGH),
        timestamp=time.time(),
        api_id="HIGH_BUY_1",
        url="http://test",
        payload={}
    )
    await queue.put(urgent_req)
    await queue.put(high_req)

    # 3. 큐에서 꺼낼 때 순서 검증 (CRITICAL(0) -> HIGH(1) -> LOW(3) 순이어야 함)
    first_out = await queue.get()
    second_out = await queue.get()
    third_out = await queue.get()

    assert first_out.api_id == "URGENT_STOPLOSS_0", f"Expected CRITICAL first, got {first_out.api_id}"
    assert second_out.api_id == "HIGH_BUY_1", f"Expected HIGH second, got {second_out.api_id}"
    assert "LOW_REQ" in third_out.api_id, f"Expected LOW third, got {third_out.api_id}"
    print("  ✅ 우선순위 큐 추월(Preemption) 검증 통과!")

async def test_token_bucket_rate_limiter():
    """토큰 버킷 속도 제어기 검증 (초당 생성량 및 대기 제어)"""
    print("▶ [Test 2] Token Bucket Rate Limiter 검증 시작 (Rate: 5.0/s)...")
    limiter = TokenBucketRateLimiter(rate=5.0, capacity=2.0)

    # 2개 버스트 즉시 소진
    start_t = time.time()
    await limiter.acquire(1.0)
    await limiter.acquire(1.0)
    burst_elapsed = time.time() - start_t
    assert burst_elapsed < 0.1, f"Burst acquire should be instant, took {burst_elapsed:.3f}s"

    # 3번째 토큰 요청 시 대기 발생 검증 (5.0 rate 기준 약 0.2초 대기 필요)
    start_t = time.time()
    await limiter.acquire(1.0)
    wait_elapsed = time.time() - start_t
    assert wait_elapsed >= 0.15, f"Expected wait >= 0.15s, got {wait_elapsed:.3f}s"
    print(f"  ✅ Token Bucket 지연 제어 정상 작동 (대기시간: {wait_elapsed:.3f}s)")

async def test_async_portfolio_manager():
    """비동기 포트폴리오 관리자 동시성 및 자산/PnL 산출 검증"""
    print("▶ [Test 3] AsyncPortfolioManager 동시성 및 PnL 검증 시작...")
    pm = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)

    # 포지션 추가
    await pm.add_position(code="005930", name="삼성전자", qty=10, buy_price=70000)
    await pm.add_position(code="000660", name="SK하이닉스", qty=5, buy_price=160000)

    # 현재가 업데이트 (삼성전자 +5%, SK하이닉스 -2%)
    await pm.update_current_price("005930", 73500)
    await pm.update_current_price("000660", 156800)

    # 안전 주문 수량 계산 검증
    order_qty = await pm.get_order_qty(current_price=50000)
    assert order_qty > 0, "Order qty should be > 0"

    # 스냅샷 검증
    snap = await pm.get_snapshot()
    assert snap['stock_count'] == 2
    assert len(snap['positions']) == 2
    samsung = next(p for p in snap['positions'] if p['code'] == "005930")
    assert samsung['pnl'] == 35000  # (73500 - 70000) * 10
    assert abs(samsung['yield_rate'] - 5.0) < 0.01

    # 부분 매도 검증
    await pm.update_partial_sell("005930", sold_qty=5, sell_price=74000, next_stage=1)
    snap2 = await pm.get_snapshot()
    samsung2 = next(p for p in snap2['positions'] if p['code'] == "005930")
    assert samsung2['qty'] == 5
    assert samsung2['sell_stage'] == 1
    print("  ✅ AsyncPortfolioManager 상태 동기화 및 PnL 검증 통과!")

async def main():
    print("=" * 60)
    print("🧪 [Phase 1] 키움 비동기 코어 엔진 & 포트폴리오 통합 단위 테스트")
    print("=" * 60)
    await test_priority_queue_preemption()
    await test_token_bucket_rate_limiter()
    await test_async_portfolio_manager()
    print("=" * 60)
    print("🎉 Phase 1 모든 비동기 코어 단위 테스트 100% 성공!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
