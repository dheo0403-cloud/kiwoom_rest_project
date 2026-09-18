import React from 'react';
import {
  TrendingUp,
  TrendingDown,
  Percent,
  ShieldAlert,
  BarChart3,
  Globe,
  Gauge,
  Zap,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  ShieldCheck,
  Activity,
  History
} from 'lucide-react';
import { QuantPerformanceMetrics, MacroStatus, ClosedTrade } from '../types';

interface QuantPerformanceBentoProps {
  performance: QuantPerformanceMetrics;
  macroStatus: MacroStatus;
  colorMode: 'KRX' | 'GLOBAL';
}

export const QuantPerformanceBento: React.FC<QuantPerformanceBentoProps> = ({
  performance,
  macroStatus,
  colorMode
}) => {
  const isDailyProfit = performance.daily_return_pct >= 0;
  const isCumProfit = performance.cumulative_return_pct >= 0;

  const dailyColor = isDailyProfit
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');

  const cumColor = isCumProfit
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');

  const isWinRateGood = performance.win_rate_pct >= 50.0;
  const isPfGood = performance.profit_factor >= 1.2;

  // Macro Regime Badge & Color Mapping
  const regime = macroStatus.regime || 'BULL_TREND';
  let regimeLabel = 'BULL_TREND (강세장)';
  let regimeColorClass = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  let regimeDotClass = 'bg-emerald-400 animate-pulse';

  if (regime === 'NEUTRAL_RANGE') {
    regimeLabel = 'NEUTRAL_RANGE (횡보/조정)';
    regimeColorClass = 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    regimeDotClass = 'bg-amber-400';
  } else if (regime === 'PANIC_CRASH') {
    regimeLabel = 'PANIC_CRASH (급락/매수차단)';
    regimeColorClass = 'bg-rose-500/20 text-rose-400 border-rose-500/40';
    regimeDotClass = 'bg-rose-500 animate-ping';
  }

  // Micro Indicators (호가 불균형 & 체결강도)
  const ob = macroStatus.orderbook_imbalance || {
    imbalance_ratio: 0.25,
    total_bid_qty: 250000,
    total_ask_qty: 150000,
    bid_ask_spread: 100
  };
  const totalDepth = (ob.total_bid_qty + ob.total_ask_qty) || 1;
  const bidRatio = Math.min(100, Math.max(0, (ob.total_bid_qty / totalDepth) * 100));
  const askRatio = 100 - bidRatio;
  const isBidDominant = ob.imbalance_ratio >= 0;

  const volumePower = macroStatus.volume_power || 100.0;
  const isStrongVolume = volumePower >= 120.0;

  const recentTrades: ClosedTrade[] = performance.recent_closed_trades || [];

  return (
    <div className="bento-card p-4 flex flex-col bg-slate-900/80 border border-slate-800 mb-3.5 relative overflow-hidden">
      {/* 1. Header Bar: Title & Macro Regime Badge */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-blue-500/10 text-blue-400">
            <BarChart3 className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              퀀트 전략 성과 & 실시간 시장 국면 (Quant Performance & Macro Regime)
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                4대 퀀트 엔진 실시간 집계
              </span>
            </h2>
          </div>
        </div>

        {/* Macro Regime Status Badge */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 hidden sm:inline">Macro Regime:</span>
          <div className={`px-2.5 py-1 rounded-full text-xs font-bold border flex items-center gap-1.5 shadow-sm ${regimeColorClass}`}>
            <span className={`w-2 h-2 rounded-full ${regimeDotClass}`} />
            <span>{regimeLabel}</span>
          </div>
        </div>
      </div>

      {/* 2. Top 6 KPI Metric Cards Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 my-3">
        {/* KPI 1: 일일 수익률 */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              {isDailyProfit ? <ArrowUpRight className="w-3.5 h-3.5 text-rose-400" /> : <ArrowDownRight className="w-3.5 h-3.5 text-blue-400" />}
              일일 수익률
            </span>
            <span className="text-[9px] text-slate-500">당일</span>
          </div>
          <div className="mt-1.5">
            <div className={`text-lg sm:text-xl font-black font-mono tabular-nums ${dailyColor}`}>
              {isDailyProfit ? '+' : ''}{performance.daily_return_pct.toFixed(2)}%
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              실현손익 반영
            </div>
          </div>
        </div>

        {/* KPI 2: 누적 수익률 */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              <TrendingUp className="w-3.5 h-3.5 text-indigo-400" />
              누적 수익률
            </span>
            <span className="text-[9px] text-slate-500">전체</span>
          </div>
          <div className="mt-1.5">
            <div className={`text-lg sm:text-xl font-black font-mono tabular-nums ${cumColor}`}>
              {isCumProfit ? '+' : ''}{performance.cumulative_return_pct.toFixed(2)}%
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              기초원금 대비
            </div>
          </div>
        </div>

        {/* KPI 3: 승률 (Win Rate) */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              <Percent className="w-3.5 h-3.5 text-emerald-400" />
              전략 승률
            </span>
            <span className={`text-[9px] font-bold px-1 rounded ${isWinRateGood ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-400'}`}>
              {performance.winning_trades}승 {performance.losing_trades}패
            </span>
          </div>
          <div className="mt-1.5">
            <div className={`text-lg sm:text-xl font-black font-mono tabular-nums ${isWinRateGood ? 'text-emerald-400' : 'text-amber-400'}`}>
              {performance.win_rate_pct.toFixed(1)}%
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              총 {performance.total_trades}회 청산
            </div>
          </div>
        </div>

        {/* KPI 4: 최대 낙폭 (MDD) */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
              최대 낙폭(MDD)
            </span>
            <span className="text-[9px] text-slate-500">한도 -5%</span>
          </div>
          <div className="mt-1.5">
            <div className="text-lg sm:text-xl font-black font-mono tabular-nums text-rose-400">
              {performance.mdd_pct.toFixed(2)}%
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              최고점 대비 하락
            </div>
          </div>
        </div>

        {/* KPI 5: 손익비 (Profit Factor) */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              <Layers className="w-3.5 h-3.5 text-purple-400" />
              손익비 (PF)
            </span>
            <span className="text-[9px] text-slate-500">목표 1.5+</span>
          </div>
          <div className="mt-1.5">
            <div className={`text-lg sm:text-xl font-black font-mono tabular-nums ${isPfGood ? 'text-emerald-400' : 'text-slate-200'}`}>
              {performance.profit_factor.toFixed(2)}
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              총이익/총손실
            </div>
          </div>
        </div>

        {/* KPI 6: 켈리 자산 배분 비중 & 매수 상태 */}
        <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="flex items-center gap-1">
              <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
              켈리 배분 비중
            </span>
            <span className={`text-[9px] font-bold px-1 rounded ${macroStatus.is_buy_allowed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
              {macroStatus.is_buy_allowed ? '진입 허용' : '매수 차단'}
            </span>
          </div>
          <div className="mt-1.5">
            <div className="text-lg sm:text-xl font-black font-mono tabular-nums text-cyan-300">
              {(macroStatus.kelly_multiplier * 20).toFixed(0)}%
              <span className="text-xs font-normal text-slate-400 ml-1">/ 종목당</span>
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              레짐 승수: {macroStatus.kelly_multiplier.toFixed(1)}x
            </div>
          </div>
        </div>
      </div>

      {/* 3. Sub Bottom Section: Micro Market Gauge & Recent Trades Feed */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 mt-1 pt-3 border-t border-white/5">
        {/* Left (6/12): Real-time Orderbook Imbalance & Volume Power Gauge */}
        <div className="lg:col-span-6 bg-slate-950/60 p-3 rounded-lg border border-slate-800 flex flex-col justify-between space-y-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-slate-300 flex items-center gap-1.5">
              <Gauge className="w-3.5 h-3.5 text-cyan-400" />
              실시간 호가 잔량 불균형 & 체결강도
            </span>
            <span className="text-[10px] text-slate-400">
              KODEX 200: <strong className={macroStatus.kodex200_change_rate >= 0 ? 'text-rose-400' : 'text-blue-400'}>
                {macroStatus.kodex200_change_rate >= 0 ? '+' : ''}{macroStatus.kodex200_change_rate.toFixed(2)}%
              </strong>
            </span>
          </div>

          {/* Orderbook Imbalance Bar */}
          <div>
            <div className="flex justify-between text-[11px] mb-1">
              <span className="text-slate-400">
                매수 잔량: <strong className="text-rose-400 font-mono">{Math.round(ob.total_bid_qty).toLocaleString()}</strong>주 ({bidRatio.toFixed(1)}%)
              </span>
              <span className={`font-bold font-mono ${isBidDominant ? 'text-rose-400' : 'text-blue-400'}`}>
                불균형: {ob.imbalance_ratio >= 0 ? '+' : ''}{ob.imbalance_ratio.toFixed(2)} ({isBidDominant ? '매수 지지' : '매도 우세'})
              </span>
              <span className="text-slate-400">
                매도 잔량: <strong className="text-blue-400 font-mono">{Math.round(ob.total_ask_qty).toLocaleString()}</strong>주 ({askRatio.toFixed(1)}%)
              </span>
            </div>
            <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden flex">
              <div
                className="bg-gradient-to-r from-rose-500 to-red-500 h-full transition-all duration-500"
                style={{ width: `${bidRatio}%` }}
                title={`매수 잔량: ${bidRatio.toFixed(1)}%`}
              />
              <div
                className="bg-gradient-to-r from-blue-600 to-indigo-500 h-full transition-all duration-500"
                style={{ width: `${askRatio}%` }}
                title={`매도 잔량: ${askRatio.toFixed(1)}%`}
              />
            </div>
          </div>

          {/* Volume Power Indicator */}
          <div className="flex items-center justify-between pt-1 text-xs">
            <span className="text-slate-400 flex items-center gap-1 text-[11px]">
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              실시간 체결강도:
              <strong className={`ml-1 font-mono text-xs ${isStrongVolume ? 'text-amber-300 font-extrabold' : 'text-slate-200'}`}>
                {volumePower.toFixed(1)}%
              </strong>
            </span>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${isStrongVolume ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-slate-800 text-slate-400'}`}>
              {isStrongVolume ? '🔥 수급 모멘텀 급증' : '보통 수급'}
            </span>
          </div>
        </div>

        {/* Right (6/12): Recent Completed Trades Feed (최근 완료 매매) */}
        <div className="lg:col-span-6 bg-slate-950/60 p-3 rounded-lg border border-slate-800 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="font-bold text-slate-300 flex items-center gap-1.5">
              <History className="w-3.5 h-3.5 text-purple-400" />
              최근 퀀트 청산 타점 성과 (Recent Closed Trades)
            </span>
            <span className="text-[10px] text-slate-400 font-mono">
              누적 이익: <strong className="text-rose-400">+{Math.round(performance.total_profit).toLocaleString()}원</strong>
            </span>
          </div>

          <div className="space-y-1.5 max-h-24 overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-slate-800">
            {recentTrades.length === 0 ? (
              <div className="py-2 text-center text-slate-500 text-[11px]">
                최근 청산된 매매 내역이 없습니다.
              </div>
            ) : (
              recentTrades.slice(0, 3).map((trade, idx) => {
                const isWin = trade.pnl >= 0;
                const pColor = isWin
                  ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
                  : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');

                return (
                  <div
                    key={`${trade.code}-${idx}`}
                    className="p-1.5 bg-slate-900/90 rounded border border-slate-800/80 flex items-center justify-between text-[11px]"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="font-bold text-white">{trade.name}</span>
                      <span className="text-[9px] text-slate-500 font-mono">({trade.code})</span>
                      <span className="text-[9px] text-slate-400 ml-1">
                        {trade.qty}주 @ {Math.round(trade.sell_price).toLocaleString()}원
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className={`font-bold font-mono ${pColor}`}>
                        {isWin ? '+' : ''}{Math.round(trade.pnl).toLocaleString()}원 ({isWin ? '+' : ''}{trade.return_pct.toFixed(2)}%)
                      </span>
                      <span className="text-[9px] text-slate-500 font-mono">
                        {trade.timestamp}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
