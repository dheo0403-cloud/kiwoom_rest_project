"""
고충실도(High-Fidelity) 퀀트 트레이딩 백테스팅 및 Walk-Forward 최적화 엔진
- 현실적 마찰비용 모델링: 매수/매도 수수료(0.015%x2) + 증권거래세(0.18%) = 0.21%, 체결 슬리피지(0.08%) 반영
- ATR 변동성 돌파 진입, 샹들리에 엑시트(Chandelier Exit), R-배수 분할 익절, 프랙셔널 켈리 자산 배분 시뮬레이션
- In-Sample (70%) vs Out-of-Sample (30%) Walk-Forward Optimization (WFO) 지원
- 샤프 지수(Sharpe Ratio), 최대 낙폭(MDD), 손익비(Profit Factor), 승률(Win Rate) 정밀 산출
"""
import dataclasses
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from indicators import TechnicalIndicators


@dataclasses.dataclass
class BacktestTrade:
    code: str
    entry_date: Any
    entry_price: float
    shares: int
    initial_stop: float
    trailing_stop: float
    highest_high: float
    sell_stage: int = 0
    exit_date: Optional[Any] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    return_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    fee_tax_paid: float = 0.0


class HighFidelityBacktester:
    """
    고충실도 퀀트 트레이딩 이벤트 기반 백테스터
    """
    def __init__(
        self,
        initial_capital: float = 10_000_000.0,
        k_breakout: float = 0.5,
        atr_period: int = 14,
        atr_hard_stop_mult: float = 2.0,
        atr_trailing_stop_mult: float = 2.5,
        kelly_fraction: float = 0.4,
        max_stocks: int = 5,
        slippage_rate: float = 0.0008,    # 0.08% 호가 슬리피지
        buy_fee_rate: float = 0.00015,     # 0.015% 매수 수수료
        sell_fee_rate: float = 0.00015,    # 0.015% 매도 수수료
        sell_tax_rate: float = 0.0018      # 0.18% 증권거래세
    ):
        self.initial_capital = initial_capital
        self.k_breakout = k_breakout
        self.atr_period = atr_period
        self.atr_hard_stop_mult = atr_hard_stop_mult
        self.atr_trailing_stop_mult = atr_trailing_stop_mult
        self.kelly_fraction = kelly_fraction
        self.max_stocks = max_stocks
        self.slippage_rate = slippage_rate
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.sell_tax_rate = sell_tax_rate

        self.closed_trades: List[BacktestTrade] = []
        self.equity_curve: List[Dict[str, Any]] = []

    def _calculate_kelly_allocation(self, trade_returns: List[float], min_trades: int = 10) -> float:
        """프랙셔널 켈리 자산 배분 비중 계산"""
        if len(trade_returns) < min_trades:
            return 1.0 / self.max_stocks  # 기본 20%

        wins = [r for r in trade_returns if r > 0]
        losses = [abs(r) for r in trade_returns if r < 0]

        if not wins or not losses:
            return 1.0 / self.max_stocks

        p = len(wins) / len(trade_returns)
        avg_win = float(np.mean(wins))
        avg_loss = float(np.mean(losses))

        if avg_loss == 0:
            return 0.25

        b = avg_win / avg_loss
        q = 1.0 - p
        full_kelly = (p * b - q) / b
        if full_kelly <= 0:
            return 0.05  # 최소 비중

        adjusted = full_kelly * self.kelly_fraction
        return float(np.clip(adjusted, 0.05, 0.25))

    def run_backtest(self, df: pd.DataFrame, code: str = "005930") -> Dict[str, Any]:
        """
        단일 종목 또는 멀티 종목 OHLCV 시계열 백테스트 실행
        """
        df_ind = TechnicalIndicators.compute_all_indicators(df)
        df_ind['breakout_level'] = df_ind['open'] + (self.k_breakout * df_ind['atr14'].shift(1).fillna(df_ind['atr14']))

        cash = self.initial_capital
        active_position: Optional[BacktestTrade] = None
        self.closed_trades = []
        self.equity_curve = []
        trade_returns = []

        for idx, row in df_ind.iterrows():
            current_date = row.get('date', row.get('datetime', idx))
            open_p = float(row['open'])
            high_p = float(row['high'])
            low_p = float(row['low'])
            close_p = float(row['close'])
            atr_val = float(row['atr14'])
            breakout_lvl = float(row['breakout_level'])

            # ==========================================
            # 1. 활성 포지션 관리 (출구 전략: 하드스탑 / 샹들리에 / 분할익절)
            # ==========================================
            if active_position is not None:
                # 최고가 갱신
                if high_p > active_position.highest_high:
                    active_position.highest_high = high_p
                    # 샹들리에 트레일링 스탑 상향 래칫
                    new_stop = active_position.highest_high - (self.atr_trailing_stop_mult * atr_val)
                    if active_position.sell_stage >= 1:
                        new_stop = max(active_position.entry_price, new_stop)
                    active_position.trailing_stop = max(active_position.trailing_stop, new_stop)

                # [출구 1] 하드 스탑로스 (진입가 - 2.0*ATR 또는 -5% 하드 캡)
                hard_stop_p = active_position.entry_price - (self.atr_hard_stop_mult * atr_val)
                is_hard_stop = (low_p <= hard_stop_p) or (low_p <= active_position.entry_price * 0.95)

                # [출구 2] 샹들리에 트레일링 스탑
                is_trailing_stop = (low_p <= active_position.trailing_stop) and (active_position.highest_high >= active_position.entry_price * 1.02)

                if is_hard_stop or is_trailing_stop:
                    # 슬리피지 반영 매도 체결가
                    exit_raw = hard_stop_p if is_hard_stop else active_position.trailing_stop
                    exit_raw = min(open_p, exit_raw) if open_p < exit_raw else exit_raw
                    exit_price = exit_raw * (1.0 - self.slippage_rate)

                    sell_val = active_position.shares * exit_price
                    sell_fee = sell_val * (self.sell_fee_rate + self.sell_tax_rate)
                    net_sell_val = sell_val - sell_fee

                    pnl = net_sell_val - (active_position.shares * active_position.entry_price)
                    ret_pct = pnl / (active_position.shares * active_position.entry_price)

                    cash += net_sell_val
                    active_position.exit_date = current_date
                    active_position.exit_price = exit_price
                    active_position.pnl = pnl
                    active_position.return_pct = ret_pct
                    active_position.exit_reason = "하드스탑로스" if is_hard_stop else "샹들리에트레일링스탑"
                    active_position.fee_tax_paid += sell_fee

                    self.closed_trades.append(active_position)
                    trade_returns.append(ret_pct)
                    active_position = None

                # [출구 3] ATR R-배수 분할 익절 (R1: +1.5 ATR 33%, R2: +2.5 ATR 50%)
                elif active_position is not None and atr_val > 0:
                    r1_target = active_position.entry_price + (1.5 * atr_val)
                    r2_target = active_position.entry_price + (2.5 * atr_val)

                    if active_position.sell_stage == 0 and high_p >= r1_target:
                        # 1차 익절 33%
                        partial_qty = max(1, int(active_position.shares * 0.33))
                        exec_price = r1_target * (1.0 - self.slippage_rate)
                        sell_val = partial_qty * exec_price
                        sell_fee = sell_val * (self.sell_fee_rate + self.sell_tax_rate)
                        cash += (sell_val - sell_fee)

                        active_position.shares -= partial_qty
                        active_position.sell_stage = 1
                        active_position.trailing_stop = max(active_position.trailing_stop, active_position.entry_price)  # 본절가 고정
                        active_position.fee_tax_paid += sell_fee

                    elif active_position.sell_stage == 1 and high_p >= r2_target:
                        # 2차 익절 50%
                        partial_qty = max(1, int(active_position.shares * 0.50))
                        exec_price = r2_target * (1.0 - self.slippage_rate)
                        sell_val = partial_qty * exec_price
                        sell_fee = sell_val * (self.sell_fee_rate + self.sell_tax_rate)
                        cash += (sell_val - sell_fee)

                        active_position.shares -= partial_qty
                        active_position.sell_stage = 2
                        active_position.fee_tax_paid += sell_fee

            # ==========================================
            # 2. 신규 진입 (ATR 동적 변동성 돌파)
            # ==========================================
            if active_position is None and not np.isnan(breakout_lvl):
                if high_p >= breakout_lvl:
                    # 슬리피지 반영 진입가
                    entry_raw = max(open_p, breakout_lvl)
                    entry_price = entry_raw * (1.0 + self.slippage_rate)

                    # 프랙셔널 켈리 비중 산출
                    alloc_pct = self._calculate_kelly_allocation(trade_returns)
                    invest_budget = (cash + (active_position.shares * close_p if active_position else 0.0)) * alloc_pct
                    invest_budget = min(invest_budget, cash * 0.95)

                    # 1.5% Risk-at-Risk 한도
                    if atr_val > 0:
                        risk_cap = (cash * 0.015) // (2.0 * atr_val)
                        if risk_cap > 0:
                            invest_budget = min(invest_budget, risk_cap * entry_price)

                    shares = int(invest_budget // entry_price)
                    if shares > 0:
                        buy_val = shares * entry_price
                        buy_fee = buy_val * self.buy_fee_rate
                        if cash >= (buy_val + buy_fee):
                            cash -= (buy_val + buy_fee)
                            initial_stop = entry_price - (self.atr_hard_stop_mult * atr_val)

                            active_position = BacktestTrade(
                                code=code,
                                entry_date=current_date,
                                entry_price=entry_price,
                                shares=shares,
                                initial_stop=initial_stop,
                                trailing_stop=initial_stop,
                                highest_high=high_p,
                                fee_tax_paid=buy_fee
                            )

            # ==========================================
            # 3. 자산 곡선 (Equity Curve) 기록
            # ==========================================
            pos_val = (active_position.shares * close_p) if active_position else 0.0
            total_equity = cash + pos_val
            self.equity_curve.append({
                'date': current_date,
                'equity': total_equity,
                'cash': cash,
                'in_position': 1 if active_position else 0
            })

        return self.compute_metrics()

    def compute_metrics(self) -> Dict[str, Any]:
        """백테스트 성과 지표 산출"""
        if not self.equity_curve:
            return {}

        eq_df = pd.DataFrame(self.equity_curve)
        equity = eq_df['equity']
        returns = equity.pct_change().dropna()

        initial = self.initial_capital
        final = float(equity.iloc[-1])
        total_return = (final - initial) / initial
        n_bars = len(equity)
        cagr = ((1.0 + total_return) ** (252.0 / max(1, n_bars))) - 1.0 if n_bars > 0 else 0.0

        # Sharpe Ratio (연환산)
        std = returns.std()
        sharpe = float((returns.mean() / (std + 1e-9)) * np.sqrt(252.0)) if std > 0 else 0.0

        # Max Drawdown (MDD)
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        mdd = float(drawdown.min())

        # Trades 통계
        trade_rets = [t.return_pct for t in self.closed_trades if t.return_pct is not None]
        wins = [r for r in trade_rets if r > 0]
        losses = [r for r in trade_rets if r <= 0]
        win_rate = (len(wins) / len(trade_rets)) if trade_rets else 0.0

        total_profit = sum(t.pnl for t in self.closed_trades if t.pnl and t.pnl > 0)
        total_loss = abs(sum(t.pnl for t in self.closed_trades if t.pnl and t.pnl < 0))
        profit_factor = (total_profit / total_loss) if total_loss > 0 else (99.0 if total_profit > 0 else 0.0)
        total_fees = sum(t.fee_tax_paid for t in self.closed_trades)

        return {
            'initial_capital': initial,
            'final_equity': final,
            'total_return_pct': total_return * 100.0,
            'cagr_pct': cagr * 100.0,
            'sharpe_ratio': sharpe,
            'mdd_pct': mdd * 100.0,
            'total_trades': len(self.closed_trades),
            'win_rate_pct': win_rate * 100.0,
            'profit_factor': profit_factor,
            'total_fee_tax_paid': total_fees
        }

    def run_walk_forward_optimization(self, df: pd.DataFrame,
                                      k_values: List[float] = [0.4, 0.5, 0.6, 0.7],
                                      in_sample_ratio: float = 0.7) -> Dict[str, Any]:
        """
        Walk-Forward Optimization (WFO) 롤링 검증
        - 70% In-Sample 데이터로 최적 k 도출 후 30% Out-of-Sample 데이터로 과최적화 검증
        """
        n = len(df)
        split_idx = int(n * in_sample_ratio)
        df_in = df.iloc[:split_idx].reset_index(drop=True)
        df_out = df.iloc[split_idx:].reset_index(drop=True)

        best_k = self.k_breakout
        best_sharpe = -999.0
        optimization_results = []

        # 1. In-Sample 최적화
        for k in k_values:
            self.k_breakout = k
            metrics = self.run_backtest(df_in)
            optimization_results.append({
                'k': k,
                'sharpe': metrics.get('sharpe_ratio', 0),
                'return_pct': metrics.get('total_return_pct', 0),
                'mdd_pct': metrics.get('mdd_pct', 0)
            })
            if metrics.get('sharpe_ratio', -999) > best_sharpe:
                best_sharpe = metrics['sharpe_ratio']
                best_k = k

        # 2. Out-of-Sample 검증
        self.k_breakout = best_k
        oos_metrics = self.run_backtest(df_out)

        return {
            'best_k': best_k,
            'in_sample_best_sharpe': best_sharpe,
            'in_sample_results': optimization_results,
            'out_of_sample_metrics': oos_metrics
        }


# 하위 호환성을 위한 엔트리 클래스
BacktestEngine = HighFidelityBacktester
