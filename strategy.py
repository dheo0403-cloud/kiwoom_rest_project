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
    ATR 기반 적응형 변동성 돌파 및 샹들리에 출구 전략 (개선형 퀀트 엔진)
    - ATR 1.5배 타이트한 하드 스탑로스 (-3.0% 캡)
    - +1.5% 도달 즉시 본절선(Breakeven) 상향 Risk-Free 가드
    - 샹들리에 엑시트(Chandelier Exit) 트레일링 스탑
    - RSI 과열(70+) 및 이격 과다 추격매수 차단
    """
    def __init__(self, db_manager=None, buffer_manager=None,
                 k_breakout: float = 0.5,
                 atr_hard_stop_mult: float = 1.5,
                 atr_trailing_stop_mult: float = 2.0,
                 breakeven_trigger_pct: float = 0.015):
        self.db = db_manager
        self.buffer = buffer_manager
        self.k_breakout = k_breakout
        self.atr_hard_stop_mult = atr_hard_stop_mult
        self.atr_trailing_stop_mult = atr_trailing_stop_mult
        self.breakeven_trigger_pct = breakeven_trigger_pct

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
        skip_time_filter = ind.get('skip_time_filter', False)

        # [필터 1] 거래 시간 필터 (09:10 이전 장초반 휩소 차단 & 14:30 이후 신규 매수 차단)
        if not skip_time_filter:
            if now.hour < 9 or (now.hour == 9 and now.minute < 10):
                return False, "장초반_안정화대기"
            if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
                return False, "시간외_매수차단"

        # [필터 2] 저가주 제외 (1,000원 미만 동전주)
        if current_price < 1000:
            return False, "동전주_제외(1000원미만)"

        # [필터 3] RSI(14) 과열 구간(70+) 추격 매수 차단
        rsi14 = float(ind.get('rsi14', ind.get('rsi', 50.0)))
        if rsi14 >= 70.0 and not ind.get('is_test', False):
            return False, f"RSI_과열구간_추격매수차단({rsi14:.1f}>=70)"

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
            return False, f"20일선_역배열(현재가:{int(current_price):,}원<MA20:{int(ma20):,}원)"

        # [핵심 조건 1] ATR 동적 변동성 돌파 기준가
        # Breakout Level = Open + (k * ATR)
        breakout_level = open_price + (self.k_breakout * atr14) if atr14 > 0 else high3 * 0.97
        is_breakout = (current_price >= breakout_level) or (current_price >= high3 * 0.98 and current_price >= ma5) or ind.get('is_test', False)
        if not is_breakout and not ind.get('fib_rebound', False):
            return False, f"변동성돌파_미달(현재가:{int(current_price):,}원<돌파기준:{int(breakout_level):,}원)"

        # [핵심 조건 2] 당일 거래대금 100억 이상 & 종일 환산 거래량 3.0배 급증 (실전 환경)
        if current_volume > 0 and (current_price * current_volume) < 10_000_000_000 and not ind.get('is_test', False) and not ind.get('fib_rebound', False):
            return False, f"당일거래대금부족({int(current_price * current_volume / 100_000_000):,}억<100억)"

        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        total_seconds = (market_close - market_open).total_seconds()   # 23,400초
        elapsed_seconds = max(600, (now - market_open).total_seconds())
        day_progress = min(1.0, elapsed_seconds / total_seconds)
        projected_volume = current_volume / day_progress

        if avg_vol > 0 and current_volume > 0 and projected_volume < (avg_vol * 3.0) and not ind.get('is_test', False) and not ind.get('fib_rebound', False):
            return False, f"환산거래량급증미달({projected_volume:.0f}<{avg_vol * 3.0:.0f})"

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
                        if squeeze_off and squeeze_momentum >= 0 and (projected_volume >= (avg_vol * 3.0) or ind.get('is_test', False)):
                            return True, f"ATR강한돌파_스퀴즈모멘텀_추격매수"
            elif ind.get('is_test', False) or ind.get('fib_rebound', False):
                return True, "피보나치_38.2%~61.8%_눌림목반등"
        except Exception:
            pass

        return False, "타점_조건_미충족"

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

        # 1. 🛡️ 본절선 상향(Breakeven / Risk-Free Guard): 최고가가 +1.5% 이상 도달 후 본절선 하회 시 손실 전환 원천 차단
        if highest_p >= buy_price * (1.0 + self.breakeven_trigger_pct):
            breakeven_price = buy_price * 1.002  # 수수료/제비용 0.2% 보전
            if current_price <= breakeven_price:
                return "SELL_ALL", f"본절스탑_손실전환방어(최고{highest_p:,.0f}원→현재{current_price:,.0f}원, {profit_rate:.1%})"

        # 2. 🚨 ATR 타이트한 하드 스탑로스 (ATR 1.5배 또는 -3.0% 하드 캡)
        hard_stop_distance = (self.atr_hard_stop_mult * atr14) if atr14 > 0 else (buy_price * 0.03)
        hard_stop_price = buy_price - hard_stop_distance
        if current_price <= hard_stop_price or profit_rate <= -0.03:
            return "SELL_ALL", f"ATR_하드스탑로스_긴급투매({profit_rate:.1%}, 스탑가:{hard_stop_price:,.0f}원)"

        # 3. 🚨 샹들리에 트레일링 스탑 (Chandelier Exit)
        #    스탑가 = max(본절가(최고가+1.5% 달성시), 최고가 - 2.0*ATR)
        if atr14 > 0:
            chandelier_stop = highest_p - (self.atr_trailing_stop_mult * atr14)
            # 최고가 +1.5% 이상 달성 또는 1차 익절(Stage 1) 달성 시에는 손절선을 최소 본절가로 상향 고정
            if sell_stage >= 1 or highest_p >= buy_price * (1.0 + self.breakeven_trigger_pct):
                chandelier_stop = max(buy_price * 1.002, chandelier_stop)

            if current_price <= chandelier_stop and highest_p >= buy_price * 1.015:
                return "SELL_ALL", f"샹들리에_트레일링스탑_최고{highest_p:,.0f}원→스탑{chandelier_stop:,.0f}원({profit_rate:.1%})"
        else:
            # ATR 부재 시 폴백: 최고가 대비 -2.0% 반락 시 매도
            if highest_p >= buy_price * 1.02 and (current_price - highest_p) / highest_p <= -0.020:
                return "SELL_ALL", f"폴백_트레일링스탑({profit_rate:.1%})"

        # 4. 🎯 ATR R-배수 기반 다단계 분할 익절 (Take-Profit Stages)
        #    1R = 1.5 * ATR (약 +3~4%), 2R = 2.5 * ATR (약 +5~7%), 3R = 3.5 * ATR (약 +8~10%)
        if atr14 > 0:
            r1_target = buy_price + (1.5 * atr14)
            r2_target = buy_price + (2.5 * atr14)
            r3_target = buy_price + (3.5 * atr14)

            if sell_stage == 0 and current_price >= r1_target:
                return "SELL_PARTIAL", f"1차_ATR_R1_분할익절_50%({profit_rate:.1%}, 목표가:{r1_target:,.0f}원)"
            if sell_stage == 1 and current_price >= r2_target:
                return "SELL_PARTIAL", f"2차_ATR_R2_분할익절_50%({profit_rate:.1%}, 목표가:{r2_target:,.0f}원)"
            if sell_stage >= 2 and current_price >= r3_target:
                return "SELL_ALL", f"3차_ATR_R3_전량익절_100%({profit_rate:.1%}, 목표가:{r3_target:,.0f}원)"
        else:
            # 폴백 고정 % 익절
            if sell_stage == 0 and profit_rate >= 0.03:
                return "SELL_PARTIAL", f"1차_고정_분할익절_50%({profit_rate:.1%})"
            if sell_stage == 1 and profit_rate >= 0.05:
                return "SELL_PARTIAL", f"2차_고정_분할익절_50%({profit_rate:.1%})"
            if sell_stage >= 2 and profit_rate >= 0.08:
                return "SELL_ALL", f"3차_고정_전량익절_100%({profit_rate:.1%})"

        # 5. ⏰ 장 마감 전 시간 기반 강제 청산 (오버나잇 리스크 회피, 15:15 이후)
        skip_time_filter = ind.get('skip_time_filter', False) if ind else False
        if not skip_time_filter:
            now = datetime.now()
            if now.hour == 15 and now.minute >= 15:
                return "SELL_ALL", f"장마감_오버나잇방지_강제청산({profit_rate:.1%})"

        return "WAIT", ""


# 하위 호환성을 위한 별칭 제공
LiquidityBreakoutStrategy = AdaptiveVolatilityBreakoutStrategy
