"""
가치평가 및 펀더멘털 스코어링 엔진 (FundamentalValuationEngine) 단위 테스트
- 재무비율, 피오트로스키 F-Score(9점), 그레이엄 넘버 및 안전마진율, 유니버스 스크리닝 검증
"""
import pytest
from valuation import FundamentalValuationEngine


@pytest.fixture
def sample_financials():
    """테스트용 가상 재무제표 데이터 (당기 및 전기)"""
    curr = {
        'net_income': 150000000.0,      # 1.5억 (전기 1.0억 대비 증가)
        'total_assets': 1000000000.0,    # 10억
        'total_equity': 600000000.0,     # 6억
        'total_liabilities': 400000000.0, # 4억 (부채비율 66.7%)
        'revenue': 800000000.0,          # 8억 (전기 6억 대비 증가)
        'operating_income': 180000000.0, # 1.8억
        'gross_profit': 300000000.0,     # 3.0억 (매출총이익률 37.5%)
        'cfo': 200000000.0,              # 2.0억 (CFO > Net Income 만족)
        'current_assets': 500000000.0,   # 유동자산 5억
        'current_liabilities': 250000000.0, # 유동부채 2.5억 (유동비율 200%)
        'shares_outstanding': 100000.0,  # 10만주 (전기 동일)
        'current_price': 50000.0
    }
    prev = {
        'net_income': 100000000.0,      # 1.0억
        'total_assets': 900000000.0,     # 9억
        'total_equity': 500000000.0,     # 5억
        'total_liabilities': 400000000.0, # 4억 (부채비율 80.0%)
        'revenue': 600000000.0,          # 6억
        'operating_income': 120000000.0,
        'gross_profit': 200000000.0,     # 2.0억 (매출총이익률 33.3%)
        'cfo': 120000000.0,
        'current_assets': 400000000.0,
        'current_liabilities': 250000000.0, # 유동비율 160%
        'shares_outstanding': 100000.0
    }
    return curr, prev


def test_compute_financial_ratios(sample_financials):
    """핵심 재무비율 계산 및 정합성 검증"""
    curr, _ = sample_financials
    ratios = FundamentalValuationEngine.compute_financial_ratios(curr, current_price=50000.0)

    # EPS = 1.5억 / 10만주 = 1,500원
    assert ratios['eps'] == 1500.0
    # BPS = 6.0억 / 10만주 = 6,000원
    assert ratios['bps'] == 6000.0
    # PER = 50,000 / 1,500 = 33.33
    assert abs(ratios['per'] - (50000.0 / 1500.0)) < 1e-4
    # PBR = 50,000 / 6,000 = 8.33
    assert abs(ratios['pbr'] - (50000.0 / 6000.0)) < 1e-4
    # ROE = 1.5억 / 6억 * 100 = 25.0%
    assert ratios['roe'] == 25.0
    # ROA = 1.5억 / 10억 * 100 = 15.0%
    assert ratios['roa'] == 15.0
    # 부채비율 = 4억 / 6억 * 100 = 66.67%
    assert abs(ratios['debt_to_equity'] - (400.0 / 6.0)) < 1e-4


def test_piotroski_f_score_perfect_score(sample_financials):
    """피오트로스키 F-Score 9점 만점 시나리오 검증"""
    curr, prev = sample_financials
    total_score, breakdown = FundamentalValuationEngine.calculate_piotroski_f_score(curr, prev)

    assert total_score == 9
    assert breakdown['f_roa'] == 1
    assert breakdown['f_cfo'] == 1
    assert breakdown['f_delta_roa'] == 1
    assert breakdown['f_accrual'] == 1
    assert breakdown['f_delta_leverage'] == 1
    assert breakdown['f_delta_liquidity'] == 1
    assert breakdown['f_no_dilution'] == 1
    assert breakdown['f_delta_gross_margin'] == 1
    assert breakdown['f_delta_turnover'] == 1


def test_piotroski_f_score_deteriorated():
    """재무 악화 기업의 F-Score 감점 검증"""
    curr = {
        'net_income': -5000.0,
        'total_assets': 10000.0,
        'total_equity': 4000.0,
        'total_liabilities': 6000.0,
        'revenue': 8000.0,
        'gross_profit': 1000.0,
        'cfo': -6000.0,
        'current_assets': 2000.0,
        'current_liabilities': 3000.0,
        'shares_outstanding': 1200.0  # 신주 발행 (희석)
    }
    prev = {
        'net_income': 1000.0,
        'total_assets': 9000.0,
        'total_equity': 5000.0,
        'total_liabilities': 4000.0,
        'revenue': 10000.0,
        'gross_profit': 3000.0,
        'cfo': 1500.0,
        'current_assets': 4000.0,
        'current_liabilities': 2000.0,
        'shares_outstanding': 1000.0
    }
    total_score, breakdown = FundamentalValuationEngine.calculate_piotroski_f_score(curr, prev)
    assert total_score == 0


def test_graham_number_calculation():
    """그레이엄 넘버 및 안전마진율 검증"""
    eps = 2000.0
    bps = 20000.0
    # sqrt(22.5 * 2000 * 20000) = sqrt(900,000,000) = 30,000
    res = FundamentalValuationEngine.calculate_graham_number(eps, bps, current_price=24000.0)
    assert res['graham_number'] == 30000.0
    assert res['is_undervalued'] is True
    # (30000 - 24000) / 30000 * 100 = 20.0%
    assert res['margin_of_safety_pct'] == 20.0

    # 고평가 주식
    overvalued = FundamentalValuationEngine.calculate_graham_number(eps, bps, current_price=40000.0)
    assert overvalued['is_undervalued'] is False
    assert overvalued['margin_of_safety_pct'] < 0


def test_screen_universe(sample_financials):
    """펀더멘털 스크리닝 필터 검증 (F-Score >= 6, 부채비율 <= 150%, ROE >= 8%)"""
    curr, prev = sample_financials
    good_stock = {
        'code': '005930',
        'name': '삼성전자',
        'current_price': 50000.0,
        'current_financials': curr,
        'prev_financials': prev
    }
    bad_stock = {
        'code': '999999',
        'name': '부실기업',
        'current_price': 10000.0,
        'current_financials': {
            'net_income': -1000.0,
            'total_assets': 10000.0,
            'total_equity': 1000.0,
            'total_liabilities': 9000.0, # 부채비율 900%
            'revenue': 5000.0,
            'current_assets': 1000.0,
            'current_liabilities': 3000.0,
            'shares_outstanding': 100.0
        },
        'prev_financials': {}
    }

    screened = FundamentalValuationEngine.screen_universe([good_stock, bad_stock])
    assert len(screened) == 1
    assert screened[0]['code'] == '005930'
    assert screened[0]['f_score'] == 9
    assert screened[0]['screen_passed'] is True
