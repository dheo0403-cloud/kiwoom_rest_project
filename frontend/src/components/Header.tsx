import React, { useState, useRef, useEffect } from 'react';
import {
  Activity, ShieldAlert, Zap, RefreshCw, Sliders, CheckCircle2,
  AlertTriangle, Radio, TrendingUp, TrendingDown, Wallet, DollarSign,
  Package, ChevronDown, ChevronUp, ExternalLink
} from 'lucide-react';
import { BotStatus, PortfolioSnapshot } from '../types';

interface HeaderProps {
  botStatus: BotStatus;
  portfolio: PortfolioSnapshot;
  isConnected: boolean;
  onOpenKillSwitch: () => void;
  onOpenParams: () => void;
  onRefresh: () => void;
  colorMode: 'KRX' | 'GLOBAL';
  onToggleColorMode: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  botStatus,
  portfolio,
  isConnected,
  onOpenKillSwitch,
  onOpenParams,
  onRefresh,
  colorMode,
  onToggleColorMode
}) => {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isPositionsDropdownOpen, setIsPositionsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const handleRefreshClick = () => {
    setIsRefreshing(true);
    onRefresh();
    setTimeout(() => setIsRefreshing(false), 600);
  };

  // Outside click listener for positions dropdown
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsPositionsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const totalAsset = portfolio.total_asset || 0;
  const currentCapital = portfolio.current_capital || 0;
  const positions = portfolio.positions || [];
  const stockCount = positions.length;

  const isDemo = botStatus.is_demo;

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 px-4 sm:px-6 py-2.5 bg-slate-900/95 border-b border-white/10 backdrop-blur-md sticky top-0 z-40 select-none">
      {/* 1. Left: Brand & Mode Badges */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Zap className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-sm sm:text-base font-black text-white tracking-tight flex items-center gap-1.5">
              KIWOOM QUANT COCKPIT
              <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                v2.0
              </span>
            </h1>
          </div>
        </div>

        {/* Live WS Status Badge */}
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800/90 border border-slate-700/60 text-xs">
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'}`} />
          <span className="text-slate-300 font-semibold text-[10px]">
            {isConnected ? 'LIVE WS' : 'DISCONNECTED'}
          </span>
        </div>

        {/* Real / Mock Mode Badge */}
        <div className={`px-2.5 py-1 rounded-md text-[11px] font-black tracking-wider border flex items-center gap-1.5 shadow-sm ${
          !isDemo
            ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40 shadow-emerald-950/40'
            : 'bg-amber-500/15 text-amber-400 border-amber-500/40 shadow-amber-950/40'
        }`}>
          <Radio className="w-3 h-3 animate-pulse" />
          {!isDemo ? 'LIVE (실전투자)' : 'MOCK (모의투자)'}
        </div>
      </div>

      {/* 2. Center: [총자산 | D+2 예수금 | 보유 종목] 실시간 계좌 잔고 & 포지션 뱃지 바 */}
      <div className="flex items-center gap-2 relative" ref={dropdownRef}>
        <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-950/90 border border-slate-700/80 rounded-xl shadow-inner font-mono text-xs">
          {/* 총자산 */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400 flex items-center gap-1 text-[11px]">
              <Wallet className="w-3.5 h-3.5 text-blue-400" />
              총자산:
            </span>
            <span className="font-extrabold text-white text-xs sm:text-sm tabular-nums">
              {Math.round(totalAsset).toLocaleString()}
              <span className="text-[10px] text-slate-400 font-normal ml-0.5">원</span>
            </span>
          </div>

          <span className="text-slate-700">|</span>

          {/* D+2 주문가능 예수금 */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400 flex items-center gap-1 text-[11px]">
              <DollarSign className="w-3.5 h-3.5 text-amber-400" />
              D+2 예수금:
            </span>
            <span className="font-extrabold text-amber-300 text-xs sm:text-sm tabular-nums">
              {Math.round(currentCapital).toLocaleString()}
              <span className="text-[10px] text-slate-400 font-normal ml-0.5">원</span>
            </span>
          </div>

          <span className="text-slate-700">|</span>

          {/* 보유 포지션 드롭다운 버튼 */}
          <button
            onClick={() => setIsPositionsDropdownOpen(prev => !prev)}
            className={`flex items-center gap-1.5 px-2 py-0.5 rounded-lg text-[11px] font-bold transition border ${
              stockCount > 0
                ? 'bg-blue-600/20 text-blue-300 border-blue-500/40 hover:bg-blue-600/30'
                : 'bg-slate-800/80 text-slate-400 border-slate-700 hover:text-white'
            }`}
            title="클릭 시 현재 보유 중인 주식 포지션 상세 목록 확인"
          >
            <Package className="w-3.5 h-3.5 text-cyan-400" />
            <span>보유: <strong className="text-white">{stockCount}</strong>개</span>
            {isPositionsDropdownOpen ? <ChevronUp className="w-3 h-3 text-slate-400" /> : <ChevronDown className="w-3 h-3 text-slate-400" />}
          </button>
        </div>

        {/* 2-1. 보유 포지션 상세 인터랙티브 드롭다운 팝오버 */}
        {isPositionsDropdownOpen && (
          <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-80 sm:w-96 bg-slate-900/95 border border-slate-700 rounded-xl shadow-2xl p-3 z-50 backdrop-blur-xl font-mono animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between pb-2 border-b border-white/10 text-xs">
              <span className="font-bold text-slate-200 flex items-center gap-1.5">
                <Package className="w-4 h-4 text-cyan-400" />
                현재 계좌 보유 포지션 ({stockCount}개)
              </span>
              <span className="text-[10px] text-slate-400">실시간 체결단가 기준</span>
            </div>

            <div className="mt-2 max-h-60 overflow-y-auto space-y-2 pr-1 scrollbar-thin scrollbar-thumb-slate-700">
              {stockCount === 0 ? (
                <div className="py-6 text-center text-slate-500 text-xs">
                  <p>현재 보유 중인 포지션이 없습니다.</p>
                  <p className="text-[10px] text-slate-600 mt-1">100% 현금 보유 상태 (타점 감시 대기)</p>
                </div>
              ) : (
                positions.map((pos) => {
                  const pnl = pos.pnl || (pos.current_price - pos.buy_price) * pos.qty;
                  const isProfit = pnl >= 0;
                  const yieldRate = pos.yield_rate || ((pos.current_price / pos.buy_price) - 1) * 100;
                  const pnlColor = isProfit
                    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
                    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');

                  return (
                    <div
                      key={pos.code}
                      className="p-2 bg-slate-950/80 rounded-lg border border-slate-800 hover:border-slate-700 transition flex items-center justify-between text-xs"
                    >
                      <div>
                        <div className="font-bold text-white flex items-center gap-1.5">
                          <span>{pos.name}</span>
                          <span className="text-[10px] text-slate-400">({pos.code})</span>
                        </div>
                        <div className="text-[11px] text-slate-400 mt-0.5">
                          {pos.qty}주 @ {Math.round(pos.buy_price).toLocaleString()}원
                        </div>
                      </div>

                      <div className="text-right">
                        <div className="font-bold text-slate-200">
                          {Math.round(pos.current_price).toLocaleString()}원
                        </div>
                        <div className={`text-[11px] font-semibold ${pnlColor}`}>
                          {isProfit ? '+' : ''}{Math.round(pnl).toLocaleString()}원 ({isProfit ? '+' : ''}{yieldRate.toFixed(2)}%)
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* 3. Right Controls: Color Toggle, Params, Refresh, Emergency Kill-Switch */}
      <div className="flex items-center gap-2">
        {/* Color Mode Toggle */}
        <button
          onClick={onToggleColorMode}
          title="한국식(KRX)/글로벌 색상 토글"
          className="px-2.5 py-1.5 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 font-medium transition flex items-center gap-1.5"
        >
          <span className="text-[10px] px-1 py-0.5 rounded bg-slate-900 text-slate-400">{colorMode}</span>
        </button>

        {/* Quant Parameters Button */}
        <button
          onClick={onOpenParams}
          className="p-1.5 sm:px-3 sm:py-1.5 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-medium transition flex items-center gap-1.5"
        >
          <Sliders className="w-3.5 h-3.5 text-indigo-400" />
          <span className="hidden sm:inline">파라미터 튜닝</span>
        </button>

        {/* Refresh Button */}
        <button
          onClick={handleRefreshClick}
          className="p-1.5 sm:px-2.5 sm:py-1.5 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition"
          title="데이터 새로고침"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-blue-400' : ''}`} />
        </button>

        {/* 🚨 Emergency Kill-Switch Button */}
        <button
          onClick={onOpenKillSwitch}
          className="px-3.5 py-1.5 text-xs font-bold rounded-lg bg-gradient-to-r from-rose-600 to-red-700 hover:from-rose-500 hover:to-red-600 text-white shadow-lg shadow-rose-900/30 border border-rose-500/50 flex items-center gap-1.5 transition active:scale-95"
        >
          <ShieldAlert className="w-4 h-4 animate-pulse text-white" />
          <span>긴급 킬스위치</span>
        </button>
      </div>
    </header>
  );
};
