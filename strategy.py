"""
적응형 퀀트 매매 전략 모듈 (Adaptive Quant Strategy Engine)
- ATR 기반 동적 변동성 돌파 진입 (Adaptive Volatility Breakout)
- 샹들리에 엑시트(Chandelier Exit) 동적 트레일링 스탑 & R-배수 다단계 분할 익절
- 볼린저 밴드 + 켈트너 채널 스퀴즈 모멘텀(Squeeze Momentum) 필터
- 인메모리 링버퍼(MarketDataBuffer) 초고속 피보나치 눌림목 판정
"""
from datetime import datetime
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np


class AdaptiveVolatilityBreakoutStrategy:
    """
    ATR 기반 적응형 변동성 돌파 및 샹들리에 출구 전략
    """
    def __init__(self, db_manager=None, buffer_manager=None,
                 k_breakout: float = 0.5,
                 atr_hard_stop_mult: float = 2.0,
                 atr_trailing_stop_mult: float = 2.5):
        self.db = db_manager
        self.buffer = buffer_manager
        self.k_breakout = k_breakout
        self.atr_hard_stop_mult = atr_hard_stop_mult
        self.atr_trailing_stop_mult = atr_trailing_stop_mult

    def set_buffer_manager(self, buffer_manager):
        """인메모리 링버퍼 매니저 설정"""
        self.buffer = buffer_manager

    async def check_buy_signal(self, code: str, current_price: float, current_volume: float,
                               ind: Dict[str, Any]) -> Tuple[bool, str]:
        """
        ATR 동적 변동성 돌파 및 피보나치 눌림목 매수 시그널 검증
        """
        if not ind:
            return False, ""

        now = datetime.now()

        # [필터 1] 거래 시간 필터 (09:10 이전 장초반 휩소 차단 & 14:30 이후 신규 매수 차단)
        if now.hour < 9 or (now.hour == 9 and now.minute < 10):
            return False, "장초반_안정화대기"
        if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
            return False, "시간외_매수차단"

        # [필터 2] 저가주 제외 (1,000원 미만 동전주)
        if current_price < 1000:
            return False, ""

        ma5 = ind.get('ma5', 0)
        ma20 = ind.get('ma20', 0)
        high3 = ind.get('high3', ind.get('high10', current_price))
        avg_vol = ind.get('avg_vol', 0)
        atr14 = ind.get('atr14', 0)
        open_price = ind.get('open', current_price)
        squeeze_off = ind.get('squeeze_off', True)
        squeeze_momentum = ind.get('squeeze_momentum', 0.0)

        # [추세 필터] 20일선(MA20) 역배열 하락 추세 매수 차단
        if ma20 > 0 and current_price < ma20:
            return False, ""

        # [핵심 조건 1] ATR 동적 변동성 돌파 기준가
        # Breakout Level = Open + (k * ATR)
        breakout_level = open_price + (self.k_breakout * atr14) if atr14 > 0 else high3 * 0.97
        is_breakout = (current_price >= breakout_level) or (current_price >= high3 * 0.98 and current_price >= ma5)
        if not is_breakout:
            return False, ""

        # [핵심 조건 2] 당일 거래대금 100억 이상 & 종일 환산 거래량 3.0배 급증
        if (current_price * current_volume) < 10_000_000_000:
            return False, ""

        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        total_seconds = (market_close - market_open).total_seconds()   # 23,400초
        elapsed_seconds = max(600, (now - market_open).total_seconds())
        day_progress = min(1.0, elapsed_seconds / total_seconds)
        projected_volume = current_volume / day_progress

        if avg_vol > 0 and projected_volume < (avg_vol * 3.0):
            return False, ""

        # [핵심 조건 3] 인메모리 링버퍼 기반 피보나치 눌림목 또는 스퀴즈 모멘텀 반등 확인
        try:
            if self.buffer:
                df = self.buffer.get_dataframe(code, limit=10)
            elif self.db:
                query = f"SELECT * FROM minute_ohlcv WHERE code='{code}' ORDER BY datetime DESC LIMIT 10"
                df = await self.db.get_dataframe(query)
            else:
                df = pd.DataFrame()

            if not df.empty and len(df) >= 5:
                if 'datetime' in df.columns:
                    df = df.sort_values('datetime').reset_index(drop=True)

                highest = df['high'].max()
                lowest = df['low'].min()

                if highest > lowest:
                    fibo_236 = highest - (highest - lowest) * 0.236
                    # 23.6% 눌림목 구간 반등
                    if current_price <= fibo_236:
                        prev_candle = df.iloc[-1]
                        if current_price > prev_candle['open'] or prev_candle['close'] > prev_candle['open']:
                            return True, f"ATR돌파_피보나치23.6%눌림목반등"
                    else:
                        # 조정 없는 강력한 돌파 + 스퀴즈 모멘텀 양수 전환
                        if squeeze_off and squeeze_momentum >= 0 and projected_volume >= (avg_vol * 3.0):
                            return True, f"ATR강한돌파_스퀴즈모멘텀_추격매수"
        except Exception:
            pass

        return False, ""

    async def check_sell_signal(self, code: str, buy_price: float, current_price: float,
                                ind: Dict[str, Any], sell_stage: int = 0,
                                highest_price: Optional[float] = None) -> Tuple[str, str]:
        """
        ATR 샹들리에 엑시트 + R-배수 분할 익절 매도 시그널 생성
        Returns: (action, reason)
          action: "WAIT" / "SELL_ALL" / "SELL_PARTIAL"
        """
        if buy_price <= 0:
            return "WAIT", ""

        profit_rate = (current_price - buy_price) / buy_price
        highest_p = highest_price or current_price
        atr14 = float(ind.get('atr14', 0) if ind else 0)

        # 1. 🚨 하드 스탑로스 (ATR 2.0배 또는 -5% 하드 캡)
        hard_stop_distance = (self.atr_hard_stop_mult * atr14) if atr14 > 0 else (buy_price * 0.04)
        hard_stop_price = buy_price - hard_stop_distance
        if current_price <= hard_stop_price or profit_rate <= -0.05:
            return "SELL_ALL", f"ATR_하드스탑로스_긴급투매({profit_rate:.1%}, 스탑가:{hard_stop_price:,.0f}원)"

        # 2. 🚨 샹들리에 트레일링 스탑 (Chandelier Exit)
        #    스탑가 = max(본절가(Stage>=1시), 최고가 - 2.5*ATR)
        if atr14 > 0:
            chandelier_stop = highest_p - (self.atr_trailing_stop_mult * atr14)
            # 1차 익절(Stage 1) 이상 달성 시에는 손절선을 최소 본절가(buy_price)로 상향 고정 (Risk-Free)
            if sell_stage >= 1:
                chandelier_stop = max(buy_price, chandelier_stop)

            if current_price <= chandelier_stop and highest_p >= buy_price * 1.02:
                return "SELL_ALL", f"샹들리에_트레일링스탑_최고{highest_p:,.0f}원→스탑{chandelier_stop:,.0f}원({profit_rate:.1%})"
        else:
            # ATR 부재 시 폴백: 최고가 대비 -2.5% 반락 시 매도
            if highest_p >= buy_price * 1.03 and (current_price - highest_p) / highest_p <= -0.025:
                return "SELL_ALL", f"폴백_트레일링스탑({profit_rate:.1%})"

        # 3. 🎯 ATR R-배수 기반 다단계 분할 익절 (Take-Profit Stages)
        #    1R = 1.5 * ATR (약 +3~4%), 2R = 2.5 * ATR (약 +5~7%)
        if atr14 > 0:
            r1_target = buy_price + (1.5 * atr14)
            r2_target = buy_price + (2.5 * atr14)

            if sell_stage == 0 and current_price >= r1_target:
                return "SELL_PARTIAL", f"1차_ATR_R1_분할익절_33%({profit_rate:.1%}, 목표가:{r1_target:,.0f}원)"
            if sell_stage == 1 and current_price >= r2_target:
                return "SELL_PARTIAL", f"2차_ATR_R2_분할익절_50%({profit_rate:.1%}, 목표가:{r2_target:,.0f}원)"
        else:
            # 폴백 고정 % 익절
            if sell_stage == 0 and profit_rate >= 0.03:
                return "SELL_PARTIAL", f"1차_고정_분할익절_33%({profit_rate:.1%})"
            if sell_stage == 1 and profit_rate >= 0.05:
                return "SELL_PARTIAL", f"2차_고정_분할익절_50%({profit_rate:.1%})"

        # 4. ⏰ 장 마감 전 시간 기반 강제 청산 (오버나잇 리스크 회피, 15:15 이후)
        now = datetime.now()
        if now.hour == 15 and now.minute >= 15:
            return "SELL_ALL", f"장마감_오버나잇방지_강제청산({profit_rate:.1%})"

        return "WAIT", ""


# 하위 호환성을 위한 별칭 제공
LiquidityBreakoutStrategy = AdaptiveVolatilityBreakoutStrategy
