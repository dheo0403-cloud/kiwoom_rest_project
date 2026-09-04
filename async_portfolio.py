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
        self.current_capital = initial_capital
        self.max_stocks = max_stocks
        self.weight_per_stock = 1.0 / max_stocks
        self.kelly_fraction = kelly_fraction  # 40% Fractional Kelly
        self.positions: Dict[str, Dict[str, Any]] = {}
        # 최근 50회 매매 수익률 이력 (예: +0.05, -0.02)
        self.trade_returns: collections.deque = collections.deque(maxlen=50)
        self._lock = asyncio.Lock()

    async def sync_capital(self, deposit: float):
        """예수금(주문가능 현금) 동기화"""
        async with self._lock:
            self.current_capital = float(deposit)

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
        """키움 계좌 잔고 API 응답(kt00005) 기반 포지션 동기화"""
        async with self._lock:
            if not account_data:
                return

            output2 = account_data.get('output2', [])
            if isinstance(output2, dict):
                output2 = [output2]

            prev_positions = self.positions.copy()
            new_positions = {}
            for item in output2:
                code = item.get('stk_cd', '').strip()
                if not code:
                    continue
                # 종목코드 6자리 정규화
                if code.startswith('A'):
                    code = code[1:]

                qty = int(item.get('hldg_qty', 0) or item.get('ccls_qty_sum', 0) or 0)
                if qty <= 0:
                    continue

                buy_price = float(item.get('pchs_avg_pric', 0) or item.get('pchs_amt', 0) or 0)
                current_price = float(item.get('prpr', 0) or buy_price)
                name = item.get('stk_nm', code)

                prev_pos = prev_positions.get(code)
                highest_price = max(prev_pos.get('highest_price', buy_price), current_price) if prev_pos else max(buy_price, current_price)
                sell_stage = prev_pos.get('sell_stage', 0) if prev_pos else 0

                new_positions[code] = {
                    'name': name,
                    'qty': qty,
                    'buy_price': buy_price,
                    'current_price': current_price,
                    'highest_price': highest_price,
                    'sell_stage': sell_stage
                }
            self.positions = new_positions

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
            if qty == 0 and max_available >= current_price:
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

            total_asset = self.current_capital + invested_eval
            total_pnl = invested_eval - invested_pchs
            total_yield = (total_pnl / self.initial_capital * 100.0) if self.initial_capital > 0 else 0.0

            return {
                'initial_capital': self.initial_capital,
                'current_capital': self.current_capital,
                'total_asset': total_asset,
                'invested_pchs': invested_pchs,
                'invested_eval': invested_eval,
                'total_pnl': total_pnl,
                'total_yield': total_yield,
                'kelly_allocation_pct': self.get_kelly_allocation_fraction() * 100.0,
                'positions_count': len(pos_list),
                'positions': pos_list
            }
