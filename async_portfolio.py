"""
Gate Info:
- Importers/callers: kiwoom_rest_project/main_rest_async.py, kiwoom_rest_project/api_server.py, kiwoom_rest_project/test_async_core.py
- Affected API: None (In-memory Thread-Safe Portfolio State)
- Data schemas: Position Dict {code, name, qty, buy_price, current_price, highest_price, sell_stage, pnl, yield_rate}
- User's verbatim instruction: "미안 AKS에 대한 부분은 삭제해줘 마이그레이션 후 별도로 문의할께"
"""
import asyncio
from typing import Dict, Any, Optional

class AsyncPortfolioManager:
    """
    동시성 안전한 비동기 포트폴리오 관리자
    - asyncio.Lock() 기반 세밀한 상태 동기화
    - 실시간 평가 손익, 비중 기반 주문 수량 계산
    - 웹소켓 브로드캐스트용 스냅샷 직렬화 지원
    """
    def __init__(self, initial_capital: float = 10_000_000, max_stocks: int = 5):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.max_stocks = max_stocks
        self.weight_per_stock = 1.0 / max_stocks
        self.positions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def sync_capital(self, deposit: float):
        """예수금(주문가능 현금) 동기화"""
        async with self._lock:
            self.current_capital = float(deposit)

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
            # 추정 예수금 차감
            self.current_capital = max(0.0, self.current_capital - (qty * buy_price))

    async def remove_position(self, code: str, sell_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """포지션 청산 (전량 매도)"""
        async with self._lock:
            pos = self.positions.pop(code, None)
            if pos and sell_price:
                self.current_capital += pos['qty'] * sell_price
            return pos

    async def update_partial_sell(self, code: str, sold_qty: int, sell_price: float, next_stage: int):
        """부분 익절 처리"""
        async with self._lock:
            if code in self.positions:
                pos = self.positions[code]
                pos['qty'] = max(0, pos['qty'] - sold_qty)
                pos['sell_stage'] = next_stage
                self.current_capital += sold_qty * sell_price
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

    async def get_order_qty(self, current_price: float) -> int:
        """포트폴리오 자산 배분 기준 안전 주문 수량 계산"""
        async with self._lock:
            if current_price <= 0:
                return 0
            invested = sum(pos['buy_price'] * pos['qty'] for pos in self.positions.values())
            total_asset = self.current_capital + invested
            allocate_amt = total_asset * self.weight_per_stock

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
            total_unrealized_pnl = invested_eval - invested_pchs
            total_yield_rate = (total_unrealized_pnl / (self.current_capital + invested_pchs) * 100.0) if (self.current_capital + invested_pchs) > 0 else 0.0

            return {
                'current_capital': self.current_capital,
                'invested_amount': invested_eval,
                'total_asset': total_asset,
                'unrealized_pnl': total_unrealized_pnl,
                'total_yield_rate': total_yield_rate,
                'stock_count': len(self.positions),
                'max_stocks': self.max_stocks,
                'positions': pos_list
            }
