"""
동시성 안전한 비동기 포트폴리오 관리자 (Async Portfolio Manager)
- asyncio.Lock() 기반 세밀한 자산/포지션 상태 동기화
- 프랙셔널 켈리 공식(Fractional Kelly Criterion) 기반 동적 자산 배분
- 종목별 최고가 추적 및 실시간 평가 손익(PnL) 산출
- 웹소켓 브로드캐스트용 스냅샷 직렬화 지원
"""
import asyncio
import collections
from typing import Dict, Any, Optional, List
import numpy as np


class AsyncPortfolioManager:
    """
    동시성 안전한 비동기 포트폴리오 및 켈리 자산 배분 관리자
    """
    def __init__(self, initial_capital: float = 10_000_000, max_stocks: int = 5,
                 kelly_fraction: float = 0.4):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital  # 실제 주문 가능 현금 (D+2 예수금)
        self.total_asset = initial_capital      # 계좌 총 평가 자산 (원금 + 주식평가금)
        self.max_stocks = max_stocks
        self.weight_per_stock = 1.0 / max_stocks
        self.kelly_fraction = kelly_fraction  # 40% Fractional Kelly
        self.positions: Dict[str, Dict[str, Any]] = {}
        # 최근 50회 매매 수익률 이력 (예: +0.05, -0.02)
        self.trade_returns: collections.deque = collections.deque(maxlen=50)
        self.daily_realized_pnl: float = 0.0  # 당일 실현 손익 누적 (원)
        self._lock = asyncio.Lock()

    async def sync_capital(self, available_cash: float, total_asset: Optional[float] = None):
        """주문가능 예수금(D+2) 및 총 평가자산 독립 동기화
        - total_asset(총자산)은 항상 available_cash(D+2 예수금) 이상이어야 함
        - 만약 total_asset < available_cash이면 총자산은 최소 available_cash로 보정
        """
        async with self._lock:
            self.current_capital = float(available_cash)
            if total_asset is not None and float(total_asset) > 0:
                new_total = float(total_asset)
                # 안전 가드: total_asset이 available_cash보다 작으면 방어 보정
                if new_total < float(available_cash):
                    print(f"⚠️ [Portfolio sync_capital] 총자산({int(new_total):,}원) < D+2예수금({int(available_cash):,}원) → 총자산 보정: {int(available_cash):,}원")
                    new_total = float(available_cash)
                self.total_asset = new_total
            elif self.total_asset == 0.0:
                self.total_asset = float(available_cash)
                print(f"⚠️ [Portfolio sync_capital] total_asset=0 초기화 → D+2예수금으로 대체: {int(available_cash):,}원")

    async def record_closed_trade(self, return_pct: float):
        """청산 완료된 매매의 수익률 기록 (켈리 계산용)"""
        async with self._lock:
            self.trade_returns.append(float(return_pct))

    def get_kelly_allocation_fraction(self, min_trades: int = 10,
                                      min_alloc: float = 0.05, max_alloc: float = 0.25) -> float:
        """
        최근 매매 이력 기반 프랙셔널 켈리 최적 투자 비중 f* 산출
        f* = (p * b - (1 - p)) / b
        """
        returns = list(self.trade_returns)
        if len(returns) < min_trades:
            return self.weight_per_stock  # 초기 기본 비중 (20%)

        wins = [r for r in returns if r > 0]
        losses = [abs(r) for r in returns if r < 0]

        if not wins or not losses:
            return self.weight_per_stock

        win_rate = len(wins) / len(returns)      # p
        avg_win = float(np.mean(wins))
        avg_loss = float(np.mean(losses))

        if avg_loss == 0:
            return max_alloc

        payoff_ratio = avg_win / avg_loss       # b
        p = win_rate
        q = 1.0 - win_rate

        # 켈리 공식
        full_kelly = (p * payoff_ratio - q) / payoff_ratio
        if full_kelly <= 0:
            return min_alloc  # 우위(Edge) 없을 때 최소 비중으로 방어

        adjusted_kelly = full_kelly * self.kelly_fraction
        return float(np.clip(adjusted_kelly, min_alloc, max_alloc))

    async def sync_positions(self, account_data: Any):
        """키움 계좌 잔고 API 응답(kt00004/kt00018/kt00005/OPW00018) 기반 포지션 동기화 (모의/실전/다중 스키마 100% 대응)"""
        async with self._lock:
            if not account_data:
                return

            # 1. 딕셔너리의 모든 리스트 키들을 검사하여 종목 리스트가 있는 키를 지능적으로 탐색
            raw_list = []
            code_keys = [
                'stk_cd', 'pdno', 'code', 'expcode', 'mksc_shrn_iscd', 'shcode',
                'jongmok_code', 'item_code', 'stck_shrn_iscd', 'item_cd', 'prdt_cd',
                'stk_code', 'iscd', 'jong_cd', 'stck_cd', 'stk_no', 'isu_cd',
                '종목코드', '종목번호', '단축코드', '상품번호', '종목'
            ]
            name_keys = [
                'stk_nm', 'name', 'prdt_name', 'jongmok_name', 'hts_kor_isnm',
                'item_name', 'stck_nm', 'isu_nm', 'kor_isnm', 'prdt_nm',
                '종목명', '상품명', '한글종목명', '종목'
            ]
            qty_keys = [
                'hldg_qty', 'hold_qty', 'ord_psbl_qty', 'bal_qty', 'rmnd_qty',
                'tot_hldg_qty', 'hld_qty', 'jango_qty', 'now_qty', 'stck_qty',
                'ccls_qty_sum', 'qty', 'ccls_qty',
                '보유수량', '잔고수량', '체결수량', '주문가능수량', '수량'
            ]
            buy_p_keys = [
                'pchs_avg_pric', 'buy_price', 'pchs_price', 'avg_buy_price',
                'buy_uv', 'pchs_unit_amt', 'pchs_avg_amt', 'avg_pchs_price',
                'pchs_prc', 'buy_prc', 'ccls_avg_pric', 'ccls_avg_prc', 'ccls_prc',
                'ccls_uv', 'pchs_uv', 'ord_uv', 'pur_prc', 'pur_avg_prc',
                'pur_price', 'pur_avg_price', 'thst_buy_uv', 'bf_buy_uv',
                'avg_prc', 'avg_price', 'avg_cost', 'unit_price', 'unit_cost',
                'cost_price', 'cost_basis',
                '매입단가', '매입평균가', '평균매입가', '매입가', '평균단가',
                '매수가', '매수단가', '체결평균가', '체결가', '체결단가', '취득단가', '취득가', '평단가', '평단'
            ]
            pchs_amt_keys = [
                'pchs_amt', 'buy_amt', 'pchs_amt_smtl_amt', 'tot_pchs_amt', 'tot_buy_amt',
                'thst_pchs_amt', 'bf_pchs_amt', 'ccls_amt', 'pur_amt', 'hldg_amt', 'jango_amt',
                '매입금액', '총매입금액', '매수금액', '체결금액', '취득금액', '매입금'
            ]
            cur_p_keys = [
                'prpr', 'current_price', 'stck_prpr', 'cur_prc', 'clpr',
                'price', 'now_prc', 'stck_clpr',
                '현재가', '종가', '현재가격', '현재시세', '현재가액'
            ]
            evlu_amt_keys = [
                'evlu_amt', 'eval_amt', 'evlu_amt_smtl_amt', 'tot_evlu_amt', 'stck_evlu_amt',
                '평가금액', '총평가금액', '평가금'
            ]
            pnl_keys = [
                'evlu_pfls_amt', 'pnl', 'evlt_pfls_amt', 'tot_evlu_pfls_amt', 'pfls_amt',
                '평가손익', '손익금액', '평가손익금액'
            ]
            rt_keys = [
                'evlu_pfls_rt', 'yield_rate', 'evlt_pfls_rt', 'tot_pnl_rt', 'pnl_rt', 'pfls_rt',
                '수익률', '평가손익률', '손익률'
            ]

            def extract_valid_code(d: Dict[str, Any]) -> str:
                for k in code_keys:
                    val = d.get(k)
                    if val is not None and str(val).strip():
                        raw_c = str(val).strip()
                        # ISIN 표준코드 (예: KR7090460005) 대응: 3번째부터 6자리 추출
                        if raw_c.startswith('KR7') and len(raw_c) >= 9:
                            cand = raw_c[3:9]
                            if cand.isdigit():
                                return cand
                        # 'A' 접두사 및 '_AL', '_NX' 등 거래소 구분자 제거
                        c = raw_c.replace('A', '').split('_')[0].strip()
                        if len(c) == 6 and c.isdigit():
                            return c
                        elif len(c) > 6 and c[-6:].isdigit():
                            return c[-6:]
                        elif 1 <= len(c) < 6 and c.isdigit():
                            return c.zfill(6)
                        elif len(c) >= 3 and not c.startswith('KR'):
                            return c
                return ''

            def is_holding_item(d: Any) -> bool:
                if not isinstance(d, dict):
                    return False
                c = extract_valid_code(d)
                if not c:
                    return False
                # 수량 또는 종목명이 존재하는지 확인
                for qk in qty_keys:
                    qv = d.get(qk)
                    if qv is not None:
                        q_clean = str(qv).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            if float(q_clean) > 0:
                                return True
                        except ValueError:
                            pass
                return any(k in d and bool(str(d[k]).strip()) for k in name_keys)

            def extract_candidate_lists(data: Any, depth: int = 0) -> List[List[Dict[str, Any]]]:
                if depth > 4:
                    return []
                candidates = []
                if isinstance(data, list) and data:
                    valid_items = [x for x in data if isinstance(x, dict) and is_holding_item(x)]
                    if valid_items:
                        candidates.append(valid_items)
                    for item in data:
                        if isinstance(item, dict):
                            candidates.extend(extract_candidate_lists(item, depth + 1))
                    return candidates

                if not isinstance(data, dict):
                    return []

                priority_keys = [
                    'output2', 'Output2', 'output_2', 'acnt_dtl_list', 'holdings',
                    'stk_list', 'item_list', 'list', 'data', 'grid', 'table',
                    'rows', 'items', 'output', 'Output', 'stocks', 'positions',
                    '종목리스트', '잔고리스트'
                ]
                for k in priority_keys:
                    v = data.get(k)
                    if isinstance(v, list) and v:
                        valid_items = [x for x in v if isinstance(x, dict) and is_holding_item(x)]
                        if valid_items:
                            candidates.append(valid_items)

                for k, v in data.items():
                    if isinstance(v, dict):
                        candidates.extend(extract_candidate_lists(v, depth + 1))
                    elif isinstance(v, list) and v:
                        valid_items = [x for x in v if isinstance(x, dict) and is_holding_item(x)]
                        if valid_items and valid_items not in candidates:
                            candidates.append(valid_items)
                return candidates

            candidate_lists = extract_candidate_lists(account_data)
            if candidate_lists:
                # 유효한 보유종목 개수가 가장 많은 리스트를 최우선 선택
                candidate_lists.sort(key=lambda lst: sum(1 for x in lst if is_holding_item(x)), reverse=True)
                raw_list = candidate_lists[0]

            prev_positions = self.positions.copy()
            new_positions = {}
            for item in raw_list:
                if not isinstance(item, dict):
                    continue

                code = extract_valid_code(item)
                if not code:
                    continue

                # 1) 보유수량 파싱 (양수 수량을 찾을 때까지 순회)
                qty = 0
                for qk in qty_keys:
                    if item.get(qk) is not None:
                        q_str = str(item.get(qk)).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            q_parsed = int(float(q_str))
                            if q_parsed > 0:
                                qty = q_parsed
                                break
                        except (ValueError, TypeError):
                            pass

                # 2) 현재가 파싱 (0 초과 현재가를 찾을 때까지 순회)
                current_price = 0.0
                for ck in cur_p_keys:
                    if item.get(ck) is not None:
                        cp_str = str(item.get(ck)).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            cp_parsed = float(cp_str)
                            if cp_parsed > 0:
                                current_price = cp_parsed
                                break
                        except (ValueError, TypeError):
                            pass

                # 3) 평가금액 및 매입금액 파싱
                evlu_amt = 0.0
                for ek in evlu_amt_keys:
                    if item.get(ek) is not None:
                        ek_clean = str(item.get(ek)).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            ek_parsed = float(ek_clean)
                            if ek_parsed > 0:
                                evlu_amt = ek_parsed
                                break
                        except (ValueError, TypeError):
                            pass

                pchs_amt = 0.0
                for pk in pchs_amt_keys:
                    if item.get(pk) is not None:
                        pk_clean = str(item.get(pk)).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            pk_parsed = float(pk_clean)
                            if pk_parsed > 0:
                                pchs_amt = pk_parsed
                                break
                        except (ValueError, TypeError):
                            pass

                # 4) 평가손익 및 수익률 추출
                item_pnl: Optional[float] = None
                for pnl_k in pnl_keys:
                    if item.get(pnl_k) is not None:
                        pnl_clean = str(item.get(pnl_k)).strip().replace(',', '')
                        try:
                            item_pnl = float(pnl_clean)
                            break
                        except (ValueError, TypeError):
                            pass

                item_yield_rate: Optional[float] = None
                for rt_k in rt_keys:
                    if item.get(rt_k) is not None:
                        rt_clean = str(item.get(rt_k)).strip().replace(',', '').replace('%', '')
                        try:
                            item_yield_rate = float(rt_clean)
                            break
                        except (ValueError, TypeError):
                            pass

                # 5) 수량 역산 안전 가드 (수량이 0 이하일 때 평가금액/매입금액 기반 산출)
                if qty <= 0 and evlu_amt > 0 and current_price > 0:
                    qty = int(round(evlu_amt / current_price))
                elif qty <= 0 and pchs_amt > 0 and current_price > 0:
                    qty = int(round(pchs_amt / current_price))

                if qty <= 0:
                    continue

                # 6) 매수가(매입평균단가) 6단계 다중 방어 해석 알고리즘
                buy_price = 0.0

                # Layer 1: 직접 단가 키 파싱 (1원 이하 무효값 제외)
                for bk in buy_p_keys:
                    if item.get(bk) is not None:
                        bp_str = str(item.get(bk)).strip().replace(',', '').replace('+', '').replace('-', '')
                        try:
                            bp_parsed = float(bp_str)
                            if bp_parsed > 1.0:
                                buy_price = bp_parsed
                                break
                        except (ValueError, TypeError):
                            pass

                # Layer 2: 총 매입금액 / 수량 역산 (pchs_amt / qty)
                if buy_price <= 1.0 and pchs_amt > 0 and qty > 0:
                    cand_bp = pchs_amt / qty
                    if cand_bp > 1.0:
                        buy_price = cand_bp

                # Layer 3: 평가금액 - 평가손익 기반 역산 ((evlu_amt - pnl) / qty)
                if buy_price <= 1.0 and item_pnl is not None and qty > 0:
                    base_eval = evlu_amt if evlu_amt > 0 else (current_price * qty)
                    if base_eval > 0:
                        inferred_pchs_amt = base_eval - item_pnl
                        if inferred_pchs_amt > 0:
                            cand_bp = inferred_pchs_amt / qty
                            if cand_bp > 1.0:
                                buy_price = cand_bp

                # Layer 4: 현재가 / (1 + 수익률) 역산 (current_price / (1 + yield/100))
                if buy_price <= 1.0 and current_price > 1.0 and item_yield_rate is not None and item_yield_rate != 0:
                    try:
                        denom = 1.0 + (item_yield_rate / 100.0)
                        if denom > 0:
                            inferred_bp = current_price / denom
                            if inferred_bp > 1.0:
                                buy_price = round(inferred_bp, 2)
                    except ZeroDivisionError:
                        pass

                # Layer 5: 직전 메모리 포지션(prev_positions)의 정상 매수가 상속
                if buy_price <= 1.0 and code in prev_positions:
                    prev_bp = prev_positions[code].get('buy_price', 0)
                    if prev_bp > 1.0:
                        buy_price = prev_bp

                # Layer 6: 현재가로 안전 폴백 (절대 1원/0원으로 표출되지 않도록 방어)
                if buy_price <= 1.0 and current_price > 1.0:
                    buy_price = current_price

                # 현재가 폴백
                if current_price <= 0 and buy_price > 0:
                    current_price = buy_price
                elif current_price <= 0:
                    current_price = 1.0

                if buy_price <= 0:
                    buy_price = current_price

                # 손익 및 수익률 보정 산출
                if item_pnl is None:
                    item_pnl = (current_price - buy_price) * qty
                if item_yield_rate is None:
                    item_yield_rate = ((current_price / buy_price) - 1.0) * 100.0 if buy_price > 0 else 0.0

                name = ''
                for nk in name_keys:
                    if item.get(nk) is not None and str(item.get(nk)).strip():
                        n_cand = str(item.get(nk)).strip()
                        if n_cand != code and not n_cand.isdigit():
                            name = n_cand
                            break
                if not name:
                    name = prev_positions.get(code, {}).get('name', code)

                prev_pos = prev_positions.get(code)
                highest_price = max(prev_pos.get('highest_price', buy_price), current_price) if prev_pos else max(buy_price, current_price)
                sell_stage = prev_pos.get('sell_stage', 0) if prev_pos else 0

                new_positions[code] = {
                    'name': name,
                    'qty': qty,
                    'buy_price': buy_price,
                    'current_price': current_price,
                    'highest_price': highest_price,
                    'sell_stage': sell_stage,
                    'pnl': item_pnl,
                    'yield_rate': item_yield_rate
                }

            if new_positions:
                self.positions = new_positions
                pos_summary = ", ".join([f"{p['name']}({code}) {p['qty']}주@{int(p['current_price']):,}원" for code, p in new_positions.items()])
                print(f"📦 [Portfolio sync_positions] {len(new_positions)}개 보유 종목 동기화 완료: {pos_summary}")
            else:
                # 키움 API에서 정상 잔고 수신 시 보유종목이 0건이면 명시적으로 빈 딕셔너리로 초기화 (유령 주식 제거)
                self.positions = {}
                print("📦 [Portfolio sync_positions] 보유 종목 0건 (전량 매도/미보유 상태) 동기화 완료.")

    async def restore_positions_from_db(self, db_positions: List[Dict[str, Any]]):
        """DB로부터 포지션을 안전 복원 (D+2 주문가능현금 current_capital을 차감하지 않고 독립 복원)"""
        async with self._lock:
            if not db_positions:
                return
            restored = {}
            for p in db_positions:
                raw_code = p.get('code') or p.get('stk_cd', '')
                code = str(raw_code).replace('A', '').split('_')[0].strip()
                if len(code) < 6 and code.isdigit():
                    code = code.zfill(6)
                name = p.get('name') or code
                qty = int(p.get('qty', 0))
                buy_p = float(p.get('buy_price', 0))
                cur_p = float(p.get('current_price') or (buy_p if buy_p > 1.0 else 0))
                # DB 자가 치유: 과거 1원/0원으로 저장된 경우 현재가(또는 pnl/yield_rate 역산)로 안전 보정
                if buy_p <= 1.0 and cur_p > 1.0:
                    buy_p = cur_p
                elif cur_p <= 0 and buy_p > 0:
                    cur_p = buy_p
                highest_p = float(p.get('highest_price') or max(buy_p, cur_p))
                sell_stage = int(p.get('sell_stage', 0))
                if code and qty > 0:
                    pnl = float(p.get('pnl')) if p.get('pnl') is not None else ((cur_p - buy_p) * qty)
                    yield_rate = float(p.get('yield_rate')) if p.get('yield_rate') is not None else (((cur_p / buy_p) - 1.0) * 100.0 if buy_p > 0 else 0.0)
                    restored[code] = {
                        'name': name,
                        'qty': qty,
                        'buy_price': buy_p,
                        'current_price': cur_p,
                        'highest_price': highest_p,
                        'sell_stage': sell_stage,
                        'pnl': pnl,
                        'yield_rate': yield_rate
                    }
            if restored:
                self.positions = restored
                print(f"🛡️ [Portfolio restore_positions_from_db] DB로부터 {len(restored)}개 포지션 안전 복원 완료: {[p['name'] for p in restored.values()]}")

    async def add_position(self, code: str, name: str, qty: int, buy_price: float):
        """신규 포지션 편입"""
        async with self._lock:
            self.positions[code] = {
                'name': name,
                'qty': int(qty),
                'buy_price': float(buy_price),
                'current_price': float(buy_price),
                'highest_price': float(buy_price),
                'sell_stage': 0
            }
            self.current_capital = max(0.0, self.current_capital - (qty * buy_price))

    async def remove_position(self, code: str, sell_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """포지션 청산 (전량 매도) 및 수익률/실현손익 기록"""
        async with self._lock:
            pos = self.positions.pop(code, None)
            if pos and sell_price:
                self.current_capital += pos['qty'] * sell_price
                buy_p = pos['buy_price']
                if buy_p > 0:
                    ret = (sell_price - buy_p) / buy_p
                    pnl = (sell_price - buy_p) * pos['qty']
                    self.trade_returns.append(ret)
                    self.daily_realized_pnl += pnl
            return pos

    async def update_partial_sell(self, code: str, sold_qty: int, sell_price: float, next_stage: int):
        """부분 익절 처리 및 실현 손익 기록"""
        async with self._lock:
            if code in self.positions:
                pos = self.positions[code]
                pos['qty'] = max(0, pos['qty'] - sold_qty)
                pos['sell_stage'] = next_stage
                self.current_capital += sold_qty * sell_price
                buy_p = pos['buy_price']
                if buy_p > 0:
                    ret = (sell_price - buy_p) / buy_p
                    pnl = (sell_price - buy_p) * sold_qty
                    self.trade_returns.append(ret)
                    self.daily_realized_pnl += pnl
                if pos['qty'] == 0:
                    self.positions.pop(code, None)

    async def update_current_price(self, code: str, current_price: float):
        """실시간 시세 반영 및 최고가 갱신"""
        async with self._lock:
            if code in self.positions:
                pos = self.positions[code]
                pos['current_price'] = current_price
                if current_price > pos.get('highest_price', 0):
                    pos['highest_price'] = current_price

    def get_available_cash_with_pending_lock(self, pending_buy_amount: float = 0.0) -> float:
        """미체결 매수 주문 증거금을 차감한 순수 가용 주문 가능 금액 반환"""
        return max(0.0, self.current_capital - float(pending_buy_amount or 0.0))

    async def get_order_qty(self, current_price: float, atr: Optional[float] = None, available_cash: Optional[float] = None) -> int:
        """
        프랙셔널 켈리 공식 및 변동성 기반 주문 수량 계산
        - 1회 거래 최대 허용 위험액(Risk-at-Risk) 1.5% 한도 적용
        - 미체결 매수 증거금 락이 반영된 실제 가용 예수금(available_cash) 기준 산출
        """
        async with self._lock:
            if current_price <= 0:
                return 0

            effective_cash = float(available_cash) if available_cash is not None else self.current_capital
            invested = sum(pos['buy_price'] * pos['qty'] for pos in self.positions.values())
            total_asset = effective_cash + invested

            # 1. 켈리 비중 산출
            kelly_alloc = self.get_kelly_allocation_fraction()
            allocate_amt = total_asset * kelly_alloc

            # 2. ATR 위험액 캡 (단일 거래 최대 손실액을 총 자산의 1.5%로 제한)
            if atr and atr > 0:
                max_risk_amt = total_asset * 0.015
                # 1.5 ATR 손절 시 위험액 기준 수량 캡 (손익비 개선 및 리스크 관리 강화)
                atr_qty_cap = int(max_risk_amt // (1.5 * atr))
                if atr_qty_cap > 0:
                    allocate_amt = min(allocate_amt, atr_qty_cap * current_price)

            # 슬리피지 및 예수금 초과 방지 5% 안전 버퍼
            safe_allocate_amt = allocate_amt * 0.95
            max_available = effective_cash * 0.95

            target_amt = min(safe_allocate_amt, max_available)
            qty = int(target_amt // current_price)
            # 소액 자본(10~50만원대)에서 켈리 비중 배분액이 1주 가격보다 적더라도,
            # 가용 예수금이 1주 가격 이상이면 최소 1주 매수 허용
            if qty == 0 and effective_cash >= current_price:
                qty = 1
            return qty

    async def can_buy(self, code: str, pending_buy_codes: Optional[set] = None) -> bool:
        """신규 매수 가능 여부 검증 (보유 종목 + 미체결 매수 대기 종목 수 한도 및 중복 체크)"""
        async with self._lock:
            if code in self.positions:
                return False
            if pending_buy_codes and code in pending_buy_codes:
                return False
            active_slots = len(self.positions) + (len(pending_buy_codes) if pending_buy_codes else 0)
            return active_slots < self.max_stocks

    async def get_snapshot(self) -> Dict[str, Any]:
        """웹소켓/대시보드 표출용 전체 포트폴리오 스냅샷 (PnL 실시간 산출)"""
        async with self._lock:
            invested_eval = 0.0
            invested_pchs = 0.0
            pos_list = []

            for code, pos in self.positions.items():
                pchs = pos['buy_price'] * pos['qty']
                eval_amt = pos['current_price'] * pos['qty']
                pnl = eval_amt - pchs
                yield_rate = (pnl / pchs * 100.0) if pchs > 0 else 0.0

                invested_pchs += pchs
                invested_eval += eval_amt

                pos_list.append({
                    'code': code,
                    'name': pos.get('name', code),
                    'qty': pos.get('qty', 0),
                    'buy_price': pos.get('buy_price', 0),
                    'current_price': pos.get('current_price', pos.get('buy_price', 0)),
                    'highest_price': pos.get('highest_price', pos.get('current_price', pos.get('buy_price', 0))),
                    'sell_stage': pos.get('sell_stage', 0),
                    'eval_amt': eval_amt,
                    'pnl': pnl,
                    'yield_rate': yield_rate
                })

            # 총 평가자산: 명시적 total_asset이 예수금 이상이면 우선 적용, 없으면 (예수금 + 주식평가액)
            # total_asset(총자산)은 항상 available_cash(D+2 예수금) 이상이어야 함 (원리: 예수금 + 보유주식평가액)
            # 만약 total_asset < current_capital + invested_eval이면 시장가 하락 등으로 갱신된 값 사용
            if self.total_asset > 0:
                calculated_total = self.current_capital + invested_eval
                # 안전 가드: total_asset이 계산된 총자산보다 작으면 현재 시장 평가액 사용
                total_asset = max(self.total_asset, calculated_total)
            else:
                total_asset = self.current_capital + invested_eval
                # total_asset이 0으로 초기화된 경우 경고 로그 출력 (디버깅용)
                if total_asset > 0:
                    print(f"⚠️ [Portfolio] total_asset이 0에서 초기화됨: 총자산={int(total_asset):,}원 (D+2예수금: {int(self.current_capital):,}원 + 평가액: {int(invested_eval):,}원)")

            total_pnl = invested_eval - invested_pchs
            if invested_pchs > 0:
                total_yield = (total_pnl / invested_pchs * 100.0)
            elif self.initial_capital > 0:
                total_yield = (total_pnl / self.initial_capital * 100.0)
            else:
                total_yield = 0.0

            return {
                'initial_capital': self.initial_capital,
                'current_capital': self.current_capital,
                'available_cash': self.current_capital,
                'total_asset': total_asset,
                'invested_capital': invested_eval,
                'invested_pchs': invested_pchs,
                'invested_eval': invested_eval,
                'total_pnl': total_pnl,
                'unrealized_pnl': total_pnl,
                'daily_realized_pnl': self.daily_realized_pnl,
                'total_trading_pnl': self.daily_realized_pnl + total_pnl,
                'total_yield': round(total_yield, 2),
                'total_yield_rate': round(total_yield, 2),
                'kelly_allocation_pct': self.get_kelly_allocation_fraction() * 100.0,
                'positions_count': len(pos_list),
                'stock_count': len(pos_list),
                'positions': pos_list
            }
