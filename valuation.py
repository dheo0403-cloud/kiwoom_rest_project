"""
FMP(Financial Modeling Prep) 호환 가치평가 및 펀더멘털 스코어링 엔진 (Fundamental Valuation Engine)
- 피오트로스키 F-Score (9점 만점 재무건전성 점수)
- 그레이엄 넘버(Graham Number) 및 안전마진율 산출
- 핵심 재무비율 (PER, PBR, PSR, EV/EBITDA, ROE, ROA, 부채비율, 유동비율) 자체 산출
- 펀더멘털 퀀트 유니버스 스크리닝 필터
"""
import math
from typing import Dict, Any, Tuple, List, Optional


class FundamentalValuationEngine:
    """FMP 표준 기반 펀더멘털 가치평가 및 F-Score 산출 클래스"""

    @staticmethod
    def compute_financial_ratios(data: Dict[str, Any], current_price: Optional[float] = None) -> Dict[str, float]:
        """
        단일 분기/연간 재무제표로부터 핵심 재무비율 벡터 산출
        """
        net_income = float(data.get('net_income', 0.0) or data.get('당기순이익', 0.0))
        total_assets = float(data.get('total_assets', 0.0) or data.get('자산총계', 0.0))
        total_equity = float(data.get('total_equity', 0.0) or data.get('자본총계', 0.0))
        total_liabilities = float(data.get('total_liabilities', 0.0) or data.get('부채총계', 0.0))
        revenue = float(data.get('revenue', 0.0) or data.get('매출액', 0.0))
        operating_income = float(data.get('operating_income', 0.0) or data.get('영업이익', 0.0))
        cfo = float(data.get('cfo', 0.0) or data.get('operating_cash_flow', 0.0) or data.get('영업활동현금흐름', 0.0))
        shares_outstanding = float(data.get('shares_outstanding', 0.0) or data.get('상장주식수', 1.0))
        if shares_outstanding <= 0:
            shares_outstanding = 1.0

        eps = float(data.get('eps', 0.0)) or (net_income / shares_outstanding if net_income else 0.0)
        bps = float(data.get('bps', 0.0)) or (total_equity / shares_outstanding if total_equity else 0.0)
        sps = float(data.get('sps', 0.0)) or (revenue / shares_outstanding if revenue else 0.0)

        price = float(current_price or data.get('current_price', 0.0) or data.get('close', 0.0))
        market_cap = float(data.get('market_cap', 0.0)) or (price * shares_outstanding)

        # 1. 수익성 비율
        roe = (net_income / total_equity * 100.0) if total_equity > 0 else 0.0
        roa = (net_income / total_assets * 100.0) if total_assets > 0 else 0.0
        op_margin = (operating_income / revenue * 100.0) if revenue > 0 else 0.0

        # 2. 안정성/유동성 비율
        debt_to_equity = (total_liabilities / total_equity * 100.0) if total_equity > 0 else 0.0
        current_assets = float(data.get('current_assets', 0.0) or data.get('유동자산', 0.0))
        current_liabilities = float(data.get('current_liabilities', 0.0) or data.get('유동부채', 0.0))
        current_ratio = (current_assets / current_liabilities * 100.0) if current_liabilities > 0 else 100.0

        # 3. 시장 가치 비율
        per = (price / eps) if eps > 0 else 0.0
        pbr = (price / bps) if bps > 0 else 0.0
        psr = (price / sps) if sps > 0 else 0.0

        # 4. EV / EBITDA
        total_debt = float(data.get('total_debt', total_liabilities))
        cash = float(data.get('cash', 0.0) or data.get('현금및현금성자산', 0.0))
        ev = market_cap + total_debt - cash
        ebitda = float(data.get('ebitda', 0.0)) or (operating_income + float(data.get('depreciation', 0.0)))
        ev_ebitda = (ev / ebitda) if ebitda > 0 else 0.0

        return {
            'eps': eps,
            'bps': bps,
            'sps': sps,
            'roe': roe,
            'roa': roa,
            'op_margin': op_margin,
            'debt_to_equity': debt_to_equity,
            'current_ratio': current_ratio,
            'per': per,
            'pbr': pbr,
            'psr': psr,
            'ev_ebitda': ev_ebitda,
            'market_cap': market_cap
        }

    @classmethod
    def calculate_piotroski_f_score(cls, curr: Dict[str, Any], prev: Dict[str, Any]) -> Tuple[int, Dict[str, int]]:
        """
        피오트로스키 F-Score (Piotroski 9-point Fundamental Scoring) 산출
        - 수익성 (4점): ROA 양수, CFO 양수, delta ROA 양수, Accrual 품질(CFO > Net Income)
        - 레버리지/유동성 (3점): delta 부채비율 음수, delta 유동비율 양수, 주식발행(희석) 없음
        - 영업 효율성 (2점): delta 매출총이익률 양수, delta 총자산회전율 양수
        """
        scores = {}

        # 당기 & 전기 기본 데이터 추출
        net_income_curr = float(curr.get('net_income', 0.0))
        assets_curr = float(curr.get('total_assets', 1.0))
        assets_prev = float(prev.get('total_assets', 1.0))
        equity_curr = float(curr.get('total_equity', 1.0))
        equity_prev = float(prev.get('total_equity', 1.0))
        cfo_curr = float(curr.get('cfo', 0.0) or curr.get('operating_cash_flow', 0.0))
        revenue_curr = float(curr.get('revenue', 0.0))
        revenue_prev = float(prev.get('revenue', 0.0))
        gross_profit_curr = float(curr.get('gross_profit', 0.0)) or float(curr.get('operating_income', 0.0))
        gross_profit_prev = float(prev.get('gross_profit', 0.0)) or float(prev.get('operating_income', 0.0))

        # 1. 수익성 지표 (Profitability - 4점)
        roa_curr = (net_income_curr / assets_curr) if assets_curr > 0 else 0.0
        net_income_prev = float(prev.get('net_income', 0.0))
        roa_prev = (net_income_prev / assets_prev) if assets_prev > 0 else 0.0

        scores['f_roa'] = 1 if roa_curr > 0 else 0
        scores['f_cfo'] = 1 if cfo_curr > 0 else 0
        scores['f_delta_roa'] = 1 if roa_curr > roa_prev else 0
        scores['f_accrual'] = 1 if cfo_curr > net_income_curr else 0

        # 2. 레버리지 / 유동성 / 자금조달 (3점)
        liab_curr = float(curr.get('total_liabilities', 0.0))
        liab_prev = float(prev.get('total_liabilities', 0.0))
        debt_ratio_curr = (liab_curr / equity_curr) if equity_curr > 0 else 0.0
        debt_ratio_prev = (liab_prev / equity_prev) if equity_prev > 0 else 0.0
        scores['f_delta_leverage'] = 1 if debt_ratio_curr <= debt_ratio_prev else 0

        curr_assets_curr = float(curr.get('current_assets', 0.0))
        curr_liab_curr = float(curr.get('current_liabilities', 1.0))
        curr_assets_prev = float(prev.get('current_assets', 0.0))
        curr_liab_prev = float(prev.get('current_liabilities', 1.0))
        current_ratio_curr = curr_assets_curr / curr_liab_curr if curr_liab_curr > 0 else 0.0
        current_ratio_prev = curr_assets_prev / curr_liab_prev if curr_liab_prev > 0 else 0.0
        scores['f_delta_liquidity'] = 1 if current_ratio_curr >= current_ratio_prev else 0

        shares_curr = float(curr.get('shares_outstanding', 0.0))
        shares_prev = float(prev.get('shares_outstanding', 0.0))
        scores['f_no_dilution'] = 1 if (shares_curr <= shares_prev or shares_prev == 0.0) else 0

        # 3. 영업 효율성 (2점)
        gm_curr = (gross_profit_curr / revenue_curr) if revenue_curr > 0 else 0.0
        gm_prev = (gross_profit_prev / revenue_prev) if revenue_prev > 0 else 0.0
        scores['f_delta_gross_margin'] = 1 if gm_curr >= gm_prev else 0

        turnover_curr = (revenue_curr / assets_curr) if assets_curr > 0 else 0.0
        turnover_prev = (revenue_prev / assets_prev) if assets_prev > 0 else 0.0
        scores['f_delta_turnover'] = 1 if turnover_curr >= turnover_prev else 0

        total_score = sum(scores.values())
        return total_score, scores

    @staticmethod
    def calculate_graham_number(eps: float, bps: float, current_price: float) -> Dict[str, float]:
        """
        벤저민 그레이엄 넘버 (Graham Number) 및 안전마진율 산출
        - Graham Number = sqrt(22.5 * EPS * BPS)
        """
        if eps <= 0 or bps <= 0:
            return {
                'graham_number': 0.0,
                'margin_of_safety_pct': -100.0,
                'is_undervalued': False
            }

        graham_num = math.sqrt(22.5 * eps * bps)
        margin_of_safety = ((graham_num - current_price) / graham_num * 100.0) if graham_num > 0 else 0.0
        is_undervalued = current_price <= graham_num

        return {
            'graham_number': round(graham_num, 2),
            'margin_of_safety_pct': round(margin_of_safety, 2),
            'is_undervalued': is_undervalued
        }

    @classmethod
    def screen_universe(
        cls,
        stock_list: List[Dict[str, Any]],
        min_f_score: int = 6,
        max_debt_to_equity: float = 150.0,
        min_roe: float = 8.0
    ) -> List[Dict[str, Any]]:
        """
        퀀트 1단계: 펀더멘털 우량주 유니버스 스크리닝 필터
        - F-Score >= 6, 부채비율 <= 150%, ROE >= 8% 통과 종목만 선별
        """
        screened = []
        for stock in stock_list:
            curr_data = stock.get('current_financials', stock)
            prev_data = stock.get('prev_financials', {})

            ratios = cls.compute_financial_ratios(curr_data, current_price=stock.get('current_price'))
            f_score, _ = cls.calculate_piotroski_f_score(curr_data, prev_data) if prev_data else (curr_data.get('f_score', 7), {})

            roe = ratios['roe']
            debt_ratio = ratios['debt_to_equity']

            if f_score >= min_f_score and debt_ratio <= max_debt_to_equity and roe >= min_roe:
                item = dict(stock)
                item.update({
                    'f_score': f_score,
                    'roe': roe,
                    'debt_to_equity': debt_ratio,
                    'per': ratios['per'],
                    'pbr': ratios['pbr'],
                    'screen_passed': True
                })
                screened.append(item)

        # F-Score 및 ROE 기준 내림차순 정렬
        screened.sort(key=lambda x: (x.get('f_score', 0), x.get('roe', 0.0)), reverse=True)
        return screened
