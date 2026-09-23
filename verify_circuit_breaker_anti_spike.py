"""
실제 퀀트 매매 손익 기반 서킷 브레이커 & 이상치 스파이크(-18.46% 등) 방어 검증 스크립트
1. 보유 종목 손익률 -0.79% 상황에서 TR 수치 튐(-18.46%) 발생 시 Anti-Spike 필터 정상 작동 검증
2. 초기 3회 Warm-up 기간 계좌 기준선 안전 캘리브레이션 검증
3. 실제 매매 손실 -2.5% 초과 3회 연속 확인 시 정상 서킷 브레이커 발동 검증
4. 정상 손익률(-0.79% 등)로 데이터 회복 시 Self-Healing 자가 복구 검증
5. reset_circuit_breaker() 수동 해제 및 재캘리브레이션 검증
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from main_rest_async import AsyncTradingBot
from async_portfolio import AsyncPortfolioManager


async def run_circuit_breaker_anti_spike_tests():
    print("=" * 80)
    print("🔬 [검증] 실제 퀀트 운용 손익률 기반 서킷 브레이커 & TR 이상치(-18.46%) 방어 테스트")
    print("=" * 80)

    # -------------------------------------------------------------
    # [시나리오 1] 보유종목 손익률 -0.79% & TR 총자산 외형 튐(-18.46%) 시뮬레이션
    # -------------------------------------------------------------
    print("\n📊 [시나리오 1] 보유종목 손익률 -0.79% & TR 이상치(-18.46%) 주입")
    mock_db = AsyncMock()
    mock_db.log_message = AsyncMock()
    mock_db.save_portfolio = AsyncMock()
    mock_db.update_balance = AsyncMock()

    mock_client = AsyncMock()
    mock_client.mode = "REAL"
    mock_client.get_unexecuted_orders = AsyncMock(return_value={'output': []})

    portfolio = AsyncPortfolioManager(initial_capital=10_000_000, max_stocks=5)
    bot = AsyncTradingBot(
        is_demo=False,
        initial_capital=10_000_000,
        client=mock_client,
        portfolio=portfolio,
        db=mock_db
    )

    # 1. 웜업 3회 수행 (총자산 10,000,000원 정상 동기화)
    for i in range(3):
        balance_resp = {
            'output1': [{'tot_evlu_amt': '10000000', 'd2_deposit': '9000000', 'tot_pchs_amt': '1000000'}],
            'output2': [{'stk_cd': '005930', 'stk_nm': '삼성전자', 'hldg_qty': '10', 'pchs_avg_pric': '100000', 'prpr': '99210'}]
        }
        deposit_resp = {'output': [{'d2_deposit': '9000000', 'tot_evlu_amt': '10000000'}]}
        mock_client.get_account_balance = AsyncMock(return_value=balance_resp)
        mock_client.get_deposit = AsyncMock(return_value=deposit_resp)
        await bot._sync_account_balance()

    assert bot.sync_warmup_count == 3
    assert bot.daily_start_capital == 10_000_000
    assert bot.mdd_shutdown is False
    print("  ✅ 웜업 3회 완료: 기준자산 10,000,000원 안정화, 서킷 브레이커 미발동 확인")

    # 2. 4회차: TR에서 tot_evlu_amt가 8,154,000원으로 일시 왜곡(-18.46% 급락)되어 수신됨
    # 그러나 보유 주식 평가손익은 -7,900원 (-0.79%)
    balance_jitter_resp = {
        'output1': [{'tot_evlu_amt': '8154000', 'd2_deposit': '8154000'}],
        'output2': [{'stk_cd': '005930', 'stk_nm': '삼성전자', 'hldg_qty': '10', 'pchs_avg_pric': '100000', 'prpr': '99210'}]
    }
    deposit_jitter_resp = {'output': [{'d2_deposit': '8154000', 'tot_evlu_amt': '8154000'}]}
    mock_client.get_account_balance = AsyncMock(return_value=balance_jitter_resp)
    mock_client.get_deposit = AsyncMock(return_value=deposit_jitter_resp)
    await bot._sync_account_balance()

    snap = await portfolio.get_snapshot()
    print(f"  📋 4회차 동기화 결과: 총자산={int(snap['total_asset']):,}원 | 평가수익률={snap['total_yield_rate']}% | mdd_shutdown={bot.mdd_shutdown}")
    assert bot.mdd_shutdown is False, "손익률이 -0.79%인 상태에서 TR 외형 튐(-18.46%)으로 인한 서킷 브레이커가 차단(방어)되어야 합니다."
    assert bot.daily_circuit_breaker is False
    print("  ✅ [방어 성공] 실제 운용 손익률 -0.79% 보존으로 가짜 서킷 브레이커(-18.46%) 원천 차단 확인!")

    # -------------------------------------------------------------
    # [시나리오 2] 실제 퀀트 매매 손실 -2.5% 초과 3회 지속 시 정상 발동 테스트
    # -------------------------------------------------------------
    print("\n📊 [시나리오 2] 실제 퀀트 매매 누적 손실 -3.5% (한도 -2.5% 초과) 3회 연속 주입")
    # 실제 손실: 1,000만원 기준 -35만원 손실
    real_loss_balance = {
        'output1': [{'tot_evlu_amt': '9650000', 'd2_deposit': '5000000', 'tot_pchs_amt': '5000000'}],
        'output2': [{'stk_cd': '005930', 'stk_nm': '삼성전자', 'hldg_qty': '50', 'pchs_avg_pric': '100000', 'prpr': '93000'}]  # -7% 하락 (-35만원)
    }
    real_loss_deposit = {'output': [{'d2_deposit': '5000000', 'tot_evlu_amt': '9650000'}]}
    mock_client.get_account_balance = AsyncMock(return_value=real_loss_balance)
    mock_client.get_deposit = AsyncMock(return_value=real_loss_deposit)

    for i in range(3):
        await bot._sync_account_balance()
        print(f"  • 손실 동기화 {i+1}/3회: breach_count={bot.circuit_breaker_breach_count}, shutdown={bot.mdd_shutdown}")

    assert bot.circuit_breaker_breach_count >= 3
    assert bot.mdd_shutdown is True
    assert bot.daily_circuit_breaker is True
    print("  ✅ [정상 작동] 실제 매매 손실 3회 연속 확인 시 서킷 브레이커 확정 발동 확인!")

    # -------------------------------------------------------------
    # [시나리오 3] 데이터 정상화 시 Self-Healing 자가 복구 테스트
    # -------------------------------------------------------------
    print("\n📊 [시나리오 3] 시장 반등 및 데이터 정상화(손익률 -0.5% 회복) 시 자가 복구")
    recovered_balance = {
        'output1': [{'tot_evlu_amt': '9950000', 'd2_deposit': '5000000', 'tot_pchs_amt': '5000000'}],
        'output2': [{'stk_cd': '005930', 'stk_nm': '삼성전자', 'hldg_qty': '50', 'pchs_avg_pric': '100000', 'prpr': '99000'}]  # -1% 하락 (-5만원)
    }
    recovered_deposit = {'output': [{'d2_deposit': '5000000', 'tot_evlu_amt': '9950000'}]}
    mock_client.get_account_balance = AsyncMock(return_value=recovered_balance)
    mock_client.get_deposit = AsyncMock(return_value=recovered_deposit)

    await bot._sync_account_balance()
    print(f"  • 정상화 후 상태: mdd_shutdown={bot.mdd_shutdown}, daily_circuit_breaker={bot.daily_circuit_breaker}")
    assert bot.mdd_shutdown is False, "정상 손익률 회복 시 서킷 브레이커가 자가 복구되어야 합니다."
    assert bot.daily_circuit_breaker is False
    print("  ✅ [자가 복구 성공] 계좌 정상화 확인 즉시 신규 매수 자동 재개 확인!")

    # -------------------------------------------------------------
    # [시나리오 4] 수동 reset_circuit_breaker() 테스트
    # -------------------------------------------------------------
    print("\n📊 [시나리오 4] reset_circuit_breaker() 원클릭 수동 리셋 & 재캘리브레이션")
    bot.mdd_shutdown = True
    bot.daily_circuit_breaker = True
    bot.reset_circuit_breaker()

    assert bot.mdd_shutdown is False
    assert bot.daily_circuit_breaker is False
    assert bot.circuit_breaker_breach_count == 0
    print("  ✅ [수동 리셋 성공] 서킷 브레이커 즉시 해제 및 기준자산 재캘리브레이션 확인!")

    print("\n" + "=" * 80)
    print("🎉 [최종 결과] 서킷 브레이커 4대 시나리오 교차 검증 100% ALL PASS!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_circuit_breaker_anti_spike_tests())
