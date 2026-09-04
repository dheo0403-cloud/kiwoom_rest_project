import pandas as pd
from datetime import datetime

class LiquidityBreakoutStrategy:
    def __init__(self, db_manager):
        self.db = db_manager

    async def check_buy_signal(self, code, current_price, current_volume, ind):
        """
        돌파 매수 로직 검증 (비동기)
        ind: DataCollector.get_calculated_indicators 에서 넘어온 딕셔너리
        """
        if not ind:
            return False, ""

        # [필터 1] 14:30 이후 신규 매수 차단 (장 마감 리스크 회피)
        now = datetime.now()
        if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
            return False, "시간외_매수차단"

        # [필터 2] 저가주 제외 (1,000원 미만 동전주)
        if current_price < 1000:
            return False, ""

        ma5 = ind['ma5']
        ma20 = ind.get('ma20', 0)
        high10 = ind['high10']
        high3 = ind.get('high3', high10)   # ★ 3일 고가 (없으면 high10 폴백)
        avg_vol = ind['avg_vol']

        # [추세 필터] 20일선(MA20) 역배열 하락장 매수 차단
        if current_price < ma20:
            return False, ""

        # [핵심 조건] 3일 고가의 97% 이상 + 5일선 위 (돌파 직전 종목도 포함)
        if current_price < high3 * 0.97 or current_price < ma5:
            return False, ""

        # [핵심 조건] 당일 거래대금(추정치) 100억 이상 & 거래량 3.0배 이상 급증
        # ★ 장중 누적 거래량을 하루 전체로 환산(projection)하여 avg_vol과 비교
        if (current_price * current_volume) < 10_000_000_000:
            return False, ""

        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        total_seconds = (market_close - market_open).total_seconds()   # 23,400초
        elapsed_seconds = max(600, (now - market_open).total_seconds())  # 최소 10분
        day_progress = min(1.0, elapsed_seconds / total_seconds)         # 0.0 ~ 1.0

        # 현재 누적 거래량 → 종일 환산 거래량 추정
        projected_volume = current_volume / day_progress

        if projected_volume < (avg_vol * 3.0):
            return False, ""

        print(f"  ✅ [DEBUG/{code}] 핵심조건 통과! 현재가={current_price:,} / 분봉 피보나치 판단 진입")

        # [보조 조건] 피보나치 23.6% 눌림목(Pullback) 및 반등 확인
        # ★ 조정이 없는 강한 상승장에서는 거래량 3배 이상이면 추격 매수 허용
        try:
            query = f"SELECT * FROM minute_ohlcv WHERE code='{code}' ORDER BY datetime DESC LIMIT 10"
            df = await self.db.get_dataframe(query)

            if not df.empty and len(df) >= 5:
                df = df.sort_values('datetime').reset_index(drop=True)

                highest = df['high'].max()
                lowest = df['low'].min()

                if highest > lowest:
                    fibo_236 = highest - (highest - lowest) * 0.236

                    if current_price <= fibo_236:
                        prev_candle = df.iloc[-1]
                        # 반등 양봉 확인
                        if current_price > prev_candle['open'] or prev_candle['close'] > prev_candle['open']:
                            return True, f"피보나치_23.6%눌림목_지속반등"
                        else:
                            print(f"  ❌ [DEBUG/{code}] 피보나치 눌림목이나 반등 양봉 미확인")
                    else:
                        # 조정 없이 강한 상승 → 환산 거래량 3배 이상이면 추격 허용
                        if projected_volume >= (avg_vol * 3.0):
                            return True, f"강한돌파_거래량3배_추격매수허용"
                        print(f"  ❌ [DEBUG/{code}] 조정없는 상승인데 환산거래량 미달: {projected_volume:,.0f} < {avg_vol*3:,.0f}")
                        return False, ""
        except Exception as e:
            pass

        # 분봉 데이터가 없거나 조건 미충족 → 진입 보류
        return False, ""



    async def check_sell_signal(self, code, buy_price, current_price, ind,
                                sell_stage=0, highest_price=None):
        """
        다단계 익절 + 트레일링 스탑 매도 시그널 생성

        sell_stage: 0=미매도, 1=1차익절완료, 2=2차익절완료
        highest_price: 매수 이후 최고가 (트레일링 스탑용)
        
        Returns: (action, reason)
          action: "WAIT" / "SELL_ALL" / "SELL_PARTIAL"
        """
        if buy_price <= 0:
            return "WAIT", ""

        profit_rate = (current_price - buy_price) / buy_price

        # [1] 하드 스탑로스 (-4%) — 무조건 최우선 (대규모 손실 방지)
        if profit_rate <= -0.04:
            return "SELL_ALL", f"하드_스탑로스_시장가투매_{profit_rate:.1%}"

        # [2] 트레일링 스탑 (수익 보존)
        #     최소 +3% 이상 올랐을 때만 작동. 최고가 대비 -2% 하락하면 전량 매도
        if highest_price and highest_price > buy_price * 1.03:
            trailing_drop = (current_price - highest_price) / highest_price
            if trailing_drop <= -0.02:
                return "SELL_ALL", f"트레일링_스탑_최고{highest_price:,}→현재{current_price:,}({trailing_drop:.1%})"

        # [3] 장 마감 전 시간 기반 강제 청산 (오버나잇 리스크 회피, 15:15 이후)
        now = datetime.now()
        if now.hour == 15 and now.minute >= 15:
            return "SELL_ALL", f"장마감_오버나잇_방지_강제청산({profit_rate:.1%})"

        # [4] 추세 이탈 (MA5 붕괴) — 어느정도 수익권(+1% 이상)일 때 5일선을 깨면 전량 매도
        if profit_rate >= 0.01 and ind:
            ma5 = ind['ma5']
            if current_price < ma5:
                return "SELL_ALL", "수익권_추세이탈_5일선붕괴"

        return "WAIT", ""
