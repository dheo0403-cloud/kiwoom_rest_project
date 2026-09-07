import React, { useState } from 'react';
import {
  Activity, ShieldAlert, Zap, RefreshCw, Sliders, CheckCircle2,
  AlertTriangle, Radio, TrendingUp, TrendingDown
} from 'lucide-react';
import { BotStatus } from '../types';

interface HeaderProps {
  botStatus: BotStatus;
  isConnected: boolean;
  onOpenKillSwitch: () => void;
  onOpenParams: () => void;
  onRefresh: () => void;
  colorMode: 'KRX' | 'GLOBAL';
  onToggleColorMode: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  botStatus,
  isConnected,
  onOpenKillSwitch,
  onOpenParams,
  onRefresh,
  colorMode,
  onToggleColorMode
}) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefreshClick = () => {
    setIsRefreshing(true);
    onRefresh();
    setTimeout(() => setIsRefreshing(false), 600);
  };

  const kodexRate = botStatus.kodex200_change_rate || 0.0;
  const isMarketDrop = kodexRate <= -1.5;

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 bg-slate-900/90 border-b border-white/10 backdrop-blur-md sticky top-0 z-30">
      {/* Brand & Live Connection Indicator */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Zap className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
              KIWOOM QUANT COCKPIT
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                v2.0
              </span>
            </h1>
            <p className="text-[11px] text-slate-400">초저지연 비동기 퀀트 자동매매 관제 센터</p>
          </div>
        </div>

        {/* Live Status Badge */}
        <div className="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-full bg-slate-800/80 border border-slate-700/60 text-xs">
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'}`} />
          <span className="text-slate-300 font-medium text-[11px]">
            {isConnected ? 'LIVE WS CONNECTED' : 'RECONNECTING...'}
          </span>
        </div>

        {/* Mode Badge (REAL / MOCK) */}
        <div className={`px-2.5 py-1 rounded-md text-[11px] font-bold tracking-wider border flex items-center gap-1.5 ${
          !botStatus.is_demo
            ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'
            : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
        }`}>
          <Radio className="w-3 h-3 animate-pulse" />
          {!botStatus.is_demo ? 'REAL (실전투자)' : 'MOCK (모의투자)'}
        </div>
      </div>

      {/* Center Tickers: Market Condition & MDD */}
      <div className="hidden md:flex items-center gap-3">
        {/* KODEX 200 Indicator */}
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${
          isMarketDrop
            ? 'bg-rose-950/40 border-rose-600/50 text-rose-300 animate-pulse'
            : 'bg-slate-800/60 border-slate-700/50 text-slate-300'
        }`}>
          <span className="text-slate-400 font-medium">KODEX 200:</span>
          <span className={`font-bold tabular-nums flex items-center gap-0.5 ${
            kodexRate >= 0
              ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
              : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400')
          }`}>
            {kodexRate >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {kodexRate >= 0 ? `+${kodexRate.toFixed(2)}%` : `${kodexRate.toFixed(2)}%`}
          </span>
          {isMarketDrop && <span className="text-[10px] bg-rose-600 text-white px-1 rounded font-bold">급락제한</span>}
        </div>

        {/* Engine Status */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/50 text-xs text-slate-300">
          <Activity className="w-3.5 h-3.5 text-blue-400" />
          <span className="text-slate-400">엔진:</span>
          <span className="font-semibold text-emerald-400">정상 가동중</span>
        </div>
      </div>

      {/* Right Controls: Color Toggle, Params, Refresh, Emergency Kill-Switch */}
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
