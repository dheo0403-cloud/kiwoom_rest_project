"""
고충실도(High-Fidelity) 퀀트 트레이딩 백테스팅 및 Walk-Forward 최적화 엔진
- 실거래와 동일한 판정 코드 사용: strategy.py(매수/매도 시그널), async_portfolio.py(켈리 주문 수량)
- 실거래 입력 재현: 최근 20개 분봉 지표 + 당일 시가 + 전일까지 20일 피보나치/평균거래량 + 당일 누적거래량
- 현실적 마찰비용 모델링: 매수/매도 수수료(0.015%x2) + 증권거래세(0.18%) = 0.21%, 체결 슬리피지(0.08%) 반영
- 비관적 체결: 진입은 봉 종가, 청산 판정·체결은 봉 저가 기준
- In-Sample (70%) vs Out-of-Sample (30%) Walk-Forward Optimization (WFO) 지원
- 샤프 지수(Sharpe Ratio), 최대 낙폭(MDD), 손익비(Profit Factor), 승률(Win Rate) 정밀 산출
- 데이터 한계: 체결강도·호가잔량·시장 레짐(KODEX200)은 분봉 OHLCV에 없어 해당 필터는 미적용
"""
import asyncio
import dataclasses
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd
from indicators import TechnicalIndicators
from strategy import AdaptiveVolatilityBreakoutStrategy
from async_portfolio import AsyncPortfolioManager

BUFFER_BARS = 20       # 실거래 버퍼 조회 개수 (buffer.get_dataframe(limit=20))
CONTEXT_DAYS = 20      # 실거래 관심종목 일봉 조회 개수 (chart_items[:20])
PARTIAL_SELL_RATIO = 0.5  # 실거래 SELL_PARTIAL 매도 비율


@dataclasses.dataclass
class BacktestTrade:
    code: str
    entry_date: Any
    entry_price: float
    shares: int
    cost_basis: float          # 매수 금액 + 매수 수수료
    highest_high: float
    sell_stage: int = 0
    realized: float = 0.0      # 분할 매도 순수령액 누적
    exit_date: Optional[Any] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    return_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    fee_tax_paid: float = 0.0


