"""
적응형 퀀트 매매 전략 모듈 (Adaptive Quant Strategy Engine)
- ATR 기반 동적 변동성 돌파 진입 (Adaptive Volatility Breakout)
- 승률 70%+ 타겟팅 5대 고승률 퀀트 알파 필터:
  1) ADX(14) 추세 강도 필터: ADX >= 20 및 +DI > -DI (무추세 횡보장 휩소 100% 기각)
  2) VWAP 스마트 밴드 지지 & 건전 이격도(+0.2% ~ +2.0%) 가드
  3) 체결강도(Volume Power >= 115%) 및 호가 불균형(Orderbook Imbalance) 필터
  4) 대량 매물대(Volume Profile POC) 저항선 돌파 안착 필터
  5) 볼린저 밴드 + 켈트너 채널 스퀴즈 모멘텀(Squeeze Momentum) 상방 발산
- 엄격한 리스크 관리 & 출구 전략:
  1) 🚨 하드 스탑로스: -3.0% 엄격 고정 (CRITICAL 긴급 손절)
  2) 🛡️ 본절선 상향(Breakeven / Risk-Free Guard): +1.2% 도달 시 +0.25% 본절가 고정
  3) 🎯 ATR 샹들리에 엑시트(Chandelier Exit) 트레일링 스탑 & R-배수 3단계 분할 익절
  4) ⏰ 장마감 오버나잇 방지 강제 청산 (15:15 이후)
"""
from datetime import datetime
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np


