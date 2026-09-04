import os
import asyncio
import sys
import time
import argparse
from datetime import datetime

# 한국 표준시(KST) 강제 적용
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

from kiwoom_rest import KiwoomRest
from database import DatabaseManager
from data_collector import DataCollector
from strategy import LiquidityBreakoutStrategy
from portfolio import PortfolioManager

class TradingSystemRest:
    def __init__(self, is_demo=True):
        self.is_demo = is_demo
        self.kiwoom = KiwoomRest(is_demo=self.is_demo)
        self.db = DatabaseManager()
        self.data_collector = DataCollector(self.kiwoom, self.db)
        self.strategy = LiquidityBreakoutStrategy(self.db)
        self.portfolio = PortfolioManager(self.db, max_stocks=5, weight_per_stock=0.2, max_daily_entries=3)
        
        self.mode_name = "모의투자" if self.is_demo else "실전투자"
        self.current_capital = 10000000 if self.is_demo else 300000
        
        self.target_list = []
        self.data_synced_today = False
        self._last_heartbeat = 0
        self._loop_count = 0
        self._monitoring_started = False
        self.market_buy_enabled = True  # KOSPI 급락 시 False
        self.highest_total_asset = 0
        self.mdd_shutdown = False

    async def refresh_daily_session(self):
        """매일 아침 토큰 갱신 및 잔고/포지션 하드 싱크"""
        token = await self.kiwoom.get_access_token()
        if token:
            print("\n💰 [실제 계좌 잔고 및 보유 종목 하드 싱크]")
            # 1. 잔고 동기화
            balance_data = await self.kiwoom.get_account_balance()
            total_capital = 0
            if balance_data:
                # 최상위 키 또는 output/acnt_info 배열 첫 번째 요소 처리
                raw_output = balance_data.get('output', balance_data)
                if isinstance(raw_output, list) and len(raw_output) > 0:
                    raw_output = raw_output[0]
                
                # 주문 가능 현금보다 D+2 예수금을 최우선으로 찾음 (매도 대금 포함하여 재매수 가능하게)
                possible_keys = ['entr_d2', 'd2_deposit', 'ord_alowa', 'entr', 'prvs_rcdl_excc_amt', 'ord_psbl_cash', 'dnca_tot_amt', 'deposit', '주문가능금액']
                for key in possible_keys:
                    if key in raw_output and str(raw_output[key]).strip().replace(',', '').isdigit():
                        total_capital = int(str(raw_output[key]).strip().replace(',', ''))
                        if total_capital > 0:
                            break
            
            if total_capital <= 0:
                print("⚠️ 예수금 조회 실패. 봇 내부 자본금으로 연산합니다.")
                total_capital = self.current_capital
                
            print(f"✅ 총 자본금(주문가능현금) 확인 완료: {total_capital:,}원")
            self.current_capital = total_capital
            self.portfolio.sync_capital(self.current_capital)
            
            # MDD 초기화 (하루 시작 시 초기 자산액을 기준으로 세팅)
            self.highest_total_asset = total_capital
            self.mdd_shutdown = False
            
            # 2. 보유 종목 동기화
            positions_data = await self.kiwoom.get_account_positions()
            if positions_data:
                out = []
                # JSON 구조가 {"output": {"stk_cntr_remn": [...]}} 인 경우 처리
                if 'output' in positions_data:
                    output_obj = positions_data['output']
                    if isinstance(output_obj, list):
                        out = output_obj
                    elif isinstance(output_obj, dict):
                        out = output_obj.get('stk_cntr_remn') or output_obj.get('acnt_info') or []
                
                # 최상위에 배열이 있는 경우 처리
                if not out:
                    out = positions_data.get('stk_cntr_remn') or positions_data.get('acnt_info') or []
                
                # 단일 객체인 경우 리스트로 감싸기
                if out and not isinstance(out, list): 
                    out = [out]
                    
                if out:
                    self.portfolio.sync_positions(out)
                    await self.db.save_portfolio(self.portfolio.positions)
                else:
                    print("⚠️ 보유 종목 리스트가 비어있습니다 (현재 보유 주식 없음).")
                    self.portfolio.sync_positions([])
                    await self.db.save_portfolio(self.portfolio.positions)
            else:
                print("⚠️ 보유 종목 조회 실패.")
                
            # DB 기초 자산 기록
            await self.db.update_balance(total_capital, total_capital, 0, 0.0)
            return True
        else:
            print(f"[{self.mode_name}] ❌ 인증 실패")
            return False

    async def prepare_targets(self):
        print("\n🔍 1차 유동성 필터 대상 종목 스캔 중...")
        candidates = await self.data_collector.fetch_top_liquidity_stocks()
        if not candidates:
            print("⚠️ 대상 종목을 찾지 못했습니다. API 제한이거나 응답 에러입니다.")
            return False
            
        print("💾 대상 종목 일봉 데이터 비동기 수집/적재 중...")
        await self.data_collector.update_daily_data(candidates)
        
        print("🔍 2차 돌파 필터 검증 중...")
        self.target_list = await self.data_collector.filter_2nd_breakout(candidates)
        print(f"✅ 최종 감시 대상 종목 {len(self.target_list)}개 확정\n")
        
        # 감시 종목을 DB에 저장 (대시보드 표시용)
        await self.db.save_watchlist(self.target_list, self.data_collector.stock_names)

        # KOSPI 시장 상황 체크 (KODEX 200 기준)
        await self._check_market_condition()
        return True

    async def _check_market_condition(self):
        """시장 급락 여부 판단 (KODEX 200 ETF 등락률 기준)"""
        try:
            market_data = await self.kiwoom.get_price("069500")
            if market_data:
                output = market_data.get('output', market_data)
                if isinstance(output, list): output = output[0] if output else {}
                flu_rt_raw = output.get('flu_rt') or output.get('prdy_ctrt') or '0'
                flu_rt = float(str(flu_rt_raw).replace('+', '').replace('%', ''))
                if flu_rt <= -1.0:
                    self.market_buy_enabled = False
                    print(f"⚠️ [MARKET] KOSPI 급락 감지 ({flu_rt:+.2f}%). 당일 신규 매수를 중단합니다.")
                else:
                    self.market_buy_enabled = True
                    print(f"✅ [MARKET] 시장 상황 정상 (KODEX200 {flu_rt:+.2f}%). 매수 활성화.")
        except Exception as e:
            print(f"⚠️ KOSPI 시장 체크 실패: {e}. 매수를 허용합니다.")
            self.market_buy_enabled = True

    async def _check_position_async(self, code, pos):
        try:
            data = await self.kiwoom.get_price(code)
            if not data: return
            
            output = data.get('output', [{}])[0] if isinstance(data.get('output'), list) else data.get('output', data)
            current_price = abs(int(output.get('cur_prc', 0) or output.get('price', 0) or output.get('stck_prpr', 0)))
            if current_price <= 0: return

            # 트레일링 스탑용 최고가 갱신
            self.portfolio.update_highest_price(code, current_price)
            
            ind = await self.data_collector.get_calculated_indicators(code)
            sell_stage = pos.get('sell_stage', 0)
            highest_price = pos.get('highest_price', pos['buy_price'])

            signal, reason = await self.strategy.check_sell_signal(
                code, pos['buy_price'], current_price, ind,
                sell_stage=sell_stage, highest_price=highest_price
            )
            
            if signal in ["SELL_ALL", "SELL_PARTIAL"]:
                # 스마트 매도 호가 조회 (매수 1호가 타겟팅)
                order_price = current_price
                try:
                    ob_data = await self.kiwoom.get_orderbook(code)
                    if ob_data and 'output' in ob_data:
                        out = ob_data['output'][0] if isinstance(ob_data['output'], list) else ob_data['output']
                        buy_1bid = abs(int(out.get('buy_fpr_bid', 0)))
                        buy_2bid = abs(int(out.get('buy_fpr_bid2', 0)))
                        
                        if "시장가투매" in reason and buy_2bid > 0:
                            # 하드 스탑 시 확실한 체결을 위해 매수 2호가 타겟팅 (시장가 효과)
                            order_price = buy_2bid
                            print(f"🚨 [하드 스탑] 현재가: {current_price:,} -> 매수2호가 타겟: {order_price:,} (폭락장 체결 보장)")
                        elif buy_1bid > 0:
                            order_price = buy_1bid
                            print(f"🎯 [스마트 매도] 현재가: {current_price:,} -> 매수1호가 타겟: {order_price:,} (체결 보장)")
                except Exception as e:
                    print(f"⚠️ 호가 조회 실패, 현재가 매도: {e}")

                if signal == "SELL_ALL":
                    await self._execute_sell(code, pos['name'], pos['qty'], order_price, reason)
                    self.portfolio.remove_position(code)
                    await self.db.save_portfolio(self.portfolio.positions)
                elif signal == "SELL_PARTIAL":
                    # 30% 분할 매도 (최소 1주)
                    partial_qty = max(1, int(pos['qty'] * 0.3))
                    await self._execute_sell(code, pos['name'], partial_qty, order_price, reason)
                    self.portfolio.update_position_qty(code, partial_qty)
                    self.portfolio.advance_sell_stage(code)
                    await self.db.save_portfolio(self.portfolio.positions)
        except Exception as e:
            print(f"포지션 감시 에러 ({code}): {e}")

    async def _check_target_async(self, code):
        if code in self.portfolio.positions: return

        # KOSPI 급락 또는 MDD 셧다운 시 매수 중단
        if not self.market_buy_enabled or self.mdd_shutdown: return
            
        try:
            data = await self.kiwoom.get_price(code)
            if not data: return
            
            # 실전 API(ka10001)는 루트에 직접 데이터 반환, 모의는 output 키 사용
            output = data.get('output', [{}])[0] if isinstance(data.get('output'), list) else data.get('output', data)
            
            def parse_num(v):
                if not v: return 0
                c = str(v).replace('+', '').replace('-', '').replace(',', '').strip()
                try: return int(float(c))
                except Exception: return 0

            current_price = parse_num(output.get('cur_prc') or output.get('price') or output.get('stck_prpr') or output.get('last_prc'))
            current_volume = parse_num(output.get('trde_qty') or output.get('acml_vol') or output.get('volume') or output.get('acml_trde_qty'))
            name = output.get('stk_nm') or output.get('name') or output.get('hts_kor_isnm') or code
            
            if current_price <= 0: return

            # 대시보드용 실시간 가격 업데이트
            await self.db.update_watchlist_price(code, name, current_price, current_volume)
            
            ind = await self.data_collector.get_calculated_indicators(code)
            signal, reason = await self.strategy.check_buy_signal(code, current_price, current_volume, ind)
            
            if signal:
                if not self.portfolio.can_enter():
                    print(f"🟡 [SIGNAL] {name}({code}) 매수 시그널 감지! 하지만 진입 불가 (max_stocks 도달 또는 당일 진입 횟수 초과)")
                    return
                
                # 스마트 매수 호가 조회 (매도 1호가 타겟팅) 및 얇은 호가창 방어
                order_price = current_price
                try:
                    ob_data = await self.kiwoom.get_orderbook(code)
                    if ob_data:
                        # 실전 API는 루트에 직접 반환, 모의는 'output' 키 사용
                        if 'output' in ob_data:
                            out = ob_data['output'][0] if isinstance(ob_data['output'], list) else ob_data['output']
                        else:
                            out = ob_data  # 실전 API 루트 응답 처리
                        sel_1bid = abs(int(out.get('sel_fpr_bid', 0)))
                        if sel_1bid > 0:
                            order_price = sel_1bid
                            print(f"🎯 [스마트 매수] 현재가: {current_price:,} -> 매도1호가 타겟: {order_price:,} (체결 보장)")
                        else:
                            print(f"⚠️ [매수 취소] 얇은 호가창 감지: 매도 호가 잔량 부족 ({name})")
                            return
                    else:
                        print(f"⚠️ [매수 취소] 호가 정보 없음 ({name})")
                        return
                except Exception as e:
                    print(f"⚠️ 호가 조회 실패, 얇은 호가창 방어 로직 작동 (매수 취소): {e}")
                    return

                qty = self.portfolio.get_order_qty(order_price)
                if qty <= 0:
                    print(f"🟡 [SIGNAL] {name}({code}) 매수 시그널 감지! 하지만 주문 수량 0 (자본금 부족, 타겟가: {order_price:,}원, 남은자본: {self.current_capital:,}원)")
                    return
                print(f"🟢 [SIGNAL] {name}({code}) 매수 시그널 확인! (사유: {reason}, 타겟가: {order_price:,}원, 수량: {qty}주)")
                buy_ok = await self._execute_buy(code, name, qty, order_price, reason)
                if buy_ok:
                    self.portfolio.add_position(code, name, qty, order_price)
                    # 동일 종목 중복 매수 방지: 감시 대상에서 제거
                    if code in self.target_list:
                        self.target_list.remove(code)
                    await self.db.save_portfolio(self.portfolio.positions)
        except Exception as e:
            print(f"⚠️ 감시 대상 체크 에러 ({code}): {e}")

    async def run(self):
        print(f"🚀 {self.mode_name} 24시간 자동매매 데몬 루프를 시작합니다.")
        # DB 연결 초기화 (프로그램 구동 시 1회만)
        await self.db.init_pool()
        
        last_prep_date = None
        
        try:
            while True:
                now = datetime.now()
                today_str = now.strftime('%Y-%m-%d')
                
                # 장 시작 준비 시간(08:55) ~ 장 마감(15:30)
                start_time = now.replace(hour=8, minute=55, second=0, microsecond=0)
                end_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
                
                if start_time <= now <= end_time:
                    if last_prep_date != today_str:
                        print(f"\n🌅 [{today_str}] 새로운 거래일 시스템 세팅을 시작합니다...")
                        success = await self.refresh_daily_session()
                        if success:
                            prep_ok = await self.prepare_targets()
                            if prep_ok:
                                last_prep_date = today_str
                                self.data_synced_today = False
                                self._monitoring_started = False
                            else:
                                print("⚠️ 상위 종목 세팅 실패. 1분 후 API를 재시도합니다.")
                                await asyncio.sleep(60)
                                continue
                        else:
                            print("⚠️ 아침 세팅 실패 (API 장애 등). 1분 후 재시도합니다.")
                            await asyncio.sleep(60)
                            continue
                    
                    # 장중 실시간 감시
                    if not self._monitoring_started:
                        print(f"📡 장중 실시간 감시 루프 진입 (감시 종목: {len(self.target_list)}개, 보유 종목: {len(self.portfolio.positions)}개)")
                        self._monitoring_started = True
                        self._last_heartbeat = time.time()
                        self._loop_count = 0

                    self._loop_count += 1

                    # 1) 보유 종목 매도 감시 (즉시성 중요 → 순차 전부 체크)
                    for code, pos in list(self.portfolio.positions.items()):
                        await self._check_position_async(code, pos)

                    # 2) 수동 매도 주문 감시 (대시보드 접수 건)
                    await self._check_manual_orders_async()

                    # 3) 감시 대상 매수 탐색 (라운드 로빈: 매 루프당 최대 5종목씩)
                    if self.portfolio.can_enter() and self.target_list:
                        batch_size = 5
                        start_idx = (self._loop_count * batch_size) % len(self.target_list)
                        batch = self.target_list[start_idx:start_idx + batch_size]
                        for code in batch:
                            await self._check_target_async(code)

                    # 5분(300초)마다 heartbeat 로그 및 잔고 하드 싱크
                    now_ts = time.time()
                    if now_ts - self._last_heartbeat >= 300:
                        elapsed_min = int((now_ts - self._last_heartbeat) / 60)
                        print(f"💓 [Heartbeat] 감시 루프 정상 작동 중 | 반복: {self._loop_count}회 | 보유: {len(self.portfolio.positions)}종목 | 감시: {len(self.target_list)}종목 | 진입가능: {self.portfolio.can_enter()}")
                        self._last_heartbeat = now_ts
                        self._loop_count = 0

                        # 5분마다 키움증권 앱과 봇의 보유 주식 상태를 강제 동기화
                        await self._sync_and_update_balance_db()

                        # ★ 장중 분봉 데이터 수집: 피보나치 판단에 필요한 최신 분봉 유지
                        if self.target_list:
                            print(f"📊 [장중 분봉 수집] 감시 종목 {len(self.target_list)}개 분봉 데이터 갱신 중...")
                            await self.data_collector.update_minute_data(self.target_list)
                            print(f"✅ [장중 분봉 수집] 완료")


                    await asyncio.sleep(1.0)
                
                elif now > end_time:
                    if not self.data_synced_today and last_prep_date == today_str:
                        print("🌙 장 마감. DB 배치 수집 및 일일 리포트 생성 중...")
                        if self.target_list:
                            await self.data_collector.update_minute_data(self.target_list)
                        self.portfolio.reset_daily_count()
                        
                        # 일일 수익 정산 및 대시보드용 balance 업데이트
                        await self.generate_daily_report()
                        
                        self.data_synced_today = True
                        print("✅ 당일 영업 마감 완료. 다음 날 아침(08:55)까지 대기 상태로 들어갑니다.")
                    await asyncio.sleep(600)
                else:
                    # 자정 ~ 08:55 사이 새벽 대기 시간
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            print("루프 취소됨.")
        except Exception as e:
            print(f"❌ 시스템 오류 발생: {e}")
        finally:
            await self.shutdown()

    async def _check_manual_orders_async(self):
        """대시보드에서 들어온 수동 매도/매수 주문을 처리"""
        pending_orders = await self.db.get_pending_manual_orders()
        for order in pending_orders:
            try:
                code = order['code']
                side = order['side']
                qty = order['qty']
                order_id = order['id']
                
                print(f"🔔 [수동 주문 접수] {side} {code} {qty}주")
                
                data = await self.kiwoom.get_price(code)
                if not data:
                    print(f"⚠️ 수동 매도 실패: 현재가 조회 불가 ({code})")
                    continue
                    
                output = data.get('output', [{}])[0] if isinstance(data.get('output'), list) else data.get('output', data)
                current_price = abs(int(output.get('cur_prc', 0) or output.get('price', 0) or output.get('stck_prpr', 0)))
                
                if current_price <= 0:
                    continue
                
                name = "수동매도종목"
                if code in self.portfolio.positions:
                    name = self.portfolio.positions[code].get('name', code)
                
                if side == 'SELL':
                    await self._execute_sell(code, name, qty, current_price, reason="수동매도")
                    self.portfolio.update_position_qty(code, qty)
                    await self.db.save_portfolio(self.portfolio.positions)
                    await self.db.complete_manual_order(order_id, 'COMPLETED')
                
            except Exception as e:
                print(f"❌ 수동 주문 처리 에러: {e}")

    async def _execute_buy(self, code, name, qty, current_price, reason):
        """지정가 매수 주문 + 체결 확인. 성공 시 True 반환."""
        print(f"🚀 [BUY] {name}({code}) {qty}주 매수 시도! (사유: {reason}, 지정가: {current_price:,})")
        res = await self.kiwoom.send_order(code, qty, current_price, order_type="0", side="BUY")
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        if res and str(rt_cd) == '0':
            await self.db.log_order(code, name, "BUY", qty, current_price)
            await self.db.log_message("INFO", f"매수 완료: {name}({code}) {qty}주 @ {current_price:,}원")
            print(f"✅ [BUY_OK] {name}({code}) {qty}주 매수 완료!")

            # 체결 확인: 5초 후 잔고 및 예수금 재조회
            await asyncio.sleep(5)
            await self._sync_and_update_balance_db()
            try:
                positions_data = await self.kiwoom.get_account_positions()
                if positions_data:
                    raw = []
                    if 'output' in positions_data:
                        output_obj = positions_data['output']
                        if isinstance(output_obj, list): raw = output_obj
                        elif isinstance(output_obj, dict): raw = output_obj.get('stk_cntr_remn') or output_obj.get('acnt_info') or []
                    if not raw:
                        raw = positions_data.get('stk_cntr_remn') or positions_data.get('acnt_info') or []
                    if raw and not isinstance(raw, list): raw = [raw]
                    
                    matched = [p for p in raw if (p.get('stk_cd', '').replace('A', '') == code)]
                    if matched:
                        filled_qty = int(float(matched[0].get('hldg_qty') or matched[0].get('cur_qty', 0)))
                        print(f"🔍 [체결확인] {name}({code}) 실제 보유: {filled_qty}주")
                    else:
                        print(f"⚠️ [체결확인] {name}({code}) 잔고에서 미확인 (미체결 가능성)")
            except Exception:
                pass
            return True
        elif res:
            msg = res.get('msg1') or res.get('return_msg') or '알 수 없는 오류'
            print(f"❌ [BUY_FAIL] {name}({code}) 매수 실패 — API 응답: rt_cd={rt_cd}, msg={msg}")
            await self.db.log_message("ERROR", f"매수 실패: {name}({code}) {msg}")
        else:
            print(f"❌ [BUY_FAIL] {name}({code}) 매수 실패 — API 응답 없음")
            await self.db.log_message("ERROR", f"매수 실패: {name}({code}) API 무응답")
        return False

    async def _execute_sell(self, code, name, qty, current_price, reason):
        print(f"📉 [SELL] {name}({code}) {qty}주 매도 시도! (사유: {reason}, 지정가: {current_price:,})")
        res = await self.kiwoom.send_order(code, qty, current_price, order_type="0", side="SELL")
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        if res and str(rt_cd) == '0':
            await self.db.log_order(code, name, "SELL", qty, current_price)
            await self.db.log_message("INFO", f"매도 완료: {name}({code}) {qty}주 @ {current_price:,}원 ({reason})")
            print(f"✅ [SELL_OK] {name}({code}) {qty}주 매도 완료!")
            
            # 5초 후 잔고 재조회 및 갱신
            await asyncio.sleep(5)
            await self._sync_and_update_balance_db()
        elif res:
            msg = res.get('msg1') or res.get('return_msg') or '알 수 없는 오류'
            print(f"❌ [SELL_FAIL] {name}({code}) 매도 실패 — API 응답: rt_cd={rt_cd}, msg={msg}")
            await self.db.log_message("ERROR", f"매도 실패: {name}({code}) {msg}")
        else:
            print(f"❌ [SELL_FAIL] {name}({code}) 매도 실패 — API 응답 없음")
            await self.db.log_message("ERROR", f"매도 실패: {name}({code}) API 무응답")

    async def _sync_and_update_balance_db(self):
        """매수/매도 직후 최신 잔고 및 보유 종목을 API로 조회하여 봇 내부 상태와 DB를 동기화합니다"""
        balance_data = await self.kiwoom.get_account_balance()
        if balance_data:
            # 1. 자본금 파싱
            raw_output = balance_data.get('output', balance_data)
            if isinstance(raw_output, list) and len(raw_output) > 0:
                raw_output = raw_output[0]
            
            deposit = self.current_capital
            possible_keys = ['entr_d2', 'd2_deposit', 'ord_alowa', 'entr', 'prvs_rcdl_excc_amt', 'ord_psbl_cash', 'dnca_tot_amt', 'deposit', '주문가능금액']
            for key in possible_keys:
                if key in raw_output and str(raw_output[key]).strip().replace(',', '').isdigit():
                    deposit = int(str(raw_output[key]).strip().replace(',', ''))
                    if deposit > 0: break
            
            self.current_capital = deposit
            self.portfolio.sync_capital(deposit)
            
            # 2. 보유 종목 완벽 하드 싱크 파싱
            raw = []
            if 'output' in balance_data:
                output_obj = balance_data['output']
                if isinstance(output_obj, list): raw = output_obj
                elif isinstance(output_obj, dict): raw = output_obj.get('stk_cntr_remn') or output_obj.get('acnt_info') or []
            if not raw:
                raw = balance_data.get('stk_cntr_remn') or balance_data.get('acnt_info') or []
            if raw and not isinstance(raw, list): raw = [raw]
            
            if raw:
                self.portfolio.sync_positions(raw)
            else:
                self.portfolio.sync_positions([])
                
            await self.db.save_portfolio(self.portfolio.positions)

            # 3. 총 자산 (예수금 + 주식평가금액) 계산
            total_eval = deposit
            
            # 주식 총 평가금액 (evlt_amt_tot) 추출
            stock_eval = 0
            stock_eval_keys = ['evlt_amt_tot', 'tot_evlu_amt', '총평가금액']
            for key in stock_eval_keys:
                if key in raw_output and str(raw_output[key]).strip().replace(',', '').replace('-','').isdigit():
                    stock_eval = int(str(raw_output[key]).strip().replace(',', ''))
                    if stock_eval > 0: break
            
            # API에서 총평가액을 정상적으로 주지 않았을 경우, 보유 종목에서 직접 합산 (Foolproof)
            if stock_eval <= 0 and raw:
                for p in raw:
                    qty = int(float(p.get('hldg_qty') or p.get('cur_qty', 0)))
                    price = abs(int(float(p.get('cur_prc') or p.get('prpr', 0) or p.get('buy_uv', 0))))
                    stock_eval += (qty * price)
                    
            if stock_eval > 0:
                total_eval = deposit + stock_eval
            else:
                # 혹시 총자산(예탁자산평가액) 단일 키가 있는 경우 (fallback)
                fallback_keys = ['aset_evlt_amt', 'tot_amt']
                for key in fallback_keys:
                    if key in raw_output and str(raw_output[key]).strip().replace(',', '').replace('-','').isdigit():
                        total_eval = int(str(raw_output[key]).strip().replace(',', ''))
                        if total_eval > 0: break

            yield_rate = 0.0
            yield_keys = ['tot_pl_rt', 'tot_evlu_pfls_rt', 'yield', '수익률']
            for key in yield_keys:
                if key in raw_output and str(raw_output[key]).strip().replace(',', '').replace('-','').replace('.','').isdigit():
                    yield_rate = float(str(raw_output[key]).strip().replace(',', '').replace('%',''))
                    break
            
            # 4. MDD 셧다운 검사 로직 (-5% 하락 시 셧다운)
            if total_eval > self.highest_total_asset:
                self.highest_total_asset = total_eval
            
            mdd = 0.0
            if self.highest_total_asset > 0:
                mdd = ((total_eval - self.highest_total_asset) / self.highest_total_asset) * 100.0
                
            if mdd <= -5.0 and not self.mdd_shutdown:
                self.mdd_shutdown = True
                await self.db.log_message("WARNING", f"🚨 [서킷 브레이커 발동] 계좌 MDD {mdd:.2f}% 도달 (-5% 초과). 당일 신규 매수를 전면 중단합니다.")
                print(f"🚨 [서킷 브레이커] 당일 최고 자산 대비 -5% 초과 하락! 신규 매수 중단.")

            # 4. DB balance 테이블 갱신
            today = datetime.now().strftime('%Y-%m-%d')
            query = f"SELECT * FROM order_history WHERE DATE(timestamp) = '{today}'"
            df = await self.db.get_dataframe(query)
            daily_profit = 0
            if not df.empty:
                buy_total = df[df['side'] == 'BUY']['price'] * df[df['side'] == 'BUY']['qty']
                sell_total = df[df['side'] == 'SELL']['price'] * df[df['side'] == 'SELL']['qty']
                daily_profit = int(sell_total.sum() - buy_total.sum())

            await self.db.update_balance(total_eval, deposit, daily_profit, yield_rate)
            print(f"🔄 [계좌 싱크 완료] 자산 {total_eval:,}원 / 예수금 {deposit:,}원 / 보유 {len(self.portfolio.positions)}종목")

    async def generate_daily_report(self):
        """대시보드 표출을 위한 일일 손익 요약 계산 및 DB(balance) 적재"""
        today = datetime.now().strftime('%Y-%m-%d')
        query = f"SELECT * FROM order_history WHERE DATE(timestamp) = '{today}'"
        df = await self.db.get_dataframe(query)
        
        if df.empty:
            await self.db.log_message("REPORT", "당일 매매 내역이 없습니다.")
            return
            
        buy_total = df[df['side'] == 'BUY']['price'] * df[df['side'] == 'BUY']['qty']
        sell_total = df[df['side'] == 'SELL']['price'] * df[df['side'] == 'SELL']['qty']
        
        # 간이 PnL 계산
        daily_profit = sell_total.sum() - buy_total.sum()
        
        # 실제 API에서 잔고를 조회하여 DB를 갱신
        balance_data = await self.kiwoom.get_account_balance()
        if balance_data and 'output' in balance_data:
            out = balance_data['output']
            if isinstance(out, list) and len(out) > 0: out = out[0]
            
            deposit = int(out.get('deposit') or out.get('dnca_tot_amt') or self.current_capital)
            total_eval = int(out.get('tot_evlu_amt') or out.get('tot_amt') or self.current_capital)
            yield_rate = float(out.get('tot_evlu_pfls_rt') or out.get('yield') or 0.0)
            
            await self.db.update_balance(total_eval, deposit, daily_profit, yield_rate)
            await self.db.log_message("REPORT", f"일일 결산: 총자산 {total_eval:,}원 / 당일손익 {daily_profit:,}원")

    async def shutdown(self):
        print("시스템 종료 및 리소스 반환...")
        await self.kiwoom.close_session()
        await self.db.close_pool()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true', help='실전투자 모드')
    args = parser.parse_args()

    system = TradingSystemRest(is_demo=not args.real)
    await system.run()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("사용자에 의한 종료")