class HighFidelityBacktester:
    """
    고충실도 퀀트 트레이딩 이벤트 기반 백테스터 (분봉 입력, 단일 종목·1포지션)
    """
    def __init__(
        self,
        initial_capital: float = 10_000_000.0,
        k_breakout: float = 0.5,
        kelly_fraction: float = 0.4,
        max_stocks: int = 5,
        slippage_rate: float = 0.0008,    # 0.08% 호가 슬리피지
        buy_fee_rate: float = 0.00015,     # 0.015% 매수 수수료
        sell_fee_rate: float = 0.00015,    # 0.015% 매도 수수료
        sell_tax_rate: float = 0.0018,     # 0.18% 증권거래세
        strategy: Optional[AdaptiveVolatilityBreakoutStrategy] = None
    ):
        self.initial_capital = initial_capital
        self.strategy = strategy or AdaptiveVolatilityBreakoutStrategy(k_breakout=k_breakout)
        self.kelly_fraction = kelly_fraction
        self.max_stocks = max_stocks
        self.slippage_rate = slippage_rate
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.sell_tax_rate = sell_tax_rate

        self.closed_trades: List[BacktestTrade] = []
        self.equity_curve: List[Dict[str, Any]] = []

    @property
    def k_breakout(self) -> float:
        return self.strategy.k_breakout

    @k_breakout.setter
    def k_breakout(self, value: float):
        self.strategy.k_breakout = value

    @staticmethod
    def _daily_context(daily: pd.DataFrame, day_idx: int) -> Dict[str, float]:
        """전일까지 최근 20일 일봉으로 관심종목 기준값 산출 (main_rest_async.update_watchlist와 동일 공식)"""
        prior = daily.iloc[max(0, day_idx - CONTEXT_DAYS):day_idx]
        if prior.empty:
            return {}
        period_high = float(prior['high'].max())
        period_low = float(prior['low'].min())
        diff = period_high - period_low
        if diff <= 0:
            return {}
        return {
            'period_high': period_high,
            'period_low': period_low,
            'fib_382': period_high - diff * 0.382,
            'fib_500': period_high - diff * 0.500,
            'fib_618': period_high - diff * 0.618,
            'avg_vol': float(prior['volume'].mean()),
        }

    def _close_trade(self, pos: BacktestTrade, shares: int, raw_price: float, date: Any) -> float:
        """매도 체결 처리 후 순수령액 반환 (전량이면 거래 확정)"""
        exit_price = raw_price * (1.0 - self.slippage_rate)
        sell_val = shares * exit_price
        sell_fee = sell_val * (self.sell_fee_rate + self.sell_tax_rate)
        pos.fee_tax_paid += sell_fee
        pos.shares -= shares
        net = sell_val - sell_fee
        pos.realized += net
        if pos.shares <= 0:
            pos.exit_date = date
            pos.exit_price = exit_price
            pos.pnl = pos.realized - pos.cost_basis
            pos.return_pct = pos.pnl / pos.cost_basis
        return net

    def run_backtest(self, df: pd.DataFrame, code: str = "005930") -> Dict[str, Any]:
        """
        분봉 OHLCV 시계열 백테스트 실행 (datetime 또는 date 컬럼에 봉 시각 필요)
        """
        return asyncio.run(self._run(df, code))

    async def _run(self, df: pd.DataFrame, code: str) -> Dict[str, Any]:
        bars = TechnicalIndicators._standardize_columns(df).reset_index(drop=True)
        time_col = 'datetime' if 'datetime' in bars.columns else 'date'
        times = pd.to_datetime(bars[time_col])
        days = times.dt.date
        daily = bars.groupby(days).agg(open=('open', 'first'), high=('high', 'max'),
                                       low=('low', 'min'), volume=('volume', 'sum'))
        day_index = {d: i for i, d in enumerate(daily.index)}

        portfolio = AsyncPortfolioManager(initial_capital=self.initial_capital,
                                          max_stocks=self.max_stocks, kelly_fraction=self.kelly_fraction)
        cash = self.initial_capital
        pos: Optional[BacktestTrade] = None
        self.closed_trades = []
        self.equity_curve = []
        cur_day, context, acml_vol = None, {}, 0.0

        for i in range(len(bars)):
            row = bars.iloc[i]
            now = times.iloc[i].to_pydatetime()
            high_p, low_p, close_p = float(row['high']), float(row['low']), float(row['close'])

            if days.iloc[i] != cur_day:
                cur_day = days.iloc[i]
                context = self._daily_context(daily, day_index[cur_day])
                acml_vol = 0.0
            acml_vol += float(row['volume'])

            # 실거래와 동일하게 최근 20개 분봉으로 지표 산출
            window = bars.iloc[max(0, i - BUFFER_BARS + 1):i + 1]
            ind = TechnicalIndicators.get_latest_indicators(window)
            ind['now'] = now

            # 1. 보유 포지션 청산 판정 (봉 저가 기준 비관적 체결)
            if pos is not None:
                pos.highest_high = max(pos.highest_high, high_p)
                action, reason = await self.strategy.check_sell_signal(
                    code=code, buy_price=pos.entry_price, current_price=low_p,
                    ind=ind, sell_stage=pos.sell_stage, highest_price=pos.highest_high
                )
                if action == "SELL_PARTIAL":
                    sell_qty = max(1, int(pos.shares * PARTIAL_SELL_RATIO))
                    if sell_qty < pos.shares:
                        cash += self._close_trade(pos, sell_qty, low_p, now)
                        pos.sell_stage += 1
                    else:
                        action = "SELL_ALL"
                if action == "SELL_ALL":
                    cash += self._close_trade(pos, pos.shares, low_p, now)
                    pos.exit_reason = reason
                    self.closed_trades.append(pos)
                    await portfolio.record_closed_trade(pos.return_pct)
                    pos = None

            # 2. 신규 진입 판정 (실거래 _evaluate_buy_condition_impl과 동일 입력, 봉 종가 체결)
            elif not np.isnan(close_p):
                ind.update(context)
                ind['high10'] = context.get('period_high') or close_p
                ind['open'] = float(daily.at[cur_day, 'open'])
                ind['acml_vol'] = acml_vol
                fib_382, fib_618 = context.get('fib_382', 0), context.get('fib_618', 0)
                ind['fib_rebound'] = (fib_618 <= close_p <= fib_382) if (fib_618 > 0 and fib_382 > 0) else False

                buy_signal, _ = await self.strategy.check_buy_signal(
                    code=code, current_price=close_p, current_volume=float(row['volume']), ind=ind
                )
                if buy_signal:
                    entry_price = close_p * (1.0 + self.slippage_rate)
                    shares = await portfolio.get_order_qty(entry_price, atr=ind.get('atr14', 0), available_cash=cash)
                    buy_val = shares * entry_price
                    buy_fee = buy_val * self.buy_fee_rate
                    if shares > 0 and cash >= buy_val + buy_fee:
                        cash -= buy_val + buy_fee
                        pos = BacktestTrade(code=code, entry_date=now, entry_price=entry_price, shares=shares,
                                            cost_basis=buy_val + buy_fee, highest_high=entry_price,
                                            fee_tax_paid=buy_fee)

            # 3. 자산 곡선 (Equity Curve) 기록
            pos_val = (pos.shares * close_p) if pos else 0.0
            self.equity_curve.append({
                'date': now,
                'equity': cash + pos_val,
                'cash': cash,
                'in_position': 1 if pos else 0
            })

        return self.compute_metrics()

    def compute_metrics(self) -> Dict[str, Any]:
        """백테스트 성과 지표 산출 (수익률·샤프는 일별 종가 자산 기준 연환산)"""
        if not self.equity_curve:
            return {}

        eq_df = pd.DataFrame(self.equity_curve)
        equity = eq_df['equity']
        daily_equity = eq_df.groupby(pd.to_datetime(eq_df['date']).dt.date)['equity'].last()
        returns = daily_equity.pct_change().dropna()

        initial = self.initial_capital
        final = float(equity.iloc[-1])
        total_return = (final - initial) / initial
        n_days = len(daily_equity)
        cagr = ((1.0 + total_return) ** (252.0 / max(1, n_days))) - 1.0 if n_days > 0 else 0.0

        # Sharpe Ratio (연환산)
        std = returns.std()
        sharpe = float((returns.mean() / (std + 1e-9)) * np.sqrt(252.0)) if std > 0 else 0.0

        # Max Drawdown (MDD) - 봉 단위
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        mdd = float(drawdown.min())

        # Trades 통계
        trade_rets = [t.return_pct for t in self.closed_trades if t.return_pct is not None]
        wins = [r for r in trade_rets if r > 0]
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
