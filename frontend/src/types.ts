export interface Position {
  code: string;
  name: string;
  qty: number;
  buy_price: number;
  current_price: number;
  highest_price?: number;
  sell_stage?: number;
  pnl?: number;
  yield_rate?: number;
}

export interface ClosedTrade {
  code: string;
  name: string;
  buy_price: number;
  sell_price: number;
  qty: number;
  pnl: number;
  return_pct: number;
  timestamp: string;
}

export interface EquityHistoryPoint {
  date: string;
  total_asset: number;
  deposit: number;
  profit_loss: number;
  yield: number;
}

export interface QuantPerformanceMetrics {
  daily_return_pct: number;
  cumulative_return_pct: number;
  win_rate_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  mdd_pct: number;
  profit_factor: number;
  total_profit: number;
  total_loss: number;
  recent_closed_trades: ClosedTrade[];
  equity_history: EquityHistoryPoint[];
}

export interface MacroStatus {
  regime: 'BULL_TREND' | 'NEUTRAL_RANGE' | 'PANIC_CRASH' | string;
  regime_reason?: string;
  kodex200_change_rate: number;
  vix_value?: number;
  usdkrw_change_pct?: number;
  market_filter_passed: boolean;
  kelly_multiplier: number;
  is_buy_allowed: boolean;
  target_code?: string;
  orderbook_imbalance?: {
    imbalance_ratio: number;
    total_bid_qty: number;
    total_ask_qty: number;
    bid_ask_spread: number;
  };
  volume_power?: number;
  evaluated_at?: string;
}

export interface PortfolioSnapshot {
  total_asset: number;
  current_capital: number;
  invested_capital: number;
  stock_count: number;
  unrealized_pnl: number;
  total_yield_rate: number;
  positions: Position[];
  timestamp?: string;
  last_synced_at?: string;
  quant_performance?: QuantPerformanceMetrics;
  macro_status?: MacroStatus;
}

export interface WatchlistItem {
  code: string;
  name: string;
  current_price: number;
  volume: number;
  trading_value?: number;
  period_high?: number;
  period_low?: number;
  fib_382?: number;
  fib_500?: number;
  fib_618?: number;
  fluct_rate?: number;
  status: string;
  updated_at?: string;
}

export interface LogMessage {
  id?: string;
  timestamp: string;
  level: 'TRADE' | 'CRITICAL' | 'WARNING' | 'INFO' | 'SYSTEM' | 'ERROR' | 'MANUAL_ORDER' | 'WATCH';
  message: string;
}

export interface BotStatus {
  running: boolean;
  is_demo: boolean;
  market_filter_passed: boolean;
  kodex200_change_rate: number;
  watchlist_count: number;
  active_positions_count: number;
  circuit_breaker_open: boolean;
}

export interface CandleData {
  time: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface ChartResponse {
  code: string;
  name: string;
  current_price: number;
  period_high: number;
  period_low: number;
  fib_382: number;
  fib_500: number;
  fib_618: number;
  candles: CandleData[];
}

