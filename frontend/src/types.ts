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

export interface PortfolioSnapshot {
  total_asset: number;
  current_capital: number;
  invested_capital: number;
  stock_count: number;
  unrealized_pnl: number;
  total_yield_rate: number;
  positions: Position[];
  timestamp?: string;
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
  level: 'TRADE' | 'CRITICAL' | 'WARNING' | 'INFO' | 'SYSTEM' | 'ERROR' | 'MANUAL_ORDER';
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

