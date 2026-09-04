"""
Gate Info:
- Importers/callers: kiwoom_rest_project/test_async_trading_loop.py, kiwoom_rest_project/api_server.py
- Affected API: Kiwoom OpenAPI REST (/oauth2/token, /api/dostk/ordr, /api/dostk/stkinfo, /api/dostk/mrkcond, /api/dostk/acnt, /api/dostk/rkinfo, /api/dostk/chart)
- Data schemas: Full Async Event-Driven Trading Loop with PriorityQueue & AsyncPortfolioManager
- User's verbatim instruction: "파일 전체를 읽지 말고 최근 로그 50줄만 읽으면서 계속 진행해줘"
"""
import asyncio
import os
import sys
import argparse
from datetime import datetime
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from async_kiwoom_client import AsyncKiwoomClient, RequestPriority
from async_portfolio import AsyncPortfolioManager
from database import AsyncDatabase

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

class AsyncTradingBot:
    """
    완전 비동기(asyncio/aiohttp) 키움증권 퀀트 트레이딩 봇 데몬
    - 4단계 선점형 우선순위 큐(CRITICAL/HIGH/MEDIUM/LOW) 연동
    - 동시성 안전 포트폴리오 관리자(AsyncPortfolioManager)
    - 피보나치 눌림목 매수 전략 (0.382 / 0.5 / 0.618)
    - 스마트 호가 기반 3단계 분할 익절 및 긴급 스탑로스 선점 매도
    - KODEX 200 지수 급락 필터 & MDD -5% 계좌 서킷 브레이커
    - 수동 주문 비동기 처리 및 DB 영속화
    """
    def __init__(self, is_demo: bool = True, initial_capital: float = 10_000_000,
                 client: Optional[AsyncKiwoomClient] = None,
                 portfolio: Optional[AsyncPortfolioManager] = None,
                 db: Optional[AsyncDatabase] = None):
        self.is_demo = is_demo
        self.client = client or AsyncKiwoomClient(is_demo=is_demo)
        self.portfolio = portfolio or AsyncPortfolioManager(initial_capital=initial_capital, max_stocks=5)
        self.db = db or AsyncDatabase()

        self.market_filter_passed = True
        self.kodex200_change_rate = 0.0
        self.mdd_shutdown = False
        self.highest_total_asset = 0.0  # 최초 계좌 동기화 시 실제 자산으로 캘리브레이션
        self.is_running = False

        # 감시 종목 리스트 (코드, 이름, 피보나치 레벨 정보)
        self.watchlist: Dict[str, Dict[str, Any]] = {}

        # 분할 익절 단계 목표치
        self.take_profit_stages = [
            (0.03, 0.33, 1),  # +3% 도달 시 33% 1차 익절 -> Stage 1
            (0.05, 0.50, 2),  # +5% 도달 시 남은 수량의 50% 2차 익절 -> Stage 2
            (0.08, 1.00, 3),  # +8% 도달 시 잔여 수량 전량(100%) 3차 익절 -> Stage 3
        ]
        self.stop_loss_rate = -0.04  # -4.0% 하드 스탑로스 (CRITICAL 긴급 매도)
        self.trailing_stop_drop = 0.025  # 최고점 대비 2.5% 반락 시 트레일링 스탑 매도

    @property
    def running(self) -> bool:
        """봇 구동 상태 getter"""
        return self.is_running

    @running.setter
    def running(self, value: bool):
        """봇 구동 상태 setter"""
        self.is_running = bool(value)

    async def run_daily_trading_loop(self):
        """api_server.py 호환 비동기 트레이딩 루프 실행 별칭"""
        await self.trading_loop()

    async def sync_account_and_portfolio(self):
        """api_server.py 호환 계좌 잔고 동기화 별칭"""
        await self._sync_account_balance()

    async def prepare_morning_universe(self):
        """api_server.py 호환 감시 유니버스 갱신 별칭"""
        await self.update_watchlist()

    async def initialize(self):
        """클라이언트, DB 풀, 계좌 상태 초기화"""
        print(f"🚀 [AsyncTradingBot] 엔진 초기화 시작 (모드: {'모의투자' if self.is_demo else '실전투자'})...")
        await self.db.init_pool()
        await self.client.start()
        await self._sync_account_balance()
        self.is_running = True
        await self.db.log_message("SYSTEM", f"비동기 트레이딩 데몬 가동 완료 (모드: {self.client.mode})")

    async def _sync_account_balance(self):
        """계좌 잔고 및 예수금 비동기 동기화 (MEDIUM 우선순위)"""
        balance_data = await self.client.get_account_balance(priority=RequestPriority.MEDIUM)
        if not balance_data:
            return

        # 1. 예수금 파싱
        raw_output = balance_data.get('output', balance_data)
        if isinstance(raw_output, list) and len(raw_output) > 0:
            raw_output = raw_output[0]

        deposit = self.portfolio.current_capital
        possible_keys = ['entr_d2', 'd2_deposit', 'ord_alowa', 'entr', 'prvs_rcdl_excc_amt', 'ord_psbl_cash', 'dnca_tot_amt', 'deposit', '주문가능금액']
        for key in possible_keys:
            val_str = str(raw_output.get(key, '')).strip().replace(',', '')
            if val_str.isdigit() and int(val_str) > 0:
                deposit = float(val_str)
                break

        await self.portfolio.sync_capital(deposit)

        # 2. 보유 종목 동기화
        await self.portfolio.sync_positions(balance_data)

        # 3. DB 저장 및 스냅샷 확인
        snap = await self.portfolio.get_snapshot()
        if self.highest_total_asset == 0.0 or snap['total_asset'] > self.highest_total_asset:
            self.highest_total_asset = snap['total_asset']

        # MDD 셧다운 검사 (-5% 초과 하락 시 신규 매수 차단)
        if self.highest_total_asset > 0:
            mdd = ((snap['total_asset'] - self.highest_total_asset) / self.highest_total_asset) * 100.0
            if mdd <= -5.0 and not self.mdd_shutdown:
                self.mdd_shutdown = True
                await self.db.log_message("WARNING", f"🚨 [서킷 브레이커] 계좌 MDD {mdd:.2f}% 도달. 당일 신규 매수를 중단합니다.")
                print(f"🚨 [서킷 브레이커] 당일 최고 자산 대비 -5% 초과 하락! (MDD: {mdd:.2f}%) 신규 매수 중단.")

        await self.db.save_portfolio(self.portfolio.positions)
        await self.db.update_balance(snap['total_asset'], snap['current_capital'], snap['unrealized_pnl'], snap['total_yield_rate'])
        print(f"🔄 [계좌 싱크] 총자산 {int(snap['total_asset']):,}원 / 예수금 {int(snap['current_capital']):,}원 / 보유 {snap['stock_count']}종목")

    async def update_watchlist(self, top_n: int = 10):
        """거래대금 상위 종목 수집 및 피보나치 레벨 계산 (LOW 우선순위)"""
        print(f"🔍 [Watchlist] 거래대금 상위 {top_n}종목 스캔 및 피보나치 분석 시작...")
        top_data = await self.client.get_top_trading_value(priority=RequestPriority.LOW)
        if not top_data:
            print("⚠️ 거래대금 상위 조회 응답 없음")
            return

        if isinstance(top_data, list):
            items = top_data
        elif isinstance(top_data, dict):
            items = top_data.get('output', top_data.get('list', []))
        else:
            items = []

        if isinstance(items, dict):
            items = [items]

        today_str = datetime.now().strftime('%Y%m%d')
        new_watchlist = {}

        for item in items[:top_n]:
            if not isinstance(item, dict):
                continue
            code = (item.get('stk_cd') or item.get('code') or '').strip()
            if not code:
                continue
            if code.startswith('A'):
                code = code[1:]
            name = item.get('stk_nm') or item.get('name') or code

            # 일봉 차트 조회하여 최근 20일 고가/저가 및 피보나치 레벨 산출
            daily_chart = await self.client.get_daily_chart(code, base_dt=today_str, priority=RequestPriority.LOW)
            if not daily_chart:
                continue
            if isinstance(daily_chart, tuple):
                daily_chart = daily_chart[0]
            if not isinstance(daily_chart, dict):
                continue

            chart_items = daily_chart.get('output2', daily_chart.get('output', []))
            if not chart_items or not isinstance(chart_items, list):
                continue

            # 최근 20거래일 데이터 추출
            recent_candles = chart_items[:20]
            if len(recent_candles) < 5:
                continue

            try:
                highs = [float(c.get('hgpr', 0) or c.get('stck_hgpr', 0) or c.get('high_price', 0)) for c in recent_candles]
                lows = [float(c.get('lwpr', 0) or c.get('stck_lwpr', 0) or c.get('low_price', 0)) for c in recent_candles]
                period_high = max(highs)
                period_low = min(lows)
                diff = period_high - period_low

                if diff <= 0:
                    continue

                fib_382 = period_high - (diff * 0.382)
                fib_500 = period_high - (diff * 0.500)
                fib_618 = period_high - (diff * 0.618)

                cur_price = float(item.get('prpr', 0) or chart_items[0].get('clpr', 0))

                new_watchlist[code] = {
                    'code': code,
                    'name': name,
                    'current_price': cur_price,
                    'period_high': period_high,
                    'period_low': period_low,
                    'fib_382': fib_382,
                    'fib_500': fib_500,
                    'fib_618': fib_618,
                    'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            except Exception as e:
                print(f"⚠️ {name}({code}) 피보나치 분석 실패: {e}")
                continue

        self.watchlist = new_watchlist
        await self.db.save_watchlist(list(self.watchlist.values()))
        print(f"✅ [Watchlist] {len(self.watchlist)}개 종목 피보나치 분석 완료 및 DB 저장")

    async def check_market_filter(self):
        """KODEX 200 (069500) 지수 급락 감지 (-1.5% 하락 시 신규 매수 제한)"""
        kodex_data = await self.client.get_price("069500", priority=RequestPriority.LOW)
        if not kodex_data:
            return

        out = kodex_data.get('output', kodex_data)
        if isinstance(out, list) and len(out) > 0:
            out = out[0]

        fluct_rate_str = str(out.get('prdy_ctrt', 0) or out.get('fluctuation_rate', 0)).replace('%', '').strip()
        try:
            fluct_rate = float(fluct_rate_str)
            self.kodex200_change_rate = fluct_rate
            if fluct_rate <= -1.5:
                if self.market_filter_passed:
                    self.market_filter_passed = False
                    await self.db.log_message("WARNING", f"🚨 [시장 급락 감지] KODEX 200 {fluct_rate:.2f}% 하락. 신규 매수를 일시 제한합니다.")
                    print(f"🚨 [시장 필터] KODEX 200 {fluct_rate:.2f}% 급락 -> 신규 매수 제한")
            else:
                if not self.market_filter_passed:
                    self.market_filter_passed = True
                    await self.db.log_message("INFO", f"✅ [시장 안정 회복] KODEX 200 {fluct_rate:.2f}%. 신규 매수를 재개합니다.")
                    print(f"✅ [시장 필터] KODEX 200 안정 ({fluct_rate:.2f}%) -> 신규 매수 허용")
        except ValueError:
            pass

    async def process_manual_orders(self):
        """대시보드에서 요청된 수동 주문 큐 비동기 처리 (HIGH 우선순위)"""
        orders = await self.db.get_pending_manual_orders()
        for o in orders:
            order_id = o['id']
            code = o['code']
            side = o['side'].upper()
            qty = int(o.get('qty', 1))
            price = int(o.get('price', 0))
            name = o.get('name', code)

            # 주문 실행 전 PROCESSING 처리
            if hasattr(self.db, 'complete_manual_order'):
                await self.db.complete_manual_order(order_id, "PROCESSING")
            elif hasattr(self.db, 'update_manual_order_status'):
                await self.db.update_manual_order_status(order_id, "PROCESSING")
            print(f"⚡ [MANUAL_ORDER] 대시보드 수동 주문 실행: {side} {name}({code}) {qty}주 @ {price}원")

            order_type = "03" if price == 0 else "00"
            res = await self.client.send_order(code, qty, price, order_type=order_type, side=side, priority=RequestPriority.HIGH)

            rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
            if res and str(rt_cd) == '0':
                if hasattr(self.db, 'complete_manual_order'):
                    await self.db.complete_manual_order(order_id, "COMPLETED")
                elif hasattr(self.db, 'update_manual_order_status'):
                    await self.db.update_manual_order_status(order_id, "COMPLETED")
                await self.db.log_order(code, name, side, qty, price)
                await self.db.log_message("INFO", f"수동 주문 체결 완료: {side} {name}({code}) {qty}주")
                await self._sync_account_balance()
            else:
                msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 실패'
                if hasattr(self.db, 'complete_manual_order'):
                    await self.db.complete_manual_order(order_id, "FAILED")
                elif hasattr(self.db, 'update_manual_order_status'):
                    await self.db.update_manual_order_status(order_id, "FAILED")
                await self.db.log_message("ERROR", f"수동 주문 실패: {side} {name}({code}) - {msg}")

    async def monitor_positions_and_exit(self):
        """
        보유 포지션 실시간 시세 감시 및 출구 전략 (분할 익절 / 긴급 손절)
        - 긴급 손절(스탑로스 -4% / 트레일링 스탑): RequestPriority.CRITICAL 선점 발주
        - 정규 분할 익절(+3%, +5%, +8%): RequestPriority.HIGH 발주
        """
        snap = await self.portfolio.get_snapshot()
        positions = snap['positions']
        if not positions:
            return

        for pos in positions:
            code = pos['code']
            name = pos['name']
            qty = pos['qty']
            buy_price = pos['buy_price']
            highest_price = pos['highest_price']
            stage = pos['sell_stage']

            # 실시간 호가/시세 조회 (LOW 우선순위)
            price_data = await self.client.get_price(code, priority=RequestPriority.LOW)
            if not price_data:
                continue

            out = price_data.get('output', price_data)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]

            cur_price_str = str(out.get('prpr', 0) or out.get('current_price', 0)).replace(',', '').strip()
            if not cur_price_str.isdigit() or int(cur_price_str) <= 0:
                continue

            cur_price = float(cur_price_str)
            await self.portfolio.update_current_price(code, cur_price)

            yield_rate = (cur_price - buy_price) / buy_price if buy_price > 0 else 0.0
            peak_drop = (cur_price - highest_price) / highest_price if highest_price > 0 else 0.0

            # 1. 🚨 하드 스탑로스 (-4.0% 이하) -> CRITICAL 우선순위 긴급 전량 매도
            if yield_rate <= self.stop_loss_rate:
                print(f"🚨 [STOP_LOSS] {name}({code}) 손절 조건 도달 (수익률: {yield_rate*100:.2f}%) -> 긴급 시장가/매수2호가 매도 발주!")
                await self._execute_emergency_sell(code, name, qty, cur_price, reason=f"하드 스탑로스 ({yield_rate*100:.2f}%)")
                continue

            # 2. 🚨 트레일링 스탑 (수익권 진입 후 최고점 대비 -2.5% 반락) -> CRITICAL 긴급 매도
            if (stage >= 1 or highest_price >= buy_price * 1.03) and peak_drop <= -self.trailing_stop_drop:
                print(f"🚨 [TRAILING_STOP] {name}({code}) 최고가({highest_price:,.0f}원) 대비 {peak_drop*100:.2f}% 반락 -> 잔여 전량 매도!")
                await self._execute_emergency_sell(code, name, qty, cur_price, reason=f"트레일링 스탑 ({peak_drop*100:.2f}%)")
                continue

            # 3. 🎯 3단계 분할 익절 (+3%, +5%, +8%) -> HIGH 우선순위 매수 1호가 지정가 매도
            for target_profit, sell_ratio, next_stage in self.take_profit_stages:
                if yield_rate >= target_profit and stage < next_stage:
                    sell_qty = max(1, int(qty * sell_ratio))
                    if next_stage == 3:
                        sell_qty = qty  # 최종 3차는 전량 청산

                    print(f"🎯 [TAKE_PROFIT Stage {next_stage}] {name}({code}) 목표 수익률({target_profit*100:.1f}%) 달성! {sell_qty}주 분할 매도 발주")
                    await self._execute_profit_sell(code, name, sell_qty, cur_price, next_stage, reason=f"{next_stage}차 분할익절 ({yield_rate*100:.2f}%)")
                    break

    async def _execute_emergency_sell(self, code: str, name: str, qty: int, cur_price: float, reason: str):
        """CRITICAL 우선순위로 큐를 추월하는 긴급 스탑로스 주문"""
        # 호가창 조회 후 매수 2호가 또는 시장가 발주
        orderbook = await self.client.get_orderbook(code, priority=RequestPriority.CRITICAL)
        sell_price = int(cur_price)
        order_type = "00"

        if orderbook:
            out = orderbook.get('output', orderbook)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]
            # 매수 2호가 타겟팅으로 빠른 체결 유도
            bid2 = out.get('buy_fpr_bid2') or out.get('bid_price2')
            if bid2 and str(bid2).isdigit() and int(bid2) > 0:
                sell_price = int(bid2)
            else:
                order_type = "03"  # 시장가 전환

        res = await self.client.send_order(code, qty, sell_price, order_type=order_type, side="SELL", priority=RequestPriority.CRITICAL)
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        if res and str(rt_cd) == '0':
            await self.portfolio.remove_position(code, sell_price)
            await self.db.log_order(code, name, "SELL", qty, sell_price)
            await self.db.log_message("WARNING", f"🚨 [긴급 매도 성공] {name}({code}) {qty}주 @ {sell_price:,}원 ({reason})")
            await self._sync_account_balance()
        else:
            msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"긴급 매도 실패: {name}({code}) - {msg}")

    async def _execute_profit_sell(self, code: str, name: str, qty: int, cur_price: float, next_stage: int, reason: str):
        """HIGH 우선순위로 스마트 호가(매수 1호가) 분할 익절 주문"""
        orderbook = await self.client.get_orderbook(code, priority=RequestPriority.HIGH)
        sell_price = int(cur_price)

        if orderbook:
            out = orderbook.get('output', orderbook)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]
            # 매수 1호가(buy_fpr_bid) 타겟팅
            bid1 = out.get('buy_fpr_bid') or out.get('bid_price1')
            if bid1 and str(bid1).isdigit() and int(bid1) > 0:
                sell_price = int(bid1)

        res = await self.client.send_order(code, qty, sell_price, order_type="00", side="SELL", priority=RequestPriority.HIGH)
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        if res and str(rt_cd) == '0':
            await self.portfolio.update_partial_sell(code, qty, sell_price, next_stage)
            await self.db.log_order(code, name, "SELL", qty, sell_price)
            await self.db.log_message("INFO", f"🎯 [분할 익절 성공] {name}({code}) {qty}주 @ {sell_price:,}원 ({reason})")
            await self._sync_account_balance()
        else:
            msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"분할 익절 실패: {name}({code}) - {msg}")

    async def monitor_watchlist_and_enter(self):
        """
        감시 종목 피보나치 눌림목 매수 기회 포착
        - 0.382 / 0.5 / 0.618 눌림목 구간 지지 및 반등 감지 시 HIGH 우선순위 매수 발주
        """
        if not self.market_filter_passed or self.mdd_shutdown:
            return

        for code, info in list(self.watchlist.items()):
            # 포지션 한도 및 중복 매수 체크
            if not await self.portfolio.can_buy(code):
                continue

            name = info['name']
            fib_382 = info['fib_382']
            fib_618 = info['fib_618']

            # 실시간 호가 및 시세 조회 (LOW 우선순위)
            price_data = await self.client.get_price(code, priority=RequestPriority.LOW)
            if not price_data:
                continue

            out = price_data.get('output', price_data)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]

            cur_price_str = str(out.get('prpr', 0) or out.get('current_price', 0)).replace(',', '').strip()
            if not cur_price_str.isdigit() or int(cur_price_str) <= 0:
                continue

            cur_price = float(cur_price_str)
            info['current_price'] = cur_price

            # 피보나치 눌림목 조건: 0.382 이하 ~ 0.618 이상 구간에 위치
            if fib_618 <= cur_price <= fib_382:
                # 1분봉 반등 시그널 확인
                today_str = datetime.now().strftime('%Y%m%d')
                min_chart, _ = await self.client.get_minute_chart(code, base_dt=today_str, priority=RequestPriority.LOW)
                if not min_chart:
                    continue

                candles = min_chart.get('output2', min_chart.get('output', []))
                if not candles or len(candles) < 3:
                    continue

                # 직전 분봉 양봉 및 거래량 증가 확인 (간이 반등 필터)
                last_candle = candles[0]
                prev_candle = candles[1]
                last_open = float(last_candle.get('oprn', 0) or last_candle.get('open_price', 0))
                last_close = float(last_candle.get('clpr', 0) or last_candle.get('close_price', 0))
                last_vol = float(last_candle.get('cntg_vol', 0) or last_candle.get('volume', 0))
                prev_vol = float(prev_candle.get('cntg_vol', 0) or prev_candle.get('volume', 1))

                is_rebound = (last_close >= last_open) and (last_vol >= prev_vol * 0.8)
                if is_rebound:
                    # 안전 자산 배분 수량 계산
                    order_qty = await self.portfolio.get_order_qty(cur_price)
                    if order_qty <= 0:
                        continue

                    print(f"🔥 [BUY_SIGNAL] {name}({code}) 피보나치 눌림목 반등 확인! (현재가: {cur_price:,.0f}원, 목표수량: {order_qty}주)")
                    await self._execute_smart_buy(code, name, order_qty, cur_price)

    async def _execute_smart_buy(self, code: str, name: str, qty: int, cur_price: float):
        """HIGH 우선순위로 매도 1호가 지정가 매수 발주"""
        orderbook = await self.client.get_orderbook(code, priority=RequestPriority.HIGH)
        buy_price = int(cur_price)

        if orderbook:
            out = orderbook.get('output', orderbook)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]
            # 매도 1호가(sel_fpr_bid) 타겟팅
            ask1 = out.get('sel_fpr_bid') or out.get('ask_price1')
            if ask1 and str(ask1).isdigit() and int(ask1) > 0:
                buy_price = int(ask1)

        res = await self.client.send_order(code, qty, buy_price, order_type="00", side="BUY", priority=RequestPriority.HIGH)
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        if res and str(rt_cd) == '0':
            await self.portfolio.add_position(code, name, qty, buy_price)
            await self.db.log_order(code, name, "BUY", qty, buy_price)
            await self.db.log_message("INFO", f"🔥 [매수 체결 완료] {name}({code}) {qty}주 @ {buy_price:,}원")
            await self._sync_account_balance()
        else:
            msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"매수 주문 실패: {name}({code}) - {msg}")

    async def trading_loop(self):
        """메인 트레이딩 비동기 주기 루프"""
        loop_count = 0
        while self.is_running:
            try:
                loop_count += 1
                now = datetime.now()
                now_time = now.time()

                # 1. 수동 주문 큐 처리 (매 루프마다)
                await self.process_manual_orders()

                # 2. 시장 필터 및 계좌 싱크 (10초 주기)
                if loop_count % 5 == 0:
                    await self.check_market_filter()
                    await self._sync_account_balance()

                # 3. 감시 종목 갱신 (60초 주기)
                if loop_count % 30 == 1:
                    await self.update_watchlist()

                # 4. 포지션 감시 및 출구 전략 (매 2초마다 최우선 감시)
                await self.monitor_positions_and_exit()

                # 5. 신규 매수 기회 탐색 (매 2초마다)
                await self.monitor_watchlist_and_enter()

                # 6. 장 마감(15:30) 체크
                if now_time.hour >= 15 and now_time.minute >= 30:
                    print("🏁 [장 마감] 당일 정규 거래 시간이 종료되었습니다.")
                    await self.db.log_message("SYSTEM", "당일 정규장 마감. 트레이딩 루프를 종료합니다.")
                    break

                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [TradingLoop Error] {e}")
                await self.db.log_message("ERROR", f"트레이딩 루프 오류: {e}")
                await asyncio.sleep(2.0)

    async def shutdown(self):
        """시스템 종료 및 자원 정리"""
        self.is_running = False
        print("🛑 [AsyncTradingBot] 데몬 종료 및 자원 반환 중...")
        await self.client.stop()
        await self.db.close_pool()
        print("✅ [AsyncTradingBot] 정상 종료 완료")

async def main():
    parser = argparse.ArgumentParser(description="키움 OpenAPI 비동기 퀀트 트레이딩 데몬")
    parser.add_argument('--real', action='store_true', help='실전투자 모드 (미지정시 모의투자)')
    parser.add_argument('--capital', type=float, default=10_000_000, help='초기 운용 자본금')
    args = parser.parse_args()

    bot = AsyncTradingBot(is_demo=not args.real, initial_capital=args.capital)
    try:
        await bot.initialize()
        await bot.trading_loop()
    except KeyboardInterrupt:
        print("\n사용자에 의해 데몬이 중단되었습니다.")
    finally:
        await bot.shutdown()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
