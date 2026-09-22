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
import time
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from async_kiwoom_client import AsyncKiwoomClient, RequestPriority
from async_portfolio import AsyncPortfolioManager
from database import AsyncDatabase
from market_data_buffer import MarketDataBuffer
from strategy import AdaptiveVolatilityBreakoutStrategy
from indicators import TechnicalIndicators
from notifier import AsyncNotifier
from macro_regime_filter import MacroRegimeFilter, MarketRegime

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

class OrderTimeoutManager:
    """
    미체결 주문(Unfilled Orders) 실시간 추적 및 자동 취소/대체(Cancel & Replace) 안전장치
    - 주문 접수 후 3분(180초) 경과 미체결 주문 식별
    - 미체결 매수(BUY): kt10003 취소 발송 -> 예수금 증거금 즉시 반환
    - 미체결 매도(SELL): kt10003 취소 후 RequestPriority.CRITICAL 시장가(03) 전량 청산 재발주
    """
    def __init__(self, bot=None, client=None, db=None, timeout_seconds: float = 180.0):
        self.bot = bot
        self.client = client
        self.db = db
        self.timeout_seconds = timeout_seconds
        self.tracked_orders: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def track_order(self, order_no: str, code: str, name: str, side: str, qty: int, price: float, order_type: str = "00"):
        """신규 발주된 주문 추적 등록"""
        if not order_no or str(order_no).strip() == "" or str(order_no) == "0":
            return
        clean_ord_no = str(order_no).strip()
        async with self._lock:
            self.tracked_orders[clean_ord_no] = {
                "order_no": clean_ord_no,
                "code": str(code).replace('A', '').strip(),
                "name": name,
                "side": side.upper(),
                "qty": int(qty),
                "unfilled_qty": int(qty),
                "price": float(price),
                "order_type": order_type,
                "timestamp": time.time()
            }
        print(f"📋 [OrderTracker] 주문 추적 등록: 주문번호 {clean_ord_no} ({side} {name} {qty}주 @ {price:,.0f}원)")

    async def on_chejan_data(self, data: Dict[str, Any]):
        """키움 OnReceiveChejanData 체결/잔고 실시간 이벤트 수신 처리"""
        if not data or not isinstance(data, dict):
            return

        order_no = str(data.get('ord_no') or data.get('odno') or data.get('order_no') or '').strip()
        if not order_no:
            return

        async with self._lock:
            if order_no in self.tracked_orders:
                item = self.tracked_orders[order_no]
                cntg_qty = int(float(str(data.get('cntg_qty') or data.get('chejan_qty') or 0)))
                uncl_qty = int(float(str(data.get('uncl_qty') or data.get('otst_qty') or 0)))
                status = str(data.get('ord_stts') or data.get('status') or '')

                if uncl_qty > 0:
                    item['unfilled_qty'] = uncl_qty
                elif cntg_qty > 0:
                    item['unfilled_qty'] = max(0, item['unfilled_qty'] - cntg_qty)

                if item['unfilled_qty'] <= 0 or status in ('체결', '완료', '취소', 'FILLED', 'CANCELLED'):
                    print(f"✅ [OrderTracker] 주문 체결/완료 확인 -> 추적 종료: 주문번호 {order_no} ({item['name']})")
                    del self.tracked_orders[order_no]

    async def check_and_resolve_timeouts(self):
        """3분 경과 미체결 주문 검사 및 자동 취소/재발주 실행"""
        now = time.time()
        expired_orders = []

        async with self._lock:
            for ord_no, info in list(self.tracked_orders.items()):
                elapsed = now - info['timestamp']
                if elapsed >= self.timeout_seconds and info['unfilled_qty'] > 0:
                    expired_orders.append(info.copy())
                    del self.tracked_orders[ord_no]

        for order in expired_orders:
            ord_no = order['order_no']
            code = order['code']
            name = order['name']
            side = order['side']
            uncl_qty = order['unfilled_qty']

            print(f"⚠️ [OrderTimeout] 미체결 타임아웃({int(self.timeout_seconds)}초 경과) 감지: 주문번호 {ord_no} ({side} {name} {uncl_qty}주)")

            if side == "BUY":
                # 미체결 매수 -> 취소하여 예수금 반환
                if self.client and hasattr(self.client, 'cancel_order'):
                    await self.client.cancel_order(order_no=ord_no, code=code, qty=uncl_qty, priority=RequestPriority.HIGH)
                    if self.db:
                        await self.db.log_message("WARNING", f"🛡️ [미체결 매수 취소] 주문번호 {ord_no} ({name} {uncl_qty}주) 취소 접수 -> D+2 예수금 증거금 즉시 반환")
                    print(f"🛡️ [미체결 매수 취소] {name}({code}) {uncl_qty}주 취소 완료 (예수금 반환)")
            elif side == "SELL":
                # 미체결 매도 -> 지정가 취소 후 즉시 긴급 시장가(03) 전량 재발주
                if self.client and hasattr(self.client, 'cancel_order'):
                    await self.client.cancel_order(order_no=ord_no, code=code, qty=uncl_qty, priority=RequestPriority.HIGH)

                print(f"🚨 [미체결 매도 타임아웃] {name}({code}) 지정가 취소 후 즉시 긴급 시장가(03) CRITICAL 전량 청산 재발주!")
                if self.client:
                    sell_res = await self.client.send_order(code=code, qty=uncl_qty, price=0, order_type="03", side="SELL", priority=RequestPriority.CRITICAL)
                    new_ord_no = str((sell_res or {}).get('ord_no') or (sell_res or {}).get('odno') or '').strip()
                    if new_ord_no and new_ord_no != '0':
                        await self.track_order(new_ord_no, code, name, "SELL", uncl_qty, 0, order_type="03")
                if self.db:
                    await self.db.log_message("CRITICAL", f"🚨 [미체결 매도 대체] {name}({code}) {uncl_qty}주 긴급 시장가(03) 재청산 발주 완료!")

            if self.bot and hasattr(self.bot, '_sync_account_balance'):
                await self.bot._sync_account_balance()