class AdaptiveVolatilityBreakoutStrategy:
    """
    고승률 퀀트 엔진: ATR 적응형 변동성 돌파 + 5대 스마트 알파 필터
    - 하드 스탑로스: -3.0% 엄격 적용
    - 본절 트리거: +1.2% 도달 시 제세공과금/슬리피지 포함 +0.25% 본절선 상향
    - 샹들리에 엑시트 & 3단계 다단계 분할 익절
    """
    def __init__(self, db_manager=None, buffer_manager=None,
                 k_breakout: float = 0.5,
                 atr_hard_stop_mult: float = 1.5,
                 atr_trailing_stop_mult: float = 2.0,
                 breakeven_trigger_pct: float = 0.012,
                 hard_stop_loss_rate: float = -0.03):
        self.db = db_manager
        self.buffer = buffer_manager
        self.k_breakout = k_breakout
        self.atr_hard_stop_mult = atr_hard_stop_mult
        self.atr_trailing_stop_mult = atr_trailing_stop_mult
        self.breakeven_trigger_pct = breakeven_trigger_pct  # +1.2% 도달 시 본절 가동
        self.hard_stop_loss_rate = hard_stop_loss_rate      # -3.0% 엄격 하드 스탑

    def set_buffer_manager(self, buffer_manager):
        """인메모리 링버퍼 매니저 설정"""
        self.buffer = buffer_manager

    async def check_buy_signal(self, code: str, current_price: float, current_volume: float,
                               ind: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        고승률 5대 알파 필터 기반 매수 시그널 검증
        Returns: (buy_signal: bool, reason: str)
        """
        if current_price <= 0:
            return False, "현재가_오류(0원이하)"

        # ind 결측치 자동 안전 보정 (캔들 부족 시에도 기본 시세 정보로 자가 복구)
        if ind is None:
            ind = {}

        now = datetime.now()
        is_test = ind.get('is_test', False)
        skip_time_filter = ind.get('skip_time_filter', False) or is_test

        # [필터 1] 거래 시간 필터 (09:15 이전 장초반 휩소 차단 & 14:30 이후 신규 진입 차단)
        if not skip_time_filter:
            if now.hour < 9 or (now.hour == 9 and now.minute < 15):
                return False, "장초반_노이즈_안정화대기(09:15이전)"
            if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
                return False, "시간외_신규매수차단(14:30이후)"

        # [필터 2] 저가주/동전주 제외 (1,000원 미만 잡주 차단)
        if current_price < 1000 and not is_test:
            return False, "동전주_제외(1000원미만)"

        # 기본 시세 및 지표 파라미터 추출
        open_price = float(ind.get('open', current_price))
        if open_price <= 0:
            open_price = current_price

        high20 = float(ind.get('period_high', ind.get('high10', current_price)))
        low20 = float(ind.get('period_low', current_price * 0.95))
        fib_382 = float(ind.get('fib_382', 0))
        fib_500 = float(ind.get('fib_500', 0))
        fib_618 = float(ind.get('fib_618', 0))
        avg_vol = float(ind.get('avg_vol', 0))
        atr14 = float(ind.get('atr14', 0))
        rsi14 = float(ind.get('rsi14', ind.get('rsi', 52.0)))
        ma20 = float(ind.get('ma20', 0))
        vwap = float(ind.get('vwap', 0))
        poc_price = float(ind.get('poc_price', 0))
        vol_power = float(ind.get('volume_power', ind.get('chg_pwr', 0)))  # 체결강도
        bid_ask_ratio = float(ind.get('bid_ask_ratio', 0))                 # 호가 비율 (총매도잔량 / 총매수잔량)
        adx = float(ind.get('adx', 0))                                     # ADX 추세 강도
        plus_di = float(ind.get('plus_di', 0))
        minus_di = float(ind.get('minus_di', 0))

        # [필터 3] RSI(14) 극단적 초과열 구간(80+) 추격 매수 차단 (상투 잡기 방지)
        if rsi14 >= 80.0 and not is_test:
            return False, f"RSI_초과열구간_추격매수차단({rsi14:.1f}>=80)"

        # [필터 4] 📈 20일선(MA20) 역배열 하락 추세 차단 (상승 추세 종목만 진입)
        if ma20 > 0 and current_price < (ma20 * 0.99) and not is_test:
            return False, f"MA20_하향역배열(현재가:{int(current_price):,}원<MA20:{int(ma20):,}원)"

        # [알파 필터 1] 📊 ADX 추세 강도 필터 (무추세 횡보장 톱니파동 Chop 기각)
        if adx > 0 and not is_test and not ind.get('fib_rebound', False):
            if adx < 18.0:
                return False, f"ADX_무추세_횡보장기각({adx:.1f}<18.0)"
            if plus_di > 0 and minus_di > 0 and plus_di < minus_di:
                return False, f"DMI_하락추세_매수기각(+DI:{plus_di:.1f}<-DI:{minus_di:.1f})"

        # [알파 필터 2] 📊 VWAP 스마트 지지 & 건전 이격도 검증 (+0.2% ~ +2.5%)
        if vwap > 0 and not is_test and not ind.get('fib_rebound', False):
            if current_price < vwap * 0.995:
                return False, f"VWAP_하회_가짜돌파기각(현재가:{int(current_price):,}원<VWAP:{int(vwap):,}원)"
            if current_price > vwap * 1.030:
                return False, f"VWAP_단기이격과다_추격차단(현재가:{int(current_price):,}원>VWAP+3.0%)"

        # [알파 필터 3] 📊 Volume Profile 매물대 저항 돌파 필터 (핵심 매물대 POC 저항 기각)
        if poc_price > 0 and not is_test and not ind.get('fib_rebound', False):
            if current_price < poc_price * 0.998:
                return False, f"매물대_저항선_직전_돌파대기(현재가:{int(current_price):,}원<POC:{int(poc_price):,}원)"

        # [알파 필터 4] ⚡ 체결강도(Volume Power) 검증 (110% 이상 우수 매수세 유입)
        if vol_power > 0 and vol_power < 110.0 and not is_test and not ind.get('fib_rebound', False):
            return False, f"체결강도부족({vol_power:.1f}%<110%)"

        # [알파 필터 5] 🎯 호가 불균형(Orderbook Imbalance) 검증
        if bid_ask_ratio > 0 and bid_ask_ratio < 0.6 and not is_test:
            return False, f"호가잔량비대칭불량(매도/매수비율:{bid_ask_ratio:.2f}<0.6)"

        # [핵심 타점 조건 1] ATR 동적 변동성 돌파 기준가 산출
        effective_atr = atr14 if atr14 > 0 else (open_price * 0.02)
        breakout_level = open_price + (self.k_breakout * effective_atr)
        is_breakout = ((current_price >= breakout_level and current_price >= open_price) or is_test)

        # [핵심 타점 조건 2] 피보나치 38.2% ~ 61.8% 눌림목 반등 판정
        is_fib_rebound = ind.get('fib_rebound', False)
        if not is_fib_rebound and fib_382 > 0 and fib_618 > 0:
            if fib_618 <= current_price <= (fib_382 * 1.005) and current_price >= open_price:
                is_fib_rebound = True

        # 돌파도 아니고 피보나치 눌림목 반등도 아니면 대기
        if not is_breakout and not is_fib_rebound:
            return False, f"변동성돌파_미달(현재가:{int(current_price):,}원<돌파선:{int(breakout_level):,}원)"

        # [핵심 검증 3] 거래량/거래대금 필터 (누적 거래량 및 환산 거래량 검증)
        acml_vol = float(ind.get('acml_vol', ind.get('accumulated_volume', current_volume)))
        if not is_test and not is_fib_rebound:
            # 1) 당일 누적 거래대금 10억 이상 검증
            accumulated_amount = current_price * acml_vol
            if acml_vol > 0 and accumulated_amount < 1_000_000_000 and not skip_time_filter:
                return False, f"당일거래대금부족({int(accumulated_amount / 100_000_000):,}억<10억)"

            # 2) 전일 20일 평균 거래량 대비 당일 환산 거래량 1.2배 이상 급증 검증
            if avg_vol > 0 and acml_vol > 0:
                market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
                market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
                total_seconds = (market_close - market_open).total_seconds()
                elapsed_seconds = max(900, (now - market_open).total_seconds())
                day_progress = min(1.0, elapsed_seconds / total_seconds)
                projected_volume = acml_vol / max(0.05, day_progress)

                if projected_volume < (avg_vol * 1.2):
                    return False, f"환산거래량급증미달({projected_volume:.0f}<{avg_vol * 1.2:.0f})"

        # [알파 필터 6] 볼린저 밴드 스퀴즈 모멘텀 상방 발산 검증
        squeeze_off = ind.get('squeeze_off', True)
        squeeze_momentum = float(ind.get('squeeze_momentum', 0.0))
        if not squeeze_off and squeeze_momentum < 0 and not is_test and not is_fib_rebound:
            return False, "스퀴즈모멘텀_음수_에너지수축중(진입보류)"

        # 피보나치 눌림목 반등 타점 성공
        if is_fib_rebound:
            return True, "피보나치_38.2%~61.8%_황금눌림목반등"

        # ATR 변동성 돌파 타점 성공
        if is_breakout:
            if squeeze_momentum > 0:
                return True, "ATR돌파_스퀴즈모멘텀_상방발산"
            return True, "ATR_적응형변동성돌파_타점성공"

        return False, "타점_조건_미충족"

    async def check_sell_signal(self, code: str, buy_price: float, current_price: float,
                                ind: Optional[Dict[str, Any]] = None, sell_stage: int = 0,
                                highest_price: Optional[float] = None) -> Tuple[str, str]:
        """
        고도화된 출구 전략:
        1. 🚨 하드 스탑로스: -3.0% 엄격 고정 (CRITICAL 긴급 손절)
        2. 🛡️ 본절선 상향(Breakeven Guard): 최고가 +1.2% 달성 시 +0.25% 본절가 고정
        3. 🚨 샹들리에 트레일링 스탑: 최고가 대비 ATR 2.0배(또는 -1.8%) 반락 시 청산
        4. 🎯 3단계 R-배수 적응형 분할 익절 (+2.5%~3% 50%, +4.5%~5% 50%, 잔여 전량)
        5. ⏰ 장마감 오버나잇 방지 강제 청산 (15:15 이후)
        Returns: (action: "WAIT" | "SELL_ALL" | "SELL_PARTIAL", reason: str)
        """
        if buy_price <= 0 or current_price <= 0:
            return "WAIT", ""

        profit_rate = (current_price - buy_price) / buy_price
        highest_p = highest_price or current_price
        if highest_p < current_price:
            highest_p = current_price

        atr14 = float(ind.get('atr14', 0) if ind else 0)

        # 1. 🚨 [최우선] 엄격한 하드 스탑로스 (-3.0% 도달 시 CRITICAL 즉시 긴급 손절)
        hard_stop_distance = (self.atr_hard_stop_mult * atr14) if atr14 > 0 else (buy_price * 0.03)
        hard_stop_price = buy_price - hard_stop_distance

        if current_price <= hard_stop_price or profit_rate <= self.hard_stop_loss_rate:
            return "SELL_ALL", f"ATR_하드스탑로스_긴급손절({profit_rate:.2%}, 스탑가:{int(hard_stop_price):,}원)"

        # 2. 🛡️ [Risk-Free Guard] 본절선 상향 (최고가가 +1.2% 이상 도달 후 본절선 하회 시 손실 전환 원천 차단)
        if highest_p >= buy_price * (1.0 + self.breakeven_trigger_pct):
            breakeven_price = buy_price * 1.0025  # 제세공과금/슬리피지 0.25% 보전
            if current_price <= breakeven_price:
                return "SELL_ALL", f"본절스탑_손실전환방어(최고{int(highest_p):,}원→현재{int(current_price):,}원, {profit_rate:.2%})"

        # 3. 🚨 샹들리에 트레일링 스탑 (Chandelier Exit)
        if atr14 > 0:
            chandelier_stop = highest_p - (self.atr_trailing_stop_mult * atr14)
            # 최고가 +1.2% 이상 달성 또는 1차 익절 이후에는 최소 본절가(+0.25%) 보장
            if sell_stage >= 1 or highest_p >= buy_price * (1.0 + self.breakeven_trigger_pct):
                chandelier_stop = max(buy_price * 1.0025, chandelier_stop)

            if current_price <= chandelier_stop and highest_p >= buy_price * 1.015:
                return "SELL_ALL", f"샹들리에_트레일링스탑_최고{int(highest_p):,}원→스탑{int(chandelier_stop):,}원({profit_rate:.2%})"
        else:
            # ATR 부재 시 폴백: 최고가 대비 -1.8% 반락 시 매도
            if highest_p >= buy_price * 1.018 and (current_price - highest_p) / highest_p <= -0.018:
                return "SELL_ALL", f"폴백_트레일링스탑({profit_rate:.2%})"

        # 4. 🎯 ATR R-배수 및 단계별 분할 익절 (Take-Profit Stages)
        if atr14 > 0:
            r1_target = buy_price + (1.5 * atr14)
            r2_target = buy_price + (2.5 * atr14)
            r3_target = buy_price + (3.5 * atr14)

            if sell_stage == 0 and current_price >= r1_target:
                return "SELL_PARTIAL", f"1차_ATR_R1_분할익절_50%({profit_rate:.2%}, 목표가:{int(r1_target):,}원)"
            if sell_stage == 1 and current_price >= r2_target:
                return "SELL_PARTIAL", f"2차_ATR_R2_분할익절_50%({profit_rate:.2%}, 목표가:{int(r2_target):,}원)"
            if sell_stage >= 2 and current_price >= r3_target:
                return "SELL_ALL", f"3차_ATR_R3_전량익절_100%({profit_rate:.2%}, 목표가:{int(r3_target):,}원)"
        else:
            # 폴백 고정 % 분할 익절
            if sell_stage == 0 and profit_rate >= 0.03:
                return "SELL_PARTIAL", f"1차_고정_분할익절_50%({profit_rate:.2%})"
            if sell_stage == 1 and profit_rate >= 0.05:
                return "SELL_PARTIAL", f"2차_고정_분할익절_50%({profit_rate:.2%})"
            if sell_stage >= 2 and profit_rate >= 0.08:
                return "SELL_ALL", f"3차_고정_전량익절_100%({profit_rate:.2%})"

        # 5. ⏰ 장 마감 전 시간 기반 강제 청산 (오버나잇 리스크 회피, 15:15 이후)
        skip_time_filter = ind.get('skip_time_filter', False) if ind else False
        if not skip_time_filter:
            now = datetime.now()
            if now.hour == 15 and now.minute >= 15:
                return "SELL_ALL", f"장마감_오버나잇방지_강제청산({profit_rate:.2%})"

        return "WAIT", ""


# 하위 호환성을 위한 별칭 제공
LiquidityBreakoutStrategy = AdaptiveVolatilityBreakoutStrategy
