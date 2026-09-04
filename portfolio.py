class PortfolioManager:
    def __init__(self, db_manager, max_stocks=5, weight_per_stock=0.2, max_daily_entries=3):
        self.db = db_manager
        self.max_stocks = max_stocks
        self.weight_per_stock = weight_per_stock
        self.max_daily_entries = max_daily_entries
        
        self.daily_entries = 0
        self.positions = {} # {code: {'buy_price', 'qty', 'name', 'sell_stage', 'highest_price'}}
        self.current_capital = 0

    def sync_capital(self, total_capital):
        """계좌 잔고(예수금 등) 동기화"""
        self.current_capital = total_capital

    def sync_positions(self, positions_list):
        """실제 증권사 잔고 내역을 받아와서 내부 포트폴리오를 하드 싱크 (포지션 꼬임 방지)"""
        self.positions.clear()
        for pos in positions_list:
            code = pos.get('stk_cd') or pos.get('code') or pos.get('pdno')
            if not code: continue
            
            if code.startswith('A'): code = code[1:]
                
            qty = int(float(pos.get('cur_qty') or pos.get('hldg_qty') or pos.get('qty', 0)))
            if qty <= 0: continue
            
            price = int(float(pos.get('buy_uv') or pos.get('pchs_avg_pric') or pos.get('buy_price', 0)))
            name = pos.get('stk_nm') or pos.get('prdt_name') or pos.get('name', code)
            cur_price = int(float(pos.get('cur_prc') or pos.get('current_price') or price))
            
            self.positions[code] = {
                'buy_price': price,
                'qty': qty,
                'name': name,
                'current_price': cur_price,
                'sell_stage': 0,
                'highest_price': price
            }
        print(f"✅ 실제 잔고 기준 보유 종목 동기화 완료: {len(self.positions)}종목")
        for code, info in self.positions.items():
            print(f"   - {info['name']}({code}): {info['qty']}주 (평단 {info['buy_price']}원)")

    def can_enter(self):
        """신규 진입 가능 여부"""
        if len(self.positions) >= self.max_stocks:
            return False
        if self.daily_entries >= self.max_daily_entries:
            return False
        return True

    def get_order_qty(self, current_price):
        """총 자산(잔고+투자금) 대비 비중에 따른 매수 수량 계산"""
        if current_price <= 0: return 0
        
        # 현재 투자된 총 금액 (보유 종목들의 매입 금액 합산)
        invested_amount = sum(pos['buy_price'] * pos['qty'] for pos in self.positions.values())
        
        # 총 자산 = 가용 현금 + 투자된 금액
        total_asset = self.current_capital + invested_amount
        
        # 종목당 할당 금액 (예: 총 자산의 20%)
        allocate_amt = total_asset * self.weight_per_stock
        
        # 수수료 및 증거금 여유분(약 5%)을 고려하여 실제 매수 가능 금액 축소 (증거금 부족 방지)
        safe_allocate_amt = allocate_amt * 0.95
        
        # 만약 할당 금액이 현재 가진 가용 현금(의 95%)보다 크다면, 가용 현금 한도 내로 제한
        max_available_cash = self.current_capital * 0.95
        if safe_allocate_amt > max_available_cash:
            safe_allocate_amt = max_available_cash
            
        qty = int(safe_allocate_amt // current_price)
        
        # [소액 계좌 예외 처리] 
        # 비중(20%) 할당 금액으로는 1주도 살 수 없지만, 계좌에 실제 가용 현금이 충분하다면 최소 1주는 매수하도록 보장
        if qty == 0 and max_available_cash >= current_price:
            qty = 1
            
        return qty

    def add_position(self, code, name, qty, price):
        if code in self.positions:
            self.positions[code]['qty'] += qty
        else:
            self.positions[code] = {
                'buy_price': price,
                'qty': qty,
                'name': name,
                'current_price': price,
                'sell_stage': 0,
                'highest_price': price
            }
            self.daily_entries += 1

    def update_highest_price(self, code, current_price):
        """트레일링 스탑용 최고가 및 실시간 현재가 갱신"""
        if code in self.positions:
            pos = self.positions[code]
            pos['current_price'] = current_price
            if current_price > pos.get('highest_price', 0):
                pos['highest_price'] = current_price

    def advance_sell_stage(self, code):
        """익절 단계 1단계 진행 (0→1→2)"""
        if code in self.positions:
            self.positions[code]['sell_stage'] = self.positions[code].get('sell_stage', 0) + 1

    def update_position_qty(self, code, sold_qty):
        """매도된 수량만큼 보유 수량 차감"""
        if code in self.positions:
            self.positions[code]['qty'] -= sold_qty
            if self.positions[code]['qty'] <= 0:
                self.remove_position(code)

    def mark_half_sold(self, code):
        """하위 호환용 (기존 코드 유지)"""
        if code in self.positions:
            self.positions[code]['sell_stage'] = max(self.positions[code].get('sell_stage', 0), 1)

    def remove_position(self, code):
        if code in self.positions:
            del self.positions[code]

    def reset_daily_count(self):
        self.daily_entries = 0