class AsyncTradingBot:
    """
    완전 비동기(asyncio/aiohttp) 키움증권 퀀트 트레이딩 봇 데몬
    - 4단계 선점형 우선순위 큐(CRITICAL/HIGH/MEDIUM/LOW) 연동
    - 동시성 안전 포트폴리오 관리자(AsyncPortfolioManager) + 프랙셔널 켈리 자산 배분
    - 인메모리 링버퍼(MarketDataBuffer) 및 비동기 배치 DB 영속화
    - ATR 적응형 변동성 돌파 & 샹들리에 엑시트(Chandelier Exit) 트레일링 스탑
    - KODEX 200 지수 급락 필터 & MDD -5% 계좌 서킷 브레이커
    - 텔레그램 실시간 비동기 알림 (AsyncNotifier)
    - 수동 주문 비동기 처리 및 DB 영속화
    """
    def __init__(self, is_demo: Optional[bool] = None, initial_capital: float = 10_000_000,
                 client: Optional[AsyncKiwoomClient] = None,
                 portfolio: Optional[AsyncPortfolioManager] = None,
                 db: Optional[AsyncDatabase] = None,
                 buffer: Optional[MarketDataBuffer] = None,
                 strategy: Optional[AdaptiveVolatilityBreakoutStrategy] = None,
                 notifier: Optional[AsyncNotifier] = None):
        if is_demo is None:
            env_is_mock = os.getenv("IS_REAL", "true").lower() in ("false", "0", "no") or os.getenv("KIWOOM_MODE", "REAL").upper() in ("MOCK", "DEMO")
            is_demo = env_is_mock
        self.is_demo = is_demo
        self.client = client or AsyncKiwoomClient(is_demo=is_demo)
        self.portfolio = portfolio or AsyncPortfolioManager(initial_capital=initial_capital, max_stocks=5)
        self.db = db or AsyncDatabase()
        self.buffer = buffer or MarketDataBuffer(db_manager=self.db, buffer_maxlen=60)
        self.strategy = strategy or AdaptiveVolatilityBreakoutStrategy(db_manager=self.db, buffer_manager=self.buffer)
        self.notifier = notifier or AsyncNotifier()
        self.macro_filter = MacroRegimeFilter(kodex_crash_threshold=-1.5, vix_panic_threshold=28.0)

        self.market_filter_passed = True
        self.kodex200_change_rate = 0.0
        self.mdd_shutdown = False
        self.highest_total_asset = 0.0  # 최초 계좌 동기화 시 실제 자산으로 캘리브레이션
        self.daily_start_capital = 0.0  # 당일 시작 자산 (일일 손실률 추적용)
        self.daily_loss_limit_rate = -0.025  # 당일 최대 손실 한도: -2.5% (초과 시 서킷 브레이커 발동)
        self.daily_circuit_breaker = False   # 일일 최대 손실 제한 차단 플래그
        self.is_running = False
        self.is_paused = False
        self.is_shutdown = False

        # 감시 종목 리스트 (코드, 이름, 피보나치 레벨 정보)
        self.watchlist_size = int(os.getenv("WATCHLIST_SIZE", "30"))  # 기본 30종목으로 확대
        self.watchlist: Dict[str, Dict[str, Any]] = {}

        # 분할 익절 단계 목표치 (1차 전체의 50%, 2차 잔여 물량의 50%, 3차 나머지 전량 100%)
        self.take_profit_stages = [
            (0.03, 0.50, 1),  # +3% 도달 시 1차 익절 (전체의 50% 매도) -> Stage 1
            (0.05, 0.50, 2),  # +5% 도달 시 2차 익절 (잔여 물량의 50% 매도) -> Stage 2
            (0.08, 1.00, 3),  # +8% 도달 시 3차 익절 (나머지 전량 100% 매도) -> Stage 3
        ]
        self.stop_loss_rate = -0.03  # -3.0% 타이트한 하드 스탑로스 (CRITICAL 긴급 매도)
        self.trailing_stop_drop = 0.020  # 최고점 대비 2.0% 반락 시 트레일링 스탑 매도
        self.realtime_stream_task: Optional[asyncio.Task] = None

        # 실시간 감시 로그 쓰로틀링 상태 맵 (종목코드 -> 마지막 로그 시간/괴리율)
        self._last_watch_log_time: Dict[str, float] = {}
        self._last_watch_diff_pct: Dict[str, float] = {}
        self.unclosed_orders_count = 0

        # 미체결 주문 3분 타임아웃 자동 취소/대체 매니저
        self.order_timeout_mgr = OrderTimeoutManager(bot=self, client=self.client, db=self.db, timeout_seconds=180.0)

    @property
    def running(self) -> bool:
        """봇 구동 상태 getter (실행 중이면서 일시정지 상태가 아닐 때 True)"""
        return self.is_running and not self.is_paused

    @running.setter
    def running(self, value: bool):
        """봇 구동 상태 setter"""
        self.is_running = bool(value)
        if value:
            self.is_paused = False
        else:
            self.is_paused = True

    def pause(self):
        """수동 일시정지 (당일 매매 중단, 익일 08:50 AM 자동 웨이크업 예약)"""
        self.is_paused = True
        self.is_running = False
        print("⏸️ [AsyncTradingBot] 봇 수동 일시정지 (익일 영업일 아침 08:50 AM 자동 웨이크업 예약)")

    def resume(self):
        """수동/자동 매매 재개"""
        self.is_paused = False
        self.is_running = True
        print("▶️ [AsyncTradingBot] 봇 매매 재개 (RUNNING)")

    @staticmethod
    def is_korean_market_holiday(dt: datetime) -> bool:
        """한국 거래소 기본 공휴일 판별 (주말 및 법정공휴일/휴장일)"""
        # 주말 (토=5, 일=6)
        if dt.weekday() >= 5:
            return True
        # 양력 고정 공휴일 및 증시 폐장일 (월, 일)
        fixed_holidays = {
            (1, 1),   # 신정
            (3, 1),   # 삼일절
            (5, 1),   # 근로자의 날 (주식시장 휴장)
            (5, 5),   # 어린이날
            (6, 6),   # 현충일
            (8, 15),  # 광복절
            (10, 3),  # 개천절
            (10, 9),  # 한글날
            (12, 25), # 성탄절
            (12, 31), # 연말 주식시장 휴장일
        }
        if (dt.month, dt.day) in fixed_holidays:
            return True
        return False

    async def SetRealReg(self, screen_no: str, code_list: List[str], fid_list: List[str], opt_type: str = "0") -> bool:
        """
        키움증권 실시간 시세/체결 감시 등록 (OpenAPI SetRealReg 호환)
        - Watchlist 30개 확정 즉시 호출하여 실시간 수신 파이프라인 활성화
        """
        if hasattr(self.client, 'SetRealReg'):
            await self.client.SetRealReg(screen_no, code_list, fid_list, opt_type)
        print(f"📡 [SetRealReg] Watchlist 실시간 시세 등록 완료: 총 {len(code_list)}개 종목 (화면: {screen_no}, FID: {','.join(fid_list)})")
        return True

    async def OnReceiveRealData(self, code: str, real_type: str, real_data: Dict[str, Any]):
        """
        실시간 데이터 수신 이벤트 핸들러 (OpenAPI OnReceiveRealData 호환)
        - 실시간 틱 수신 시 최상단 핑(Ping) 디버그 로그 출력
        - 인메모리 링버퍼 및 포트폴리오/워치리스트 현재가/시가 즉각 갱신
        - 피보나치 매수 조건 평가 함수(_evaluate_buy_condition) 즉시 호출
        """
        raw_p = real_data.get('current_price') or real_data.get('prpr') or real_data.get('stck_prpr') or real_data.get('cur_prc') or 0
        raw_v = real_data.get('volume') or real_data.get('acml_vol') or real_data.get('cntg_vol') or 0
        raw_open = real_data.get('open_price') or real_data.get('oprn') or real_data.get('stck_oprc') or real_data.get('open_pric') or 0

        try:
            cur_price = abs(float(str(raw_p).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
        except (ValueError, TypeError):
            cur_price = 0.0

        try:
            cur_volume = abs(float(str(raw_v).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
        except (ValueError, TypeError):
            cur_volume = 0.0

        try:
            open_price = abs(float(str(raw_open).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
        except (ValueError, TypeError):
            open_price = 0.0

        if cur_price <= 0:
            return

        # 1. 핑(Ping) 디버그 로그 (실시간 틱 수신 가시화)
        print(f"⚡ [실시간 틱 수신] 종목코드: {code}, 현재가: {int(cur_price):,}원")

        # 2. 인메모리 링버퍼 및 상태 갱신
        self.buffer.update_tick(code, cur_price, cur_volume)
        await self.portfolio.update_current_price(code, cur_price)
        if code in self.watchlist:
            self.watchlist[code]['current_price'] = cur_price
            if open_price > 0:
                self.watchlist[code]['open_price'] = open_price

        # 3. 피보나치 및 ATR 변동성 돌파 매수 조건 평가 함수 즉각 호출
        await self._evaluate_buy_condition(code, cur_price, cur_volume, raw_data=real_data)

    async def OnReceiveChejanData(self, gubun: str, item_cnt: int, fid_list: str, data: Dict[str, Any]):
        """
        실시간 체결 및 잔고 통보 이벤트 핸들러 (OpenAPI OnReceiveChejanData 호환)
        - gubun: '0'(주문체결통보), '1'(국내주식 잔고통보)
        - OrderTimeoutManager에 실시간 전달하여 체결 수량 차감 및 미체결 해소
        """
        if hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
            await self.order_timeout_mgr.on_chejan_data(data)

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
        """클라이언트, DB 풀, 인메모리 버퍼, 텔레그램 알림, 계좌 상태 초기화"""
        print(f"🚀 [AsyncTradingBot] 엔진 초기화 시작 (모드: {'모의투자' if self.is_demo else '실전투자'})...")
        await self.db.init_pool()
        await self.buffer.start()
        await self.notifier.start()
        await self.client.start()

        # DB에서 직전 계좌 잔고 및 포지션 복원 (오프마켓/장 시작 전 0원 방어)
        if hasattr(self.db, 'get_latest_balance'):
            try:
                db_bal = await self.db.get_latest_balance()
                if db_bal and float(db_bal.get('total_asset', 0)) > 0:
                    db_total = float(db_bal.get('total_asset'))
                    db_deposit = float(db_bal.get('deposit', db_total))
                    self.portfolio.initial_capital = db_total
                    # total_asset(총자산)과 current_capital(D+2 예수금)을 독립적으로 복원
                    self.portfolio.total_asset = db_total
                    self.portfolio.current_capital = db_deposit
                    print(f"🔧 [Bot Init] DB 잔고 복원: 총자산={int(db_total):,}원 / D+2예수금={int(db_deposit):,}원")
                if hasattr(self.db, 'get_portfolio_positions'):
                    db_pos = await self.db.get_portfolio_positions()
                    if db_pos:
                        await self.portfolio.restore_positions_from_db(db_pos)
            except Exception as e:
                print(f"⚠️ [Bot Init] DB 계좌 복원 예외: {e}")

        await self._sync_account_balance()
        self.is_running = True
        self.notifier.send_message(f"🚀 [Kiwoom Quant Bot] 비동기 트레이딩 데몬 가동 완료 (모드: {self.client.mode})")
        await self.db.log_message("SYSTEM", f"비동기 트레이딩 데몬 가동 완료 (모드: {self.client.mode})")

    async def _sync_account_balance(self):
        """계좌 잔고 및 예수금 비동기 동기화 (듀얼 TR: kt00001 예수금상세 + kt00005 계좌평가)"""
        # 1) kt00005 계좌평가잔고 (보유종목, 총평가금액, D+2예수금)
        balance_data = await self.client.get_account_balance(priority=RequestPriority.MEDIUM)
        # 2) kt00001 예수금상세현황 (당일 순수 예수금 원금, 전일예수금, D+2 추정예수금)
        deposit_data = await self.client.get_deposit_info(priority=RequestPriority.MEDIUM)

        if not balance_data and not deposit_data:
            return

        # 1. 예수금 및 총평가금액 파싱 대상 수집 (balance_data 및 deposit_data)
        summary_candidates = []
        if balance_data:
            if isinstance(balance_data.get('output1'), list) and balance_data['output1']:
                summary_candidates.append(balance_data['output1'][0])
            elif isinstance(balance_data.get('output1'), dict):
                summary_candidates.append(balance_data['output1'])

            if isinstance(balance_data.get('output'), list) and balance_data['output']:
                summary_candidates.append(balance_data['output'][0])
            elif isinstance(balance_data.get('output'), dict):
                summary_candidates.append(balance_data['output'])

            summary_candidates.append(balance_data)

        if deposit_data:
            if isinstance(deposit_data.get('output1'), list) and deposit_data['output1']:
                summary_candidates.append(deposit_data['output1'][0])
            elif isinstance(deposit_data.get('output1'), dict):
                summary_candidates.append(deposit_data['output1'])

            if isinstance(deposit_data.get('output'), list) and deposit_data['output']:
                summary_candidates.append(deposit_data['output'][0])
            elif isinstance(deposit_data.get('output'), dict):
                summary_candidates.append(deposit_data['output'])

            summary_candidates.append(deposit_data)

        # 키움 계좌 TR 원본 필드 디버그 프리뷰 수집
        raw_fields_debug: Dict[str, Any] = {}
        for s_dict in summary_candidates:
            if isinstance(s_dict, dict):
                for k, v in s_dict.items():
                    if k not in raw_fields_debug and not isinstance(v, (list, dict)):
                        raw_fields_debug[k] = v

        # 키움 TR 디버그 로깅 강화: balance_data 및 deposit_data 수신 키 목록
        bal_keys = list(balance_data.keys()) if isinstance(balance_data, dict) else []
        dep_keys = list(deposit_data.keys()) if isinstance(deposit_data, dict) else []
        if bal_keys or dep_keys:
            print(f"📡 [TR 수신 상태] 잔고TR 키: {bal_keys} | 예수금TR 키: {dep_keys}")

        # 1) 총평가금액 / 총자산 키 목록 (키움 HTS 총평가: 148,442원 / 64,400원 주식평가 + 82,819원 D+2예수금)
        tot_evlu_keys = [
            'tot_evlu_amt', 'tot_asst_amt', 'aset_evlt_amt', 'asst_tot_amt',
            'evlu_amt_tot', 'evlt_amt_tot', '총평가금액', '총자산금액', '자산평가금액', '예탁자산평가액'
        ]

        # 2) D+2 추정예수금 / 주문가능금액 키 목록 (실제 D+2 예수금: 82,819원)
        # 중요: 키움 REST API에서 dnca_tot_amt는 당일 예수금(1,122원)으로 올 수 있으므로 진짜 D+2 키를 최우선 순위로 배치
        d2_deposit_keys = [
            'd2_deposit', 'd2_auto_amt', 'd2_prvs_rcdl_amt', 'd2_prvs_rcdl_excc_amt',
            'd2_ccls_amt', 'd2_estm_amt', 'prvs_rcdl_excc_amt_smtl_amt',
            'entr_d2', 'd2_entr', 'd2_ord_psbl_amt', 'ord_psbl_cash', 'ord_psbl_amt',
            'ord_alowa', 'd2_psbl_amt', 'D+2예수금', 'D+2추정예수금', '추정예수금',
            '주문가능금액', '주문가능현금', 'dnca_tot_amt'
        ]

        # 3) 당일 단순 예수금 원금 / 인출가능금 키 목록 (1,122원)
        raw_entr_keys = [
            'prvs_rcdl_excc_amt', 'entr', 'deposit', 'dnca_tot_amt', '예수금',
            '인출가능금액', '인출가능금', 'prvs_rcdl_amt', '당일예수금'
        ]

        # 4) 대용금 키 목록 (45,530원 - 분리 및 로깅용, 절대 총자산으로 오맵핑 금지)
        sub_amt_keys = [
            'sub_amt', 'sub_tot_amt', '대용금', '대용금액', 'sub_dnca_amt'
        ]

        parsed_tot_evlu_amt: Optional[float] = None
        parsed_d2_deposit: Optional[float] = None
        parsed_raw_entr: Optional[float] = None
        parsed_sub_amt: Optional[float] = None
        unclosed_cnt: int = 0

        for s_dict in summary_candidates:
            if not isinstance(s_dict, dict):
                continue

            # (1) 총평가금액 (148,442원)
            if parsed_tot_evlu_amt is None:
                for key in tot_evlu_keys:
                    val = s_dict.get(key)
                    if val is not None:
                        val_clean = str(val).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            f_val = float(val_clean)
                            if f_val > 0:
                                parsed_tot_evlu_amt = f_val
                                break
                        except ValueError:
                            pass

            # (2) D+2 주문가능금액 / 추정예수금 (1,122원)
            if parsed_d2_deposit is None:
                for key in d2_deposit_keys:
                    val = s_dict.get(key)
                    if val is not None:
                        val_clean = str(val).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            f_val = float(val_clean)
                            if f_val > 0:
                                parsed_d2_deposit = f_val
                                break
                        except ValueError:
                            pass

            # (3) 당일 단순 예수금 원금 (1,122원)
            if parsed_raw_entr is None:
                for key in raw_entr_keys:
                    val = s_dict.get(key)
                    if val is not None:
                        val_clean = str(val).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            f_val = float(val_clean)
                            if f_val > 0:
                                parsed_raw_entr = f_val
                                break
                        except ValueError:
                            pass

            # (4) 대용금 (103,890원)
            if parsed_sub_amt is None:
                for key in sub_amt_keys:
                    val = s_dict.get(key)
                    if val is not None:
                        val_clean = str(val).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            f_val = float(val_clean)
                            if f_val > 0:
                                parsed_sub_amt = f_val
                                break
                        except ValueError:
                            pass

            # 미체결 수량/건수 탐색
            for u_key in ['uncl_cnt', 'uncl_qty', 'unclosed_count', 'uncl_amt', 'otst_qty', 'otst_cnt']:
                if s_dict.get(u_key) is not None:
                    try:
                        u_val = int(float(str(s_dict[u_key]).replace(',', '')))
                        if u_val > 0:
                            unclosed_cnt = max(unclosed_cnt, u_val)
                    except ValueError:
                        pass

        # 미체결 조회 API 보강 (0건으로 감지된 경우 1회 확인)
        if hasattr(self.client, 'get_unexecuted_orders'):
            try:
                uncl_data = await self.client.get_unexecuted_orders(priority=RequestPriority.LOW)
                if uncl_data:
                    u_list = uncl_data.get('output', uncl_data.get('output1', []))
                    if isinstance(u_list, list):
                        unclosed_cnt = max(unclosed_cnt, len(u_list))
            except Exception:
                pass

        self.unclosed_orders_count = unclosed_cnt

        # 2. 보유 종목 동기화 선행 (balance_data 및 deposit_data 양방향 스캔)
        if balance_data:
            await self.portfolio.sync_positions(balance_data)
        if deposit_data and len(self.portfolio.positions) == 0:
            await self.portfolio.sync_positions(deposit_data)

        # 보유 주식 평가액 계산
        invested_eval = sum(pos['current_price'] * pos['qty'] for pos in self.portfolio.positions.values())

        # 3. 주문가능 현금(available_cash) 및 총 평가자산(final_total_asset) 독립 산출
        # (1) 주문가능 현금 (D+2 정산 추정예수금 기준: MTS 화면의 'D+2 예수금'과 1:1 매칭)
        available_cash = parsed_d2_deposit or parsed_raw_entr or self.portfolio.current_capital

        # (2) 총 평가자산 (키움 API가 제공하는 '총평가금액(tot_evlu_amt)' 필드 단일 소스 원칙 반영)
        # 키움 API의 tot_evlu_amt는 이미 (D+2 예수금 + 보유주식 평가금액)이 합산된 계좌 총자산이므로 임의 중복 가산 금지
        if parsed_tot_evlu_amt and parsed_tot_evlu_amt > 0:
            final_total_asset = float(parsed_tot_evlu_amt)
        else:
            # 키움 API 총평가금액 누락 시: (예수금 원금/D+2 중 큰 금액 + 보유주식 평가금) 폴백 산출
            base_cash = max(available_cash, parsed_raw_entr or 0.0)
            final_total_asset = float(base_cash + invested_eval)

        # 키움 계좌 TR Raw Data 분석 로그 출력
        preview_keys = ['tot_evlu_amt', 'prvs_rcdl_excc_amt', 'entr', 'deposit', 'dnca_tot_amt', 'd2_deposit', 'ord_psbl_cash', 'sub_amt']
        matched_raw = {k: raw_fields_debug[k] for k in preview_keys if k in raw_fields_debug}
        print(f"📊 [계좌 TR Raw Data] 키움 수신 필드: {matched_raw}")
        print(f"  ├─ 총평가금액(TR 원본): {int(parsed_tot_evlu_amt):,}원" if parsed_tot_evlu_amt else "  ├─ 총평가금액(TR 원본): None")
        print(f"  ├─ D+2 추정예수금(주문가능): {int(parsed_d2_deposit):,}원" if parsed_d2_deposit else "  ├─ D+2 추정예수금: None")
        print(f"  ├─ 단순 예수금(원금): {int(parsed_raw_entr):,}원" if parsed_raw_entr else "  ├─ 단순 예수금(원금): None")
        print(f"  ├─ 대용금(보유주식담보): {int(parsed_sub_amt):,}원" if parsed_sub_amt else "  ├─ 대용금: None")
        print(f"  ├─ 보유주식 평가금: {int(invested_eval):,}원 ({len(self.portfolio.positions)}종목)")
        print(f"  └─ 최종 산출: [총자산: {int(final_total_asset):,}원 | D+2 주문가능: {int(available_cash):,}원]")

        # 포트폴리오 관리자에 독립 필드로 동기화
        await self.portfolio.sync_capital(available_cash=available_cash, total_asset=final_total_asset)

        # 4. DB 저장 및 스냅샷 확인
        snap = await self.portfolio.get_snapshot()
        if self.highest_total_asset == 0.0 or snap['total_asset'] > self.highest_total_asset:
            self.highest_total_asset = snap['total_asset']

        # 당일 시작 자산 기준점 캘리브레이션
        if self.daily_start_capital == 0.0 and snap['total_asset'] > 0:
            self.daily_start_capital = snap['total_asset']

        # 🚨 [일일 최대 손실 서킷 브레이커] 당일 손실 -2.5% 초과 시 신규 매수 즉시 전면 차단
        if self.daily_start_capital > 0:
            daily_loss_pct = (snap['total_asset'] - self.daily_start_capital) / self.daily_start_capital
            if daily_loss_pct <= self.daily_loss_limit_rate and not self.daily_circuit_breaker:
                self.daily_circuit_breaker = True
                self.mdd_shutdown = True
                breaker_msg = f"🚨 [일일 서킷 브레이커] 당일 누적 손실({daily_loss_pct:.2%})이 일일 한도({self.daily_loss_limit_rate:.1%})를 초과하여 금일 신규 매수를 전면 차단합니다."
                print(breaker_msg)
                await self.db.log_message("CRITICAL", breaker_msg)
                if hasattr(self.notifier, 'send_message'):
                    self.notifier.send_message(breaker_msg)

        # 전체 최고점 대비 MDD 셧다운 검사 (-5% 초과 하락 시 신규 매수 차단)
        if self.highest_total_asset > 0:
            mdd = ((snap['total_asset'] - self.highest_total_asset) / self.highest_total_asset) * 100.0
            if mdd <= -5.0 and not self.mdd_shutdown:
                self.mdd_shutdown = True
                await self.db.log_message("WARNING", f"🚨 [서킷 브레이커] 계좌 MDD {mdd:.2f}% 도달. 당일 신규 매수를 중단합니다.")
                print(f"🚨 [서킷 브레이커] 당일 최고 자산 대비 -5% 초과 하락! (MDD: {mdd:.2f}%) 신규 매수 중단.")

        await self.db.save_portfolio(self.portfolio.positions)
        await self.db.update_balance(snap['total_asset'], snap['current_capital'], snap['unrealized_pnl'], snap['total_yield_rate'])

        # 상세 계좌 싱크 및 예수금 정산 로깅
        sync_log = f"🔄 [계좌 싱크/{self.client.mode}] 총자산 {int(snap['total_asset']):,}원 / D+2 예수금 {int(snap['current_capital']):,}원 / 보유 {snap['stock_count']}종목"
        if unclosed_cnt > 0:
            sync_log += f" (미체결: {unclosed_cnt}건)"
        print(sync_log)

        if snap['total_asset'] != snap['current_capital'] and snap['stock_count'] == 0:
            diff = snap['total_asset'] - snap['current_capital']
            diff_msg = f"💰 [예수금 정산] 총 평가자산: {int(snap['total_asset']):,}원 / D+2 주문가능: {int(snap['current_capital']):,}원 확정 (증거금·정산 차감: {int(diff):,}원, 미체결: {unclosed_cnt}건)"
            print(diff_msg)
            await self.db.log_message("INFO", diff_msg)

    async def update_watchlist(self, top_n: Optional[int] = None):
        """거래대금 상위 종목 수집 및 피보나치 레벨 계산 (기본 30종목, LOW 우선순위)"""
        target_top_n = top_n if top_n is not None else self.watchlist_size
        print(f"🔍 [Watchlist] 거래대금 상위 {target_top_n}종목 스캔 및 피보나치 분석 시작...")
        top_data = await self.client.get_top_trading_value(priority=RequestPriority.LOW)

        # 1. API 응답 추출 (Kiwoom OpenAPI REST 다중 스키마 키 100% 대응)
        items = []
        if top_data:
            if isinstance(top_data, list):
                items = top_data
            elif isinstance(top_data, dict):
                items = (
                    top_data.get('trde_prica_upper') or
                    top_data.get('output') or
                    top_data.get('Output') or
                    top_data.get('list') or
                    top_data.get('trde_val_upper') or
                    top_data.get('data') or
                    []
                )
            if isinstance(items, dict):
                items = [items]

        raw_count = len(items)
        print(f"  📋 [Watchlist Debug] API 수신 원본 종목 수: {raw_count}개")

        # 2. API 미응답 또는 빈 리스트 시 Fallback (MOCK 모드 또는 비상 상황)
        if raw_count == 0:
            if getattr(self, 'is_demo', False) or getattr(self.client, 'mode', '') == 'MOCK':
                print("  ⚠️ [Watchlist Fallback] 모의투자/장외시간 거래대금 상위 미제공 -> 코스피/코스닥 대표 주도주 20종목 자동 주입")
                items = [
                    {'stk_cd': '005930', 'stk_nm': '삼성전자'},
                    {'stk_cd': '000660', 'stk_nm': 'SK하이닉스'},
                    {'stk_cd': '373220', 'stk_nm': 'LG에너지솔루션'},
                    {'stk_cd': '207940', 'stk_nm': '삼성바이오로직스'},
                    {'stk_cd': '005380', 'stk_nm': '현대차'},
                    {'stk_cd': '000270', 'stk_nm': '기아'},
                    {'stk_cd': '068270', 'stk_nm': '셀트리온'},
                    {'stk_cd': '105560', 'stk_nm': 'KB금융'},
                    {'stk_cd': '035420', 'stk_nm': 'NAVER'},
                    {'stk_cd': '035720', 'stk_nm': '카카오'},
                    {'stk_cd': '005490', 'stk_nm': 'POSCO홀딩스'},
                    {'stk_cd': '055550', 'stk_nm': '신한지주'},
                    {'stk_cd': '028260', 'stk_nm': '삼성물산'},
                    {'stk_cd': '012330', 'stk_nm': '현대모비스'},
                    {'stk_cd': '247540', 'stk_nm': '에코프로비엠'},
                    {'stk_cd': '086520', 'stk_nm': '에코프로'},
                    {'stk_cd': '196170', 'stk_nm': '알테오젠'},
                    {'stk_cd': '000100', 'stk_nm': '유한양행'},
                    {'stk_cd': '003670', 'stk_nm': '포스코퓨처엠'},
                    {'stk_cd': '010130', 'stk_nm': '고려아연'}
                ]
                raw_count = len(items)
            else:
                raw_keys = list(top_data.keys()) if isinstance(top_data, dict) else type(top_data)
                print(f"  ⚠️ [Watchlist Debug] 거래대금 상위 응답이 비어있습니다. (Raw keys: {raw_keys})")
                return

        today_str = datetime.now().strftime('%Y%m%d')
        new_watchlist = {}

        # 필터링 단계별 탈락 카운터 (디버깅 관제용)
        drop_reasons = {
            'invalid_code': 0,
            'no_chart_data': 0,
            'insufficient_candles': 0,
            'zero_price_diff': 0,
            'analysis_error': 0,
            'price_over_cash': 0  # 예수금 초과 고가 종목 탈락
        }

        # D+2 주문가능 금액 기반 고가 종목 필터링에 사용할 현재 예수금 캐시
        available_cash = self.portfolio.current_capital

        # 상위 target_top_n개 유효 종목을 채울 때까지 순회 (원본 items 전체 대상)
        for item in items:
            if len(new_watchlist) >= target_top_n:
                break

            if not isinstance(item, dict):
                continue

            raw_code = item.get('stk_cd') or item.get('code') or item.get('mksc_shrn_iscd') or ''
            if not raw_code:
                drop_reasons['invalid_code'] += 1
                continue

            # 종목코드 정제: A접두사 및 _AL, _NX 등 거래소 접미사 제거 → 6자리 표준화
            code = raw_code.replace('A', '').split('_')[0].strip()
            if len(code) != 6 or not code.isdigit():
                drop_reasons['invalid_code'] += 1
                continue

            name = item.get('stk_nm') or item.get('name') or item.get('hts_kor_isnm') or code

            # 키움 API 초당 호출량 분산을 위한 미세 비동기 딜레이
            await asyncio.sleep(0.08)

            # 일봉 차트 조회하여 최근 20일 고가/저가 및 피보나치 레벨 산출
            daily_chart = await self.client.get_daily_chart(code, base_dt=today_str, priority=RequestPriority.LOW)
            if not daily_chart:
                drop_reasons['no_chart_data'] += 1
                continue
            if isinstance(daily_chart, tuple):
                daily_chart = daily_chart[0]
            if not isinstance(daily_chart, dict):
                drop_reasons['no_chart_data'] += 1
                continue

            chart_items = (
                daily_chart.get('stk_dt_pole_chart_qry') or
                daily_chart.get('output2') or
                daily_chart.get('output') or
                daily_chart.get('Output') or
                daily_chart.get('data') or
                []
            )
            if not chart_items or not isinstance(chart_items, list):
                drop_reasons['no_chart_data'] += 1
                continue

            # 최근 20거래일 데이터 추출
            recent_candles = chart_items[:20]
            if len(recent_candles) < 5:
                drop_reasons['insufficient_candles'] += 1
                continue

            try:
                highs = []
                lows = []
                vols = []
                for c in recent_candles:
                    if not isinstance(c, dict):
                        continue
                    h_val = c.get('high_pric') or c.get('hgpr') or c.get('stck_hgpr') or c.get('high_price') or c.get('high') or 0
                    l_val = c.get('low_pric') or c.get('lwpr') or c.get('stck_lwpr') or c.get('low_price') or c.get('low') or 0
                    v_val = c.get('acml_vol') or c.get('vol') or c.get('volume') or c.get('stck_cntg_hour') or 0
                    h = abs(float(str(h_val).replace(',', '').strip()))
                    l = abs(float(str(l_val).replace(',', '').strip()))
                    v = abs(float(str(v_val).replace(',', '').strip()))
                    if h > 0: highs.append(h)
                    if l > 0: lows.append(l)
                    if v > 0: vols.append(v)

                if not highs or not lows:
                    drop_reasons['zero_price_diff'] += 1
                    continue

                period_high = max(highs)
                period_low = min(lows)
                diff = period_high - period_low

                if diff <= 0:
                    drop_reasons['zero_price_diff'] += 1
                    continue

                fib_382 = period_high - (diff * 0.382)
                fib_500 = period_high - (diff * 0.500)
                fib_618 = period_high - (diff * 0.618)
                avg_vol = float(sum(vols) / len(vols)) if vols else 0.0

                # 현재가 및 당일 시가 추출
                c_first = chart_items[0] if isinstance(chart_items[0], dict) else {}
                cur_price_raw = (
                    item.get('prpr') or item.get('cur_prc') or item.get('stck_prpr') or item.get('price') or
                    c_first.get('cur_prc') or c_first.get('clpr') or c_first.get('stck_clpr') or c_first.get('close') or period_high
                )
                cur_price = abs(float(str(cur_price_raw).replace(',', '').strip()))

                open_price_raw = (
                    item.get('oprn') or item.get('stck_oprc') or item.get('open_price') or item.get('open_pric') or
                    c_first.get('oprn') or c_first.get('stck_oprc') or c_first.get('open_pric') or c_first.get('open') or cur_price
                )
                open_price = abs(float(str(open_price_raw).replace(',', '').strip())) if open_price_raw else cur_price

                # [1차 방어] 고가 종목 필터: 현재가 > D+2 주문가능금액이면 Watchlist에서 즉시 제외
                if available_cash > 0 and cur_price > available_cash:
                    drop_reasons['price_over_cash'] += 1
                    print(f"  🚫 [Watchlist 필터] 탈락: 잔고 부족 (현재가 {int(cur_price):,}원 > 예수금 {int(available_cash):,}원) - {name}({code})")
                    continue

                new_watchlist[code] = {
                    'code': code,
                    'name': name,
                    'current_price': cur_price,
                    'open_price': open_price,
                    'period_high': period_high,
                    'period_low': period_low,
                    'fib_382': fib_382,
                    'fib_500': fib_500,
                    'fib_618': fib_618,
                    'avg_volume': avg_vol,
                    'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            except Exception as e:
                drop_reasons['analysis_error'] += 1
                print(f"⚠️ [Watchlist Debug] {name}({code}) 피보나치 분석 예외 발생: {e}")
                continue

        # 단계별 필터링 디버그 리포트 출력
        total_dropped = sum(drop_reasons.values())
        print(f"  📊 [Watchlist Debug] 스캔 요약: 원본 {raw_count}개 ➔ 유효 감시종목 {len(new_watchlist)}개 확정 (탈락 {total_dropped}개: 코드오류 {drop_reasons['invalid_code']}, 일봉부재 {drop_reasons['no_chart_data']}, 캔들부족 {drop_reasons['insufficient_candles']}, 고저차0 {drop_reasons['zero_price_diff']}, 연산오류 {drop_reasons['analysis_error']}, 잔고부족(고가) {drop_reasons['price_over_cash']})")
        if drop_reasons['price_over_cash'] > 0:
            print(f"  💰 [Watchlist 필터] 예수금({int(available_cash):,}원) 초과로 {drop_reasons['price_over_cash']}개 고가 종목이 감시 대상에서 제외되었습니다.")

        self.watchlist = new_watchlist
        await self.db.save_watchlist(list(self.watchlist.values()))
        print(f"✅ [Watchlist] {len(self.watchlist)}개 종목 피보나치 분석 완료 및 DB 저장")

        # Watchlist 30개 확정 직후 키움 실시간 시세 등록 (SetRealReg) 호출
        if self.watchlist:
            watch_codes = list(self.watchlist.keys())
            await self.SetRealReg("1000", watch_codes, ["10", "13", "20", "41"], "0")

    async def check_market_filter(self):
        """KODEX 200 (069500) 및 복합 매크로 레짐 필터링 (급락장 판정 시 신규 매수 제한)"""
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

            # 거시 시장 레짐 평가
            regime, desc = self.macro_filter.evaluate_regime(kodex200_change_pct=fluct_rate)
            if regime == MarketRegime.PANIC_CRASH:
                if self.market_filter_passed:
                    self.market_filter_passed = False
                    await self.db.log_message("WARNING", f"🚨 [시장 급락 감지] {desc}. 신규 매수를 일시 제한합니다.")
                    print(f"🚨 [시장 필터/매크로] {desc} -> 신규 매수 제한 (급락장 방어)")
            else:
                if not self.market_filter_passed:
                    self.market_filter_passed = True
                    await self.db.log_message("INFO", f"✅ [시장 안정 회복] {desc}. 정상 매수를 재개합니다.")
                    print(f"✅ [시장 필터/매크로] {desc} -> 정상 매수 허용")
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
            # [안전 가드] 매수(BUY) 시장가 주문은 지정가(00)로 강제 전환 (ETF 증거금 부족(855056) 방지)
            if side.upper() == "BUY" and order_type == "03":
                print(f"🛡️ [매수 가드] 시장가→지정가 자동 전환: {name}({code}) @ {price}원")
                order_type = "00"
            res = await self.client.send_order(code, qty, price, order_type=order_type, side=side, priority=RequestPriority.HIGH)

            rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
            if res and str(rt_cd) == '0':
                ord_no = str((res or {}).get('ord_no') or (res or {}).get('odno') or (res or {}).get('order_no') or '').strip()
                if ord_no and hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
                    await self.order_timeout_mgr.track_order(ord_no, code, name, side, qty, price, order_type=order_type)
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
            elif not isinstance(out, dict):
                out = {}

            cur_price_raw = out.get('prpr') or out.get('current_price') or out.get('stck_prpr') or out.get('cur_prc') or out.get('clpr') or 0
            try:
                cur_price = abs(float(str(cur_price_raw).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
            except (ValueError, TypeError):
                continue

            if cur_price <= 0:
                continue

            cur_volume_raw = out.get('acml_vol') or out.get('volume') or out.get('cntg_vol') or 0
            try:
                cur_volume = abs(float(str(cur_volume_raw).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
            except (ValueError, TypeError):
                cur_volume = 0.0

            self.buffer.update_tick(code, cur_price, cur_volume)
            await self.portfolio.update_current_price(code, cur_price)

            # 지표 산출 (인메모리 버퍼 기반 0.1ms 처리)
            candle_df = self.buffer.get_dataframe(code, limit=20)
            ind = TechnicalIndicators.get_latest_indicators(candle_df) if not candle_df.empty else {}

            # 퀀트 전략 출구 시그널 검증 (ATR 샹들리에 엑시트 + R-배수 분할 익절)
            action, reason = await self.strategy.check_sell_signal(
                code=code, buy_price=buy_price, current_price=cur_price,
                ind=ind, sell_stage=stage, highest_price=highest_price
            )

            if action == "SELL_ALL":
                print(f"🚨 [EXIT_SIGNAL/SELL_ALL] {name}({code}) -> {reason} ({qty}주 전량 매도)")
                await self._execute_emergency_sell(code, name, qty, cur_price, reason=reason)
            elif action == "SELL_PARTIAL":
                # 1차: 전체 물량의 50% 매도 (Stage 0 -> Stage 1)
                # 2차: 잔여 물량의 50% 매도 (Stage 1 -> Stage 2)
                # 3차: SELL_ALL로 처리되어 잔여 물량 전량 청산 (Stage 2 -> 청산)
                sell_ratio = 0.50
                sell_qty = max(1, int(qty * sell_ratio))
                next_stage = stage + 1
                print(f"🎯 [EXIT_SIGNAL/SELL_PARTIAL] {name}({code}) -> {reason} ({sell_qty}주 매도, 잔여 {qty - sell_qty}주 유지)")
                await self._execute_profit_sell(code, name, sell_qty, cur_price, next_stage, reason=reason)

    async def cleanup_unexecuted_orders(self):
        """
        미체결 주문(Unfilled Order) 자동 감시 및 3분 타임아웃 취소/재발주 안전장치
        - OrderTimeoutManager를 통한 3분(180초) 경과 미체결 주문 식별
        - 미체결 매수: 즉시 취소 -> 예수금 증거금 반환
        - 미체결 매도: 지정가 취소 후 즉시 긴급 시장가(03) CRITICAL 전량 청산 재발주
        """
        if hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
            await self.order_timeout_mgr.check_and_resolve_timeouts()

        if not hasattr(self.client, 'get_unexecuted_orders') or not hasattr(self.client, 'cancel_order'):
            return

        try:
            uncl_data = await self.client.get_unexecuted_orders(priority=RequestPriority.MEDIUM)
            if not uncl_data:
                return

            items = []
            if isinstance(uncl_data, dict):
                items = uncl_data.get('output') or uncl_data.get('output1') or uncl_data.get('list') or []
            elif isinstance(uncl_data, list):
                items = uncl_data

            if not items:
                return

            for order in items:
                if not isinstance(order, dict):
                    continue

                order_no = str(order.get('ord_no') or order.get('odno') or order.get('order_no') or '').strip()
                code = str(order.get('stk_cd') or order.get('pdno') or order.get('code') or '').strip()
                uncl_qty = int(float(str(order.get('uncl_qty') or order.get('otst_qty') or order.get('qty') or 0)))
                side = str(order.get('side') or order.get('sll_buy_tp') or 'BUY').upper()
                side_str = "SELL" if ("매도" in side or "SELL" in side or "01" in side) else "BUY"

                if order_no and uncl_qty > 0:
                    if hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr and order_no not in self.order_timeout_mgr.tracked_orders:
                        # 외부/미추적 미체결 주문 발견 시 트래커에 즉시 편입
                        name = self.watchlist.get(code, {}).get('name', code)
                        await self.order_timeout_mgr.track_order(order_no, code, name, side_str, uncl_qty, 0)
        except Exception as e:
            print(f"⚠️ [미체결 주문 정리 오류] {e}")

    async def _cancel_unexecuted_orders_for_stock(self, code: str) -> int:
        """
        특정 종목의 미체결 주문(Unfilled Orders) 전수 조회 및 선제적 취소(Cancel) 파이프라인
        - 800033(매도가능수량 부족) 에러 원천 방지
        - OrderTimeoutManager 및 키움 kt00007(계좌미체결내역) 듀얼 조회
        - 원주문번호(orig_ord_no)를 정확히 바인딩하여 kt10003 취소 주문 발송
        - KRX 주식 락 해제 및 예수금/매도가능수량 복구를 위한 비동기 대기(0.2s) 보장
        - 취소된 주문 건수 반환
        """
        clean_code = str(code).replace('A', '').strip()
        cancelled_count = 0
        cancelled_order_nos = set()

        # 1. 인메모리 OrderTimeoutManager 추적 중인 미체결 주문 취소
        if hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
            async with self.order_timeout_mgr._lock:
                for ord_no, info in list(self.order_timeout_mgr.tracked_orders.items()):
                    if info['code'] == clean_code and info['unfilled_qty'] > 0:
                        if ord_no not in cancelled_order_nos:
                            print(f"🛡️ [미체결 사전 취소/Tracker] {info['name']}({clean_code}) 주문번호 {ord_no} ({info['side']} {info['unfilled_qty']}주) 취소 발송...")
                            if self.client and hasattr(self.client, 'cancel_order'):
                                await self.client.cancel_order(
                                    order_no=ord_no,
                                    code=clean_code,
                                    qty=info['unfilled_qty'],
                                    priority=RequestPriority.CRITICAL
                                )
                            cancelled_order_nos.add(ord_no)
                            cancelled_count += 1
                            del self.order_timeout_mgr.tracked_orders[ord_no]

        # 2. 키움 REST API kt00007 (계좌미체결내역조회) 실시간 전수 조회 및 취소
        if self.client and hasattr(self.client, 'get_unexecuted_orders') and hasattr(self.client, 'cancel_order'):
            try:
                uncl_data = await self.client.get_unexecuted_orders(code=clean_code, priority=RequestPriority.CRITICAL)
                items = []
                if isinstance(uncl_data, dict):
                    items = uncl_data.get('output') or uncl_data.get('output1') or uncl_data.get('list') or []
                elif isinstance(uncl_data, list):
                    items = uncl_data

                if items:
                    for order in items:
                        if not isinstance(order, dict):
                            continue
                        ord_no = str(order.get('ord_no') or order.get('odno') or order.get('order_no') or '').strip()
                        ord_code = str(order.get('stk_cd') or order.get('pdno') or order.get('code') or '').replace('A', '').strip()
                        uncl_qty = int(float(str(order.get('uncl_qty') or order.get('otst_qty') or order.get('qty') or 0)))

                        if (not ord_code or ord_code == clean_code) and ord_no and uncl_qty > 0:
                            if ord_no not in cancelled_order_nos:
                                print(f"🛡️ [미체결 사전 취소/API] {clean_code} 주문번호 {ord_no} ({uncl_qty}주) kt10003 취소 발송...")
                                await self.client.cancel_order(
                                    order_no=ord_no,
                                    code=clean_code,
                                    qty=uncl_qty,
                                    priority=RequestPriority.CRITICAL
                                )
                                cancelled_order_nos.add(ord_no)
                                cancelled_count += 1
            except Exception as e:
                print(f"⚠️ [미체결 사전 취소 예외] {clean_code}: {e}")

        if cancelled_count > 0:
            print(f"✅ [미체결 해소 완료] {clean_code} 총 {cancelled_count}건의 미체결 주문 취소 완료 -> KRX 매도가능수량 락 해제 대기(0.2초)")
            await asyncio.sleep(0.2)  # KRX 매도가능수량 락 해제 비동기 대기
            if hasattr(self, '_sync_account_balance'):
                await self._sync_account_balance()

        return cancelled_count

    async def _execute_emergency_sell(self, code: str, name: str, qty: int, cur_price: float, reason: str):
        """CRITICAL 우선순위로 큐를 추월하는 긴급 스탑로스 주문 (미체결 사전 취소 파이프라인 연동)"""
        # [단계 1] 매도가능수량 0주(800033 에러) 방지를 위해 기존 미체결 주문 전수 선제 취소
        await self._cancel_unexecuted_orders_for_stock(code)

        # [단계 2] 호가창 조회 후 매수 2호가 또는 시장가 발주
        orderbook = await self.client.get_orderbook(code, priority=RequestPriority.CRITICAL) if (self.client and hasattr(self.client, 'get_orderbook')) else None
        sell_price = int(cur_price)
        order_type = "00"

        if orderbook:
            out = orderbook.get('output', orderbook)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]
            elif not isinstance(out, dict):
                out = {}
            # 매수 2호가 타겟팅으로 빠른 체결 유도
            bid2 = out.get('buy_fpr_bid2') or out.get('bid_price2')
            if bid2 and str(bid2).isdigit() and int(bid2) > 0:
                sell_price = int(bid2)
            elif out.get('buy_fpr_bid') or out.get('bid_price1'):
                bid1 = out.get('buy_fpr_bid') or out.get('bid_price1')
                if bid1 and str(bid1).isdigit() and int(bid1) > 0:
                    sell_price = int(bid1)
        else:
            order_type = "03"
            sell_price = 0

        # [단계 3] CRITICAL 우선순위로 긴급 매도 발주
        res = await self.client.send_order(code, qty, sell_price, order_type=order_type, side="SELL", priority=RequestPriority.CRITICAL)
        rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')
        msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or ''
        msg_cd = (res or {}).get('msg_cd') or (res or {}).get('return_code') or ''

        # [단계 4] 만약 800033 에러가 여전히 반환될 경우의 Fail-Safe 재시도
        if res and str(rt_cd) != '0' and ('800033' in str(msg) or '800033' in str(msg_cd) or '매도가능수량' in str(msg)):
            print(f"⚠️ [800033 긴급 복구] {name}({code}) 매도가능수량 락 재감지 -> 추가 미체결 취소 및 재발주 시도...")
            await asyncio.sleep(0.3)
            await self._cancel_unexecuted_orders_for_stock(code)
            res = await self.client.send_order(code, qty, sell_price, order_type=order_type, side="SELL", priority=RequestPriority.CRITICAL)
            rt_cd = (res or {}).get('rt_cd') if (res or {}).get('rt_cd') is not None else (res or {}).get('return_code')

        if res and str(rt_cd) == '0':
            ord_no = str((res or {}).get('ord_no') or (res or {}).get('odno') or (res or {}).get('order_no') or '').strip()
            if ord_no and hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
                await self.order_timeout_mgr.track_order(ord_no, code, name, "SELL", qty, sell_price, order_type=order_type)

            pos = await self.portfolio.remove_position(code, cur_price if sell_price == 0 else sell_price)
            actual_exit_price = cur_price if sell_price == 0 else sell_price
            await self.db.log_order(code, name, "SELL", qty, int(actual_exit_price))
            await self.db.log_message("WARNING", f"🚨 [긴급 매도 성공] {name}({code}) {qty}주 @ {int(actual_exit_price):,}원 ({reason})")
            pnl = (actual_exit_price - pos['buy_price']) * qty if pos else None
            yield_rt = (actual_exit_price - pos['buy_price']) / pos['buy_price'] * 100.0 if pos and pos['buy_price'] > 0 else None
            self.notifier.notify_order_filled("SELL", name, code, qty, actual_exit_price, reason=reason, pnl=pnl, yield_rate=yield_rt)
            await self._sync_account_balance()
        else:
            err_msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"긴급 매도 실패: {name}({code}) - {err_msg}")

    async def _execute_profit_sell(self, code: str, name: str, qty: int, cur_price: float, next_stage: int, reason: str):
        """HIGH 우선순위로 스마트 호가(매수 1호가) 분할 익절 주문 (미체결 사전 취소 연동)"""
        # [단계 1] 미체결 잔량 선제 정리
        await self._cancel_unexecuted_orders_for_stock(code)

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
            ord_no = str((res or {}).get('ord_no') or (res or {}).get('odno') or (res or {}).get('order_no') or '').strip()
            if ord_no and hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
                await self.order_timeout_mgr.track_order(ord_no, code, name, "SELL", qty, sell_price, order_type="00")

            snap_pos = self.portfolio.positions.get(code, {})
            buy_p = snap_pos.get('buy_price', sell_price)
            await self.portfolio.update_partial_sell(code, qty, sell_price, next_stage)
            await self.db.log_order(code, name, "SELL", qty, sell_price)
            await self.db.log_message("INFO", f"🎯 [분할 익절 성공] {name}({code}) {qty}주 @ {sell_price:,}원 ({reason})")
            pnl = (sell_price - buy_p) * qty
            yield_rt = (sell_price - buy_p) / buy_p * 100.0 if buy_p > 0 else 0.0
            self.notifier.notify_order_filled("SELL", name, code, qty, sell_price, reason=reason, pnl=pnl, yield_rate=yield_rt)
            await self._sync_account_balance()
        else:
            msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"분할 익절 실패: {name}({code}) - {msg}")

    async def _evaluate_buy_condition(self, code: str, cur_price: float, cur_volume: float, raw_data: Optional[Dict[str, Any]] = None):
        """
        실시간 피보나치 눌림목 및 ATR 변동성 돌파 매수 조건 평가 함수
        - OnReceiveRealData 이벤트 수신 시 즉시 호출되어 매수 타점 도달 여부 판정
        - 5대 퀀트 알파 필터(체결강도, 호가불균형, VWAP, 스퀴즈모멘텀, 거래대금) 연동
        """
        if self.daily_circuit_breaker:
            return
        if self.mdd_shutdown:
            return
        if not self.market_filter_passed:
            return

        now = datetime.now()
        skip_time_filter = getattr(self, 'is_demo', False) or getattr(self, 'is_test', False)

        info = self.watchlist.get(code)
        if not info:
            return

        name = info.get('name', code)
        fib_382 = info.get('fib_382', 0)
        fib_500 = info.get('fib_500', 0)
        fib_618 = info.get('fib_618', 0)
        period_high = info.get('period_high', 0)
        period_low = info.get('period_low', 0)
        open_price = float(info.get('open_price') or info.get('open') or cur_price)
        current_deposit = self.portfolio.current_capital

        # 1. 포지션 한도 및 중복 매수 체크
        if not await self.portfolio.can_buy(code):
            return

        # 2. 지표 산출 (인메모리 버퍼 기반 + 결측치 안전 보정)
        candle_df = self.buffer.get_dataframe(code, limit=20)
        ind = TechnicalIndicators.get_latest_indicators(candle_df) if not candle_df.empty else {}
        if ind is None:
            ind = {}

        # 3. 실시간 시세 및 알파 피처 결합
        ind['avg_vol'] = info.get('avg_volume', 0)
        ind['high10'] = period_high if period_high > 0 else cur_price
        ind['period_high'] = period_high
        ind['period_low'] = period_low
        ind['fib_382'] = fib_382
        ind['fib_500'] = fib_500
        ind['fib_618'] = fib_618
        ind['open'] = open_price  # 당일 실제 시가 매핑 (변동성 돌파 정상 판정)
        ind['fib_rebound'] = (fib_618 <= cur_price <= fib_382) if (fib_618 > 0 and fib_382 > 0) else False
        ind['skip_time_filter'] = skip_time_filter

        # 누적 거래량 / 체결강도 추출 및 주입
        if raw_data and isinstance(raw_data, dict):
            raw_dict = raw_data.get('raw', raw_data) if isinstance(raw_data.get('raw'), dict) else raw_data
            acml_vol = abs(float(str(raw_dict.get('acml_vol') or raw_dict.get('volume') or cur_volume).replace(',', '').strip() or 0))
            vol_pwr = abs(float(str(raw_dict.get('volume_power') or raw_dict.get('chg_pwr') or raw_dict.get('cntg_pwr') or 0).replace(',', '').strip() or 0))
            if acml_vol > 0:
                ind['acml_vol'] = acml_vol
            if vol_pwr > 0:
                ind['volume_power'] = vol_pwr

        if 'acml_vol' not in ind or ind['acml_vol'] <= 0:
            ind['acml_vol'] = cur_volume

        # 4. 퀀트 전략 매수 시그널 검증 (5대 고승률 퀀트 알파 필터 통합)
        buy_signal, reason = await self.strategy.check_buy_signal(
            code=code, current_price=cur_price, current_volume=cur_volume, ind=ind
        )

        diff_pct = ((cur_price - fib_382) / fib_382 * 100.0) if fib_382 > 0 else 0.0

        if buy_signal:
            atr14 = ind.get('atr14', 0)
            # 프랙셔널 켈리 공식 및 1.5% Risk 한도 기반 최적 주문 수량 계산
            order_qty = await self.portfolio.get_order_qty(cur_price, atr=atr14)

            if order_qty <= 0:
                # 소액 계좌 최소 1주 안전 가드 (가용 예수금 >= 1주 가격)
                if current_deposit >= cur_price:
                    order_qty = 1
                else:
                    print(f"⚠️ [매수 실패/자금부족] {name}({code}) - 타점 도달({reason})했으나 예수금 부족 (현재 예수금: {int(current_deposit):,}원 / 1주 가격: {int(cur_price):,}원)")
                    await self.db.log_message("WARNING", f"매수 자금 부족: {name}({code}) 현재가 {int(cur_price):,}원 > 예수금 {int(current_deposit):,}원")
                    return

            # [2차 방어] 실시간 매수 시 잔고 초과 최종 체크 (매수가 × 수량 > D+2 예수금 → 매수 스킵)
            total_buy_amount = cur_price * order_qty
            if total_buy_amount > current_deposit:
                # 수량 축소 시도 (가용 예수금 내 구매 가능한 최대 수량)
                max_possible_qty = int(current_deposit // cur_price)
                if max_possible_qty > 0:
                    order_qty = max_possible_qty
                    total_buy_amount = cur_price * order_qty
                    print(f"🔧 [수량 자동 보정] {name}({code}) 예수금 범위 내 수량 조정: {order_qty}주 (총 {int(total_buy_amount):,}원)")
                else:
                    print(f"⚠️ [잔고 부족으로 매수 스킵] {name}({code}) - 예상매수금({int(total_buy_amount):,}원) > 예수금({int(current_deposit):,}원)")
                    await self.db.log_message("WARNING", f"잔고 부족으로 매수 스킵: {name}({code}) 매수금 {int(total_buy_amount):,}원 > 예수금 {int(current_deposit):,}원")
                    return

            print(f"🔥 [BUY_SIGNAL] {name}({code}) -> {reason} (현재가: {cur_price:,.0f}원, 발주수량: {order_qty}주)")
            await self._execute_smart_buy(code, name, order_qty, cur_price, reason=reason)
        else:
            # 매수 대기 상세 이유 상태 가시화 출력 (전략 사유 우선 표출)
            if reason:
                status_desc = f"전략 필터 ({reason})"
            elif cur_price > fib_382 and fib_382 > 0:
                status_desc = f"목표 타점(Fib 38.2% {int(fib_382):,}원) 미도달 ➔ 대기 중 (괴리율: {diff_pct:+.2f}%)"
            elif cur_price < fib_618 and fib_618 > 0:
                status_desc = f"피보나치 61.8% 지지선({int(fib_618):,}원) 하회 ➔ 과대낙폭 관망"
            else:
                status_desc = f"타점 대기 중 (현재가: {int(cur_price):,}원 / Fib 38.2%: {int(fib_382):,}원)"

            # 실시간 감시 로그 쓰로틀링 (종목당 5초에 1회 또는 괴리율 0.5%p 이상 변동 시에만 콘솔/DB/웹 출력)
            now_ts = time.time()
            last_time = self._last_watch_log_time.get(code, 0.0)
            last_diff = self._last_watch_diff_pct.get(code, -999.0)
            time_elapsed = now_ts - last_time
            diff_changed = abs(diff_pct - last_diff) >= 0.5

            if time_elapsed >= 5.0 or diff_changed:
                self._last_watch_log_time[code] = now_ts
                self._last_watch_diff_pct[code] = diff_pct
                print(f"⏱ [실시간 감시] {name}({code}) - 현재가: {int(cur_price):,}원 / {status_desc}")
                await self.db.log_message("WATCH", f"[실시간 감시] {name}({code}) - 현재가: {int(cur_price):,}원 / {status_desc}")

    async def _realtime_data_stream_worker(self):
        """
        키움 실시간 틱 데이터 비동기 스트림 워커
        - 등록된 감시 종목들을 지속 순회하며 실시간 시세를 수신하여 OnReceiveRealData 이벤트로 디스패치
        """
        print(f"📡 [RealtimeStream] 실시간 틱 데이터 스트림 워커 가동 시작")
        while self.is_running and not self.is_paused and not self.is_shutdown:
            try:
                watch_codes = list(self.watchlist.keys())
                if not watch_codes:
                    await asyncio.sleep(1.0)
                    continue

                for code in watch_codes:
                    if not self.is_running or self.is_paused or self.is_shutdown:
                        break

                    # API Rate Limiter 준수 미세 분산 딜레이 (초당 약 3.3건)
                    await asyncio.sleep(0.3)

                    price_data = await self.client.get_price(code, priority=RequestPriority.LOW)
                    if not price_data:
                        continue

                    out = price_data.get('output', price_data)
                    if isinstance(out, list) and len(out) > 0:
                        out = out[0]
                    elif not isinstance(out, dict):
                        out = {}

                    raw_p = out.get('prpr') or out.get('current_price') or out.get('stck_prpr') or out.get('cur_prc') or out.get('clpr') or 0
                    raw_v = out.get('acml_vol') or out.get('volume') or out.get('cntg_vol') or 0
                    raw_o = out.get('oprn') or out.get('stck_oprc') or out.get('open_price') or out.get('open_pric') or out.get('oprc') or 0

                    try:
                        cur_p = abs(float(str(raw_p).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
                    except (ValueError, TypeError):
                        cur_p = 0.0

                    try:
                        cur_v = abs(float(str(raw_v).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
                    except (ValueError, TypeError):
                        cur_v = 0.0

                    try:
                        cur_o = abs(float(str(raw_o).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
                    except (ValueError, TypeError):
                        cur_o = 0.0

                    if cur_p > 0:
                        await self.OnReceiveRealData(code, "주식체결", {
                            "current_price": cur_p,
                            "volume": cur_v,
                            "open_price": cur_o,
                            "raw": out
                        })

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [RealtimeStream Error] {e}")
                await asyncio.sleep(1.0)

    async def monitor_watchlist_and_enter(self):
        """
        감시 종목 주기 순회 매수 기회 포착
        - 실시간 스트림 워커와 함께 주기적으로 OnReceiveRealData를 트리거하여 2중 안전망 확보
        """
        if self.mdd_shutdown or not self.market_filter_passed:
            return

        for code in list(self.watchlist.keys()):
            await asyncio.sleep(0.05)
            price_data = await self.client.get_price(code, priority=RequestPriority.LOW)
            if not price_data:
                continue

            out = price_data.get('output', price_data)
            if isinstance(out, list) and len(out) > 0:
                out = out[0]
            elif not isinstance(out, dict):
                out = {}

            raw_p = out.get('prpr') or out.get('current_price') or out.get('stck_prpr') or out.get('cur_prc') or out.get('clpr') or 0
            raw_v = out.get('acml_vol') or out.get('volume') or out.get('cntg_vol') or 0
            raw_o = out.get('oprn') or out.get('stck_oprc') or out.get('open_price') or out.get('open_pric') or out.get('oprc') or 0

            try:
                cur_price = abs(float(str(raw_p).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
            except (ValueError, TypeError):
                continue

            if cur_price <= 0:
                continue

            try:
                cur_volume = abs(float(str(raw_v).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
            except (ValueError, TypeError):
                cur_volume = 0.0

            try:
                cur_open = abs(float(str(raw_o).replace(',', '').replace('+', '').replace('-', '').strip() or 0))
            except (ValueError, TypeError):
                cur_open = 0.0

            # OnReceiveRealData 이벤트로 통일 전달
            await self.OnReceiveRealData(code, "주식체결", {
                "current_price": cur_price,
                "volume": cur_volume,
                "open_price": cur_open,
                "raw": out
            })

    async def _execute_smart_buy(self, code: str, name: str, qty: int, cur_price: float, reason: str = "ATR돌파_스퀴즈모멘텀"):
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
            ord_no = str((res or {}).get('ord_no') or (res or {}).get('odno') or (res or {}).get('order_no') or '').strip()
            if ord_no and hasattr(self, 'order_timeout_mgr') and self.order_timeout_mgr:
                await self.order_timeout_mgr.track_order(ord_no, code, name, "BUY", qty, buy_price, order_type="00")
            await self.portfolio.add_position(code, name, qty, buy_price)
            await self.db.log_order(code, name, "BUY", qty, buy_price)
            await self.db.log_message("INFO", f"🔥 [매수 체결 완료] {name}({code}) {qty}주 @ {buy_price:,}원 ({reason})")
            self.notifier.notify_order_filled("BUY", name, code, qty, buy_price, reason=reason)
            await self._sync_account_balance()
        else:
            msg = (res or {}).get('msg1') or (res or {}).get('return_msg') or '주문 거절'
            await self.db.log_message("ERROR", f"매수 주문 실패: {name}({code}) - {msg}")

    async def wait_until_next_market_open(self, wake_up_hour: int = 8, wake_up_minute: int = 50):
        """
        장 마감 또는 수동 일시정지 후 다음 영업일 아침(기본 08:50 KST)까지 비동기 휴면 대기
        - 08:50 도달 시 수동 일시정지(is_paused) 상태를 자동으로 해제하고 봇을 '실행(RUNNING)' 상태로 복구
        """
        now = datetime.now()
        next_open = now.replace(hour=wake_up_hour, minute=wake_up_minute, second=0, microsecond=0)
        if now >= next_open:
            next_open += timedelta(days=1)

        # 주말(토/일) 및 한국 공휴일/휴장일 건너뛰기
        while self.is_korean_market_holiday(next_open):
            next_open += timedelta(days=1)

        total_sleep_sec = (next_open - now).total_seconds()
        hours, remainder = divmod(int(total_sleep_sec), 3600)
        minutes, _ = divmod(remainder, 60)
        print(f"💤 [휴면 모드] 다음 거래일({next_open.strftime('%Y-%m-%d %H:%M')})까지 약 {hours}시간 {minutes}분 대기합니다...")

        while not self.is_shutdown and datetime.now() < next_open:
            # 대기 도중 사용자가 수동으로 봇 시작(Resume)을 누른 경우 (정규장 시간 내)
            if self.is_running and not self.is_paused:
                now_curr = datetime.now()
                if (9 <= now_curr.hour < 15) or (now_curr.hour == 15 and now_curr.minute < 30):
                    print("⚡ [수동 재개] 사용자에 의한 봇 시작 감지 -> 휴면 루프 즉시 탈출")
                    break
            await asyncio.sleep(min(30.0, max(1.0, (next_open - datetime.now()).total_seconds())))

        # 익일 아침 08:50 도달 시: 수동 일시정지 자동 해제 및 봇 RUNNING 상태 자동 전환
        if not self.is_shutdown and datetime.now() >= next_open:
            if self.is_paused or not self.is_running:
                self.is_paused = False
                self.is_running = True
                self.mdd_shutdown = False
                self.daily_circuit_breaker = False
                self.daily_start_capital = 0.0
                self.market_filter_passed = True
                wake_msg = f"🌅 [자동 재시작 스케줄러] 익일 영업일 아침({next_open.strftime('%H:%M')}) 도달: 수동 일시정지 및 서킷브레이커를 해제하고 봇을 '실행(RUNNING)' 상태로 자동 전환합니다."
                print(wake_msg)
                if self.notifier:
                    self.notifier.send_message(f"🌅 [Kiwoom Quant Bot] 익일 장전 자동 웨이크업: 봇 '실행(RUNNING)' 상태로 자동 재개되었습니다.")
                if self.db:
                    await self.db.log_message("SYSTEM", "익일 장전 자동 웨이크업: 수동 일시정지 해제 및 봇 '실행(RUNNING)' 상태 자동 복구")

    async def trading_loop(self):
        """정규 거래 시간(09:00 ~ 15:30) 내의 실시간 트레이딩 비동기 주기 루프"""
        loop_count = 0
        print(f"🔥 [TradingLoop] 정규장 실시간 매매 루프 가동 시작 ({datetime.now().strftime('%H:%M:%S')})")

        # 실시간 틱 데이터 비동기 스트림 워커 시작
        if not self.realtime_stream_task or self.realtime_stream_task.done():
            self.realtime_stream_task = asyncio.create_task(self._realtime_data_stream_worker())

        try:
            while self.is_running and not self.is_paused and not self.is_shutdown:
                # 실시간 스트림 워커 생존 감시 및 자동 재가동 (Watchdog)
                if self.realtime_stream_task and self.realtime_stream_task.done():
                    exc = self.realtime_stream_task.exception() if not self.realtime_stream_task.cancelled() else None
                    if exc:
                        print(f"⚠️ [TradingLoop Watchdog] 실시간 스트림 워커 예외({exc}) 감지 -> 자동 재시작")
                    self.realtime_stream_task = asyncio.create_task(self._realtime_data_stream_worker())

                try:
                    loop_count += 1
                    now = datetime.now()
                    now_time = now.time()

                    # 1. 수동 주문 큐 처리 (매 루프마다)
                    await self.process_manual_orders()

                    # 2. 시장 필터, 계좌 싱크 및 미체결 방어 (10초 주기)
                    if loop_count % 5 == 0:
                        await self.check_market_filter()
                        await self.cleanup_unexecuted_orders()
                        await self._sync_account_balance()

                    # 3. 감시 종목 갱신 (60초 주기)
                    if loop_count % 30 == 1:
                        await self.update_watchlist()

                    # 4. 포지션 감시 및 출구 전략 (매 2초마다 최우선 감시)
                    await self.monitor_positions_and_exit()

                    # 5. 신규 매수 기회 탐색 (매 2초마다 2중 보완)
                    await self.monitor_watchlist_and_enter()

                    # 6. 장 마감(15:30) 도달 시 당일 루프 종료 후 정산
                    if now_time.hour >= 15 and now_time.minute >= 30:
                        print("🏁 [장 마감] 당일 정규 거래 시간이 종료되었습니다.")
                        snap = await self.portfolio.get_snapshot()
                        self.notifier.notify_daily_settlement(snap)
                        await self.buffer.flush_all()
                        await self.db.log_message("SYSTEM", "당일 정규장 마감. 일일 결산 알림 발송 완료.")
                        break

                    await asyncio.sleep(2.0)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"❌ [TradingLoop Error] {e}")
                    await self.db.log_message("ERROR", f"트레이딩 루프 오류: {e}")
                    await asyncio.sleep(2.0)
        finally:
            if self.realtime_stream_task and not self.realtime_stream_task.done():
                self.realtime_stream_task.cancel()
                try:
                    await self.realtime_stream_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def run_daemon(self):
        """24시간 365일 무중단 데몬 메인 오케스트레이터"""
        await self.initialize()

        while not self.is_shutdown:
            try:
                now = datetime.now()
                now_time = now.time()

                # 1. 주말 또는 공휴일/휴장일인 경우 다음 영업일 08:50까지 휴면
                if self.is_korean_market_holiday(now):
                    print(f"🏖️ [휴일/휴장일] 오늘은 주말 또는 공휴일입니다. 다음 영업일 아침 08:50까지 대기합니다.")
                    await self.wait_until_next_market_open(8, 50)
                    continue

                # 2. 사용자에 의해 당일 수동 일시정지된 경우 -> 익일 영업일 08:50까지 대기 (익일 아침 자동 재개)
                if self.is_paused or not self.is_running:
                    print(f"⏸️ [수동 일시정지 상태] 봇이 일시정지되어 있습니다. 익일 영업일 아침 08:50에 자동으로 '실행(RUNNING)' 상태로 복구됩니다.")
                    await self.wait_until_next_market_open(8, 50)
                    continue

                # 3. 장 시작 전(08:50 이전): 08:50까지 대기
                if now_time.hour < 8 or (now_time.hour == 8 and now_time.minute < 50):
                    target_0850 = now.replace(hour=8, minute=50, second=0, microsecond=0)
                    wait_sec = (target_0850 - now).total_seconds()
                    print(f"⏳ [개장 전 대기] 아침 08:50까지 대기합니다 ({int(wait_sec//60)}분 남음)...")
                    while not self.is_shutdown and datetime.now() < target_0850:
                        if self.is_paused:
                            break
                        await asyncio.sleep(min(30.0, max(1.0, (target_0850 - datetime.now()).total_seconds())))
                    continue

                # 4. 장 시작 준비(08:50 ~ 09:00): 계좌 잔고 동기화 및 당일 감시 유니버스 스캔
                if now_time.hour == 8 and now_time.minute >= 50:
                    print("🌅 [08:50 장전 준비] 계좌 잔고 동기화 및 당일 감시 유니버스 사전 분석...")
                    self.mdd_shutdown = False
                    self.market_filter_passed = True
                    await self._sync_account_balance()
                    await self.update_watchlist()
                    target_0900 = now.replace(hour=9, minute=0, second=0, microsecond=0)
                    while not self.is_shutdown and not self.is_paused and datetime.now() < target_0900:
                        await asyncio.sleep(1.0)
                    continue

                # 5. 정규 거래 시간(09:00 ~ 15:30): 실시간 트레이딩 루프 실행
                if (now_time.hour == 9 and now_time.minute >= 0) or (9 < now_time.hour < 15) or (now_time.hour == 15 and now_time.minute < 30):
                    await self.trading_loop()
                    continue

                # 6. 장 마감 후(15:30 이후): 다음 영업일 08:50까지 안전 휴면 대기
                if now_time.hour > 15 or (now_time.hour == 15 and now_time.minute >= 30):
                    await self.wait_until_next_market_open(8, 50)
                    continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Daemon Loop Error] {e}")
                await asyncio.sleep(5.0)

    async def shutdown(self):
        """시스템 종료 및 자원 정리 (Graceful Shutdown)"""
        self.is_shutdown = True
        self.is_running = False
        self.is_paused = False
        print("🛑 [AsyncTradingBot] 데몬 종료 및 자원 반환 중...")
        if self.realtime_stream_task and not self.realtime_stream_task.done():
            self.realtime_stream_task.cancel()
        try:
            await self.buffer.stop()
        except Exception as e:
            print(f"⚠️ [Shutdown] 버퍼 정지 오류: {e}")
        try:
            await self.notifier.stop()
        except Exception as e:
            print(f"⚠️ [Shutdown] 알림 워커 정지 오류: {e}")
        await self.client.stop()
        await self.db.close_pool()
        print("✅ [AsyncTradingBot] 정상 종료 완료")
        await self.client.stop()
        await self.db.close_pool()
        print("✅ [AsyncTradingBot] 정상 종료 완료")

async def main():
    parser = argparse.ArgumentParser(description="키움 OpenAPI 비동기 퀀트 트레이딩 데몬 (24/365 무한 루프)")
    parser.add_argument('--real', action='store_true', default=True, help='실전투자 모드 (기본값: True)')
    parser.add_argument('--mock', action='store_true', default=False, help='모의투자 모드 강제 실행')
    parser.add_argument('--capital', type=float, default=10_000_000, help='초기 운용 자본금')
    args = parser.parse_args()

    is_demo = True if args.mock else (not args.real)
    bot = AsyncTradingBot(is_demo=is_demo, initial_capital=args.capital)
    try:
        await bot.run_daemon()
    except KeyboardInterrupt:
        print("\n사용자에 의해 데몬이 중단되었습니다.")
    finally:
        await bot.shutdown()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
