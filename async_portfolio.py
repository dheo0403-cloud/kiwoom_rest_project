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

    async def sync_positions(self, account_data: Dict[str, Any]):
        """키움 계좌 잔고 API 응답(kt00004/kt00005/OPW00018) 기반 포지션 동기화 (모의/실전/다중 스키마 100% 대응)"""
        async with self._lock:
            if not account_data:
                return

            # 1. 딕셔너리의 모든 리스트 키들을 검사하여 종목 리스트가 있는 키를 지능적으로 탐색
            raw_list = []
            code_keys = [
                'stk_cd', 'pdno', 'code', 'expcode', 'mksc_shrn_iscd', 'shcode',
                'jongmok_code', 'item_code', 'stck_shrn_iscd', 'item_cd', 'prdt_cd',
                'stk_code', 'iscd', 'jong_cd', 'stck_cd', 'stk_no', 'isu_cd'
            ]
            name_keys = [
                'stk_nm', 'name', 'prdt_name', 'jongmok_name', 'hts_kor_isnm',
                'item_name', 'stck_nm', 'isu_nm', 'kor_isnm'
            ]
            qty_keys = [
                'hldg_qty', 'ccls_qty_sum', 'qty', 'hold_qty', 'ord_psbl_qty',
                'bal_qty', 'ccls_qty', 'rmnd_qty', 'tot_hldg_qty', 'hld_qty',
                'jango_qty', 'now_qty', 'stck_qty', 'ccls_qty'
            ]

            def is_holding_item(d: Any) -> bool:
                if not isinstance(d, dict):
                    return False
                has_code = any(k in d for k in code_keys)
                has_name_or_qty = any(k in d for k in name_keys) or any(k in d for k in qty_keys)
                return has_code or has_name_or_qty

            def extract_candidate_lists(data: Any, depth: int = 0) -> List[List[Dict[str, Any]]]:
                if depth > 3 or not isinstance(data, dict):
                    return []
                candidates = []
                priority_keys = [
                    'output2', 'Output2', 'output', 'Output', 'list', 'acnt_dtl_list',
                    'holdings', 'stk_list', 'item_list', 'output1', 'Output1', 'data',
                    'grid', 'table', 'rows', 'items'
                ]
                for k in priority_keys:
                    v = data.get(k)
                    if isinstance(v, list) and v:
                        if any(is_holding_item(x) for x in v):
                            candidates.append(v)
                # 하위 딕셔너리 재귀 탐색 (data, body, response 등)
                for k, v in data.items():
                    if isinstance(v, dict):
                        candidates.extend(extract_candidate_lists(v, depth + 1))
                return candidates

            candidate_lists = extract_candidate_lists(account_data)
            if candidate_lists:
                raw_list = candidate_lists[0]

            prev_positions = self.positions.copy()
            new_positions = {}
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                code = ''
                for k in code_keys:
                    val = item.get(k)
                    if val is not None and str(val).strip():
                        code = str(val).strip()
                        break

                code = code.replace('A', '').split('_')[0].strip()
                if not code:
                    continue
                if len(code) > 6 and code[-6:].isdigit():
                    code = code[-6:]
                elif len(code) != 6 and not code.isdigit():
                    continue

                # 보유수량 파싱
                qty_val = 0
                for qk in qty_keys:
                    if item.get(qk) is not None:
                        qty_val = item.get(qk)
                        break

                qty_clean = str(qty_val).strip().replace(',', '').replace('+', '').replace('-', '')
                try:
                    qty = int(float(qty_clean))
                except (ValueError, TypeError):
                    qty = 0

                if qty <= 0:
                    continue

                # 매입평균단가 파싱
                buy_p_keys = [
                    'pchs_avg_pric', 'buy_price', 'pchs_price', 'avg_buy_price',
                    'buy_uv', 'pchs_unit_amt', 'pchs_avg_amt', 'avg_pchs_price',
                    'pchs_prc', 'buy_prc'
                ]
                buy_p_val = 0
                for bk in buy_p_keys:
                    if item.get(bk) is not None:
                        buy_p_val = item.get(bk)
                        break

                buy_p_clean = str(buy_p_val).strip().replace(',', '').replace('+', '').replace('-', '')
                try:
                    buy_price = float(buy_p_clean)
                except (ValueError, TypeError):
                    buy_price = 0.0

                # 만약 매입단가가 0이고 총매입금액(pchs_amt)이 있다면 단가 역산
                if buy_price <= 0 and qty > 0:
                    pchs_amt_val = item.get('pchs_amt') or item.get('buy_amt') or item.get('pchs_amt_smtl_amt') or 0
                    pchs_amt_clean = str(pchs_amt_val).strip().replace(',', '').replace('+', '').replace('-', '')
                    try:
                        pchs_amt = float(pchs_amt_clean)
                        if pchs_amt > 0:
                            buy_price = pchs_amt / qty
                    except (ValueError, TypeError):
                        pass

                # 현재가 파싱
                cur_p_keys = [
                    'prpr', 'current_price', 'stck_prpr', 'cur_prc', 'clpr',
                    'price', 'now_prc', 'stck_clpr'
                ]
                cur_p_val = 0
                for ck in cur_p_keys:
                    if item.get(ck) is not None:
                        cur_p_val = item.get(ck)
                        break

                cur_p_clean = str(cur_p_val).strip().replace(',', '').replace('+', '').replace('-', '')
                try:
                    current_price = float(cur_p_clean)
                except (ValueError, TypeError):
                    current_price = 0.0

                # 만약 현재가가 0이고 평가금액(evlu_amt)이 있다면 현재가 역산
                if current_price <= 0 and qty > 0:
                    evlu_amt_val = item.get('evlu_amt') or item.get('eval_amt') or item.get('evlu_amt_smtl_amt') or 0
                    evlu_amt_clean = str(evlu_amt_val).strip().replace(',', '').replace('+', '').replace('-', '')
                    try:
                        evlu_amt = float(evlu_amt_clean)
                        if evlu_amt > 0:
                            current_price = evlu_amt / qty
                    except (ValueError, TypeError):
                        pass

                if current_price <= 0:
                    current_price = buy_price

                # 평가손익 및 수익률 추출
                pnl_val = item.get('evlu_pfls_amt') or item.get('pnl') or item.get('evlt_pfls_amt') or 0
                pnl_clean = str(pnl_val).strip().replace(',', '')
                try:
                    item_pnl = float(pnl_clean)
                except (ValueError, TypeError):
                    item_pnl = (current_price - buy_price) * qty

                rt_val = item.get('evlu_pfls_rt') or item.get('yield_rate') or item.get('evlt_pfls_rt') or 0
                rt_clean = str(rt_val).strip().replace(',', '').replace('%', '')
                try:
                    item_yield_rate = float(rt_clean)
                except (ValueError, TypeError):
                    item_yield_rate = ((current_price / buy_price) - 1.0) * 100.0 if buy_price > 0 else 0.0

                name = ''
                for nk in name_keys:
                    if item.get(nk) is not None and str(item.get(nk)).strip():
                        name = str(item.get(nk)).strip()
                        break
                if not name:
                    name = code

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
            elif prev_positions and (self.total_asset > self.current_capital):
                # 임시 API 통신 에러 시 기존 포지션 보존
                self.positions = prev_positions

            if new_positions:
                self.positions = new_positions
            elif prev_positions and (self.total_asset > self.current_capital):
                # 임시 API 통신 에러 시 기존 포지션 보존
                self.positions = prev_positions

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
        """포지션 청산 (전량 매도) 및 수익률 기록"""
        async with self._lock:
            pos = self.positions.pop(code, None)
            if pos and sell_price:
                self.current_capital += pos['qty'] * sell_price
                buy_p = pos['buy_price']
                if buy_p > 0:
                    ret = (sell_price - buy_p) / buy_p
                    self.trade_returns.append(ret)
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
                    self.trade_returns.append(ret)
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

    async def get_order_qty(self, current_price: float, atr: Optional[float] = None) -> int:
        """
        프랙셔널 켈리 공식 및 변동성 기반 주문 수량 계산
        - 1회 거래 최대 허용 위험액(Risk-at-Risk) 1.5% 한도 적용
        """
        async with self._lock:
            if current_price <= 0:
                return 0
            invested = sum(pos['buy_price'] * pos['qty'] for pos in self.positions.values())
            total_asset = self.current_capital + invested

            # 1. 켈리 비중 산출
            kelly_alloc = self.get_kelly_allocation_fraction()
            allocate_amt = total_asset * kelly_alloc

            # 2. ATR 위험액 캡 (단일 거래 최대 손실액을 총 자산의 1.5%로 제한)
            if atr and atr > 0:
                max_risk_amt = total_asset * 0.015
                # 2.0 ATR 손절 시 위험액 기준 수량 캡
                atr_qty_cap = int(max_risk_amt // (2.0 * atr))
                if atr_qty_cap > 0:
                    allocate_amt = min(allocate_amt, atr_qty_cap * current_price)

            # 슬리피지 및 예수금 초과 방지 5% 안전 버퍼
            safe_allocate_amt = allocate_amt * 0.95
            max_available = self.current_capital * 0.95

            target_amt = min(safe_allocate_amt, max_available)
            qty = int(target_amt // current_price)
            # 소액 자본(10~50만원대)에서 켈리 비중 배분액이 1주 가격보다 적더라도,
            # 가용 예수금이 1주 가격 이상이면 최소 1주 매수 허용
            if qty == 0 and self.current_capital >= current_price:
                qty = 1
            return qty

    async def can_buy(self, code: str) -> bool:
        """신규 매수 가능 여부 검증 (종목 수 한도 및 중복 체크)"""
        async with self._lock:
            if code in self.positions:
                return False
            return len(self.positions) < self.max_stocks

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
                    'name': pos['name'],
                    'qty': pos['qty'],
                    'buy_price': pos['buy_price'],
                    'current_price': pos['current_price'],
                    'highest_price': pos['highest_price'],
                    'sell_stage': pos['sell_stage'],
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
                'total_yield': round(total_yield, 2),
                'total_yield_rate': round(total_yield, 2),
                'kelly_allocation_pct': self.get_kelly_allocation_fraction() * 100.0,
                'positions_count': len(pos_list),
                'stock_count': len(pos_list),
                'positions': pos_list
            }
