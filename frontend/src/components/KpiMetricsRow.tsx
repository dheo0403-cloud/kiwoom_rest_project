import React from 'react';
import { Wallet, TrendingUp, TrendingDown, DollarSign, PieChart, ShieldCheck } from 'lucide-react';
import { PortfolioSnapshot } from '../types';

interface KpiMetricsRowProps {
  portfolio: PortfolioSnapshot;
  colorMode: 'KRX' | 'GLOBAL';
}

const KpiMetricsRowComponent: React.FC<KpiMetricsRowProps> = ({ portfolio, colorMode }) => {
  const totalAsset = portfolio.total_asset || 0;
  const currentCapital = portfolio.current_capital || 0;
  const investedCapital = portfolio.invested_capital || (totalAsset - currentCapital);
  const pnl = portfolio.unrealized_pnl || 0;
  const yieldRate = portfolio.total_yield_rate || 0;
  const positions = portfolio.positions || [];
  const stockCount = positions.length > 0 ? positions.length : (portfolio.stock_count || 0);

  const cashRatio = totalAsset > 0 ? (currentCapital / totalAsset) * 100 : 100;
  const investedRatio = 100 - cashRatio;

  const isProfit = pnl >= 0;
  const pnlColor = isProfit
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');
  const pnlBg = isProfit
    ? (colorMode === 'KRX' ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20')
    : (colorMode === 'KRX' ? 'bg-blue-500/10 text-blue-400 border-blue-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20');

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 mb-3.5">
      {/* 1. Total Evaluation Asset */}
      <div className="bento-card p-4 flex flex-col justify-between relative overflow-hidden group">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
            <Wallet className="w-3.5 h-3.5 text-blue-400" />
            총 평가자산
          </span>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/20">
            {portfolio.last_synced_at ? `기준: ${portfolio.last_synced_at}` : '실시간 집계'}
          </span>
        </div>
        <div className="mt-2.5">
          <div className="text-2xl font-black tracking-tight text-white font-mono tabular-nums">
            {Math.round(totalAsset).toLocaleString()}
            <span className="text-sm font-semibold text-slate-400 ml-1">원</span>
          </div>
          <div className="flex items-center gap-2 mt-1.5 text-xs">
            <span className="text-slate-400 text-[11px]">기초 원금:</span>
            <span className="text-slate-300 font-mono text-[11px]">
              {Math.round(currentCapital + investedCapital).toLocaleString()}원
            </span>
          </div>
        </div>
        <div className="absolute top-0 right-0 w-24 h-24 bg-blue-500/5 rounded-full blur-2xl group-hover:bg-blue-500/10 transition" />
      </div>

      {/* 2. Total Unrealized & Daily PnL */}
      <div className="bento-card p-4 flex flex-col justify-between relative overflow-hidden group">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
            {isProfit ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> : <TrendingDown className="w-3.5 h-3.5 text-rose-400" />}
            총 평가손익 & 수익률
          </span>
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${pnlBg} tabular-nums`}>
            {isProfit ? `+${yieldRate.toFixed(2)}%` : `${yieldRate.toFixed(2)}%`}
          </span>
        </div>
        <div className="mt-2.5">
          <div className={`text-2xl font-black tracking-tight font-mono tabular-nums ${pnlColor}`}>
            {isProfit ? `+${Math.round(pnl).toLocaleString()}` : Math.round(pnl).toLocaleString()}
            <span className="text-sm font-semibold ml-1">원</span>
          </div>
          <div className="flex items-center justify-between mt-1.5 text-xs text-slate-400">
            <span>보유 투자금: {Math.round(investedCapital).toLocaleString()}원</span>
            <span className="font-semibold text-slate-300">{stockCount}종목 보유</span>
          </div>
        </div>
        <div className={`absolute top-0 right-0 w-24 h-24 rounded-full blur-2xl transition ${isProfit ? 'bg-emerald-500/5' : 'bg-rose-500/5'}`} />
      </div>

      {/* 3. Cash & Capital Allocation */}
      <div className="bento-card p-4 flex flex-col justify-between relative overflow-hidden group">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
            <DollarSign className="w-3.5 h-3.5 text-amber-400" />
            예수금 (주문가능 현금)
          </span>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/20 font-mono">
            현금 {cashRatio.toFixed(1)}%
          </span>
        </div>
        <div className="mt-2.5">
          <div className="text-2xl font-black tracking-tight text-white font-mono tabular-nums">
            {Math.round(currentCapital).toLocaleString()}
            <span className="text-sm font-semibold text-slate-400 ml-1">원</span>
          </div>
          {/* Allocation Progress Bar */}
          <div className="mt-2">
            <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden flex">
              <div
                className="bg-gradient-to-r from-blue-500 to-indigo-500 h-full transition-all duration-500"
                style={{ width: `${Math.min(100, Math.max(0, investedRatio))}%` }}
                title={`주식 비중: ${investedRatio.toFixed(1)}%`}
              />
              <div
                className="bg-slate-700 h-full transition-all duration-500"
                style={{ width: `${Math.min(100, Math.max(0, cashRatio))}%` }}
                title={`현금 비중: ${cashRatio.toFixed(1)}%`}
              />
            </div>
          </div>
        </div>
      </div>

      {/* 4. Risk & Target Allocation */}
      <div className="bento-card p-4 flex flex-col justify-between relative overflow-hidden group">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-indigo-400" />
            리스크 관리 & 포지션 한도
          </span>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-400 border border-indigo-500/20">
            MDD -5% 한도
          </span>
        </div>
        <div className="mt-2.5">
          <div className="flex items-baseline justify-between">
            <div className="text-2xl font-black tracking-tight text-white font-mono tabular-nums">
              {stockCount} <span className="text-sm font-normal text-slate-400">/ 5종목</span>
            </div>
            <div className="text-xs font-semibold text-slate-300">
              최대 20% 분산
            </div>
          </div>
          <div className="flex items-center justify-between mt-2 text-[11px] text-slate-400">
            <span>하드 스탑로스: <strong className="text-rose-400">-4.0%</strong></span>
            <span>트레일링: <strong className="text-amber-400">2.5 ATR</strong></span>
          </div>
        </div>
      </div>
    </div>
  );
};

export const KpiMetricsRow = React.memo(KpiMetricsRowComponent, (prev, next) => {
  return (
    prev.portfolio.total_asset === next.portfolio.total_asset &&
    prev.portfolio.current_capital === next.portfolio.current_capital &&
    prev.portfolio.unrealized_pnl === next.portfolio.unrealized_pnl &&
    prev.portfolio.total_yield_rate === next.portfolio.total_yield_rate &&
    (prev.portfolio.positions?.length || 0) === (next.portfolio.positions?.length || 0) &&
    prev.colorMode === next.colorMode
  );
});
KpiMetricsRow.displayName = 'KpiMetricsRow';
