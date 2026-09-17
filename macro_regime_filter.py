"""
거시 시장(Macro) 레짐 필터 및 글로벌 지수 급락 감지 모듈 (Macro Regime Filter)
- KODEX 200 (069500), 나스닥 100 선물(NQ), S&P500 선물(ES), VIX(공포지수), USD/KRW 환율 분석
- 시장 레짐(Market Regime) 3단계 분류:
  1) BULL_TREND (강세장): 켈리 최대 배분(20~25%), ATR 변동성 돌파 및 피보나치 적극 진입
  2) NEUTRAL_RANGE (박스권/횡보장): 켈리 축소 배분(10~15%), 타이트한 본절 가드 및 분할 익절
  3) PANIC_CRASH (급락장/서킷브레이커): 신규 매수 전면 차단 (Risk-Off)
"""
from enum import Enum
from typing import Dict, Any, Optional, Tuple
from datetime import datetime


class MarketRegime(Enum):
    BULL_TREND = "BULL_TREND"          # 상승 추세 (적극 매매)
    NEUTRAL_RANGE = "NEUTRAL_RANGE"    # 박스권/횡보 (보수적 매매)
    PANIC_CRASH = "PANIC_CRASH"        # 시장 급락 (매수 차단)


class MacroRegimeFilter:
    """거시 경제 및 시장 위험 평가 필터"""

    def __init__(self,
                 kodex_crash_threshold: float = -1.5,
                 vix_panic_threshold: float = 28.0,
                 usdkrw_spike_threshold_pct: float = 1.0):
        self.kodex_crash_threshold = kodex_crash_threshold
        self.vix_panic_threshold = vix_panic_threshold
        self.usdkrw_spike_threshold_pct = usdkrw_spike_threshold_pct

        self.current_regime: MarketRegime = MarketRegime.BULL_TREND
        self.last_kodex200_rate: float = 0.0
        self.last_vix: float = 18.0
        self.last_usdkrw_rate: float = 0.0
        self.last_evaluated_at: Optional[datetime] = None

    def evaluate_regime(self,
                        kodex200_change_pct: float,
                        vix_value: Optional[float] = None,
                        usdkrw_change_pct: Optional[float] = None,
                        us_futures_change_pct: Optional[float] = None) -> Tuple[MarketRegime, str]:
        """
        국내외 복합 매크로 지표를 종합하여 현재 시장 레짐 평가
        """
        self.last_kodex200_rate = kodex200_change_pct
        self.last_vix = vix_value if vix_value is not None else 18.0
        self.last_usdkrw_rate = usdkrw_change_pct if usdkrw_change_pct is not None else 0.0
        self.last_evaluated_at = datetime.now()

        # 1. 급락장 판정 (PANIC_CRASH)
        # KODEX 200이 -1.5% 이하로 급락하거나, VIX가 28 초과로 폭등할 때
        if kodex200_change_pct <= self.kodex_crash_threshold:
            self.current_regime = MarketRegime.PANIC_CRASH
            return MarketRegime.PANIC_CRASH, f"국내지수_급락경보(KODEX200 {kodex200_change_pct:.2f}%<={self.kodex_crash_threshold}%)"

        if self.last_vix >= self.vix_panic_threshold:
            self.current_regime = MarketRegime.PANIC_CRASH
            return MarketRegime.PANIC_CRASH, f"글로벌_변동성공포(VIX {self.last_vix:.1f}>={self.vix_panic_threshold})"

        # 2. 횡보/중립장 판정 (NEUTRAL_RANGE)
        # KODEX 200이 약세(-0.5% ~ -1.5%)이거나, 원달러 환율이 급등(+1.0% 이상)할 때
        if kodex200_change_pct < -0.5 or self.last_usdkrw_rate >= self.usdkrw_spike_threshold_pct:
            self.current_regime = MarketRegime.NEUTRAL_RANGE
            return MarketRegime.NEUTRAL_RANGE, f"시장_조정구간(KODEX200 {kodex200_change_pct:.2f}%, 환율 {self.last_usdkrw_rate:+.2f}%)"

        # 3. 강세/정상장 판정 (BULL_TREND)
        self.current_regime = MarketRegime.BULL_TREND
        return MarketRegime.BULL_TREND, f"시장_안정상승(KODEX200 {kodex200_change_pct:+.2f}%)"

    def get_regime_kelly_multiplier(self) -> float:
        """시장 레짐에 따른 자산 배분 비중 승수 반환"""
        if self.current_regime == MarketRegime.BULL_TREND:
            return 1.0       # 100% 정상 켈리 비중 (최대 25%)
        elif self.current_regime == MarketRegime.NEUTRAL_RANGE:
            return 0.6       # 60% 축소 켈리 비중 (최대 15%)
        else:
            return 0.0       # 0% 신규 매수 차단

    def is_buy_allowed(self) -> bool:
        """신규 매수 허용 여부"""
        return self.current_regime != MarketRegime.PANIC_CRASH
