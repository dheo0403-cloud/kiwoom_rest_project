import React, { useState } from 'react';
import {
  Sliders,
  Play,
  Square,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  Cpu,
  CheckCircle2,
  AlertCircle,
  Activity
} from 'lucide-react';
import { BotStatus } from '../types';
import { getApiUrl } from '../utils/apiConfig';

interface StrategyControlsBentoProps {
  botStatus: BotStatus;
  onRefresh?: () => void;
}

export const StrategyControlsBento: React.FC<StrategyControlsBentoProps> = ({ botStatus, onRefresh }) => {
  const [kVal, setKVal] = useState<number>(0.5);
  const [kellyVal, setKellyVal] = useState<number>(0.2);
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [applyResult, setApplyResult] = useState<string | null>(null);
  const [isControlling, setIsControlling] = useState<boolean>(false);

  const handleApplyParams = async () => {
    setIsApplying(true);
    setApplyResult(null);
    try {
      const res = await fetch(getApiUrl(`/bot/params?k_breakout=${kVal}&kelly_fraction=${kellyVal}`), {
        method: 'POST'
      });
      if (res.ok) {
        setApplyResult(`✅ 적용 완료: k=${kVal}, Kelly=${kellyVal}`);
      } else {
        const err = await res.text();
        setApplyResult(`❌ 적용 실패: ${err}`);
      }
    } catch (e) {
      setApplyResult(`❌ 통신 에러: ${e}`);
    } finally {
      setIsApplying(false);
      setTimeout(() => setApplyResult(null), 3000);
    }
  };

  const handleControlBot = async (action: 'START' | 'STOP' | 'REFRESH') => {
    setIsControlling(true);
    try {
      const res = await fetch(getApiUrl('/bot/control'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      });
      if (res.ok) {
        if (onRefresh) onRefresh();
      } else {
        const err = await res.text();
        alert(`❌ 봇 제어 실패: ${err}`);
      }
    } catch (e) {
      alert(`❌ 통신 에러: ${e}`);
    } finally {
      setIsControlling(false);
    }
  };

  return (
    <div className="bento-card p-4 flex flex-col h-full bg-slate-900/60 border border-slate-800">
      {/* 1. Header */}
      <div className="flex items-center justify-between pb-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-purple-500/10 text-purple-400">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              전략 파라미터 & 봇 제어 콕핏
            </h2>
          </div>
        </div>

        {/* Bot Status Badge */}
        <div className="flex items-center gap-1.5">
          <span
            className={`flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full border ${
              botStatus.running
                ? 'bg-emerald-950/80 text-emerald-400 border-emerald-800/50'
                : 'bg-rose-950/80 text-rose-400 border-rose-800/50'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${botStatus.running ? 'bg-emerald-400 animate-ping' : 'bg-rose-400'}`} />
            {botStatus.running ? '엔진 가동 중' : '엔진 정지됨'}
          </span>
        </div>
      </div>

      {/* 2. Direct Bot Controls (Start / Stop / Refresh) */}
      <div className="grid grid-cols-3 gap-2 my-3">
        <button
          onClick={() => handleControlBot('START')}
          disabled={isControlling || botStatus.running}
          className={`flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-bold transition border ${
            botStatus.running
              ? 'bg-slate-800/50 text-slate-500 border-slate-700/50 cursor-not-allowed'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white border-emerald-500 shadow-lg shadow-emerald-900/30'
          }`}
        >
          <Play className="w-3.5 h-3.5" />
          <span>봇 시작</span>
        </button>

        <button
          onClick={() => handleControlBot('STOP')}
          disabled={isControlling || !botStatus.running}
          className={`flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-bold transition border ${
            !botStatus.running
              ? 'bg-slate-800/50 text-slate-500 border-slate-700/50 cursor-not-allowed'
              : 'bg-rose-600 hover:bg-rose-500 text-white border-rose-500 shadow-lg shadow-rose-900/30'
          }`}
        >
          <Square className="w-3.5 h-3.5" />
          <span>봇 일시정지</span>
        </button>

        <button
          onClick={() => handleControlBot('REFRESH')}
          disabled={isControlling}
          className="flex items-center justify-center gap-1.5 py-2 px-3 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-bold transition border border-slate-700"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isControlling ? 'animate-spin' : ''}`} />
          <span>계좌/유니버스 싱크</span>
        </button>
      </div>

      {/* 3. Realtime Risk & Engine Indicators */}
      <div className="grid grid-cols-2 gap-2 mb-3 text-[11px]">
        {/* Market Filter */}
        <div className="bg-slate-950/80 p-2.5 rounded-lg border border-slate-800 flex items-center justify-between">
          <span className="text-slate-400 flex items-center gap-1">
            <Activity className="w-3.5 h-3.5 text-blue-400" />
            KODEX 200 필터
          </span>
          <span className={`font-bold ${botStatus.market_filter_passed ? 'text-emerald-400' : 'text-rose-400'}`}>
            {botStatus.market_filter_passed ? '정상 통과' : '🚨 매수 제한'}
          </span>
        </div>

        {/* Circuit Breaker */}
        <div className="bg-slate-950/80 p-2.5 rounded-lg border border-slate-800 flex items-center justify-between">
          <span className="text-slate-400 flex items-center gap-1">
            <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
            서킷 브레이커
          </span>
          <span className={`font-bold ${botStatus.circuit_breaker_open ? 'text-rose-400 animate-pulse' : 'text-slate-300'}`}>
            {botStatus.circuit_breaker_open ? 'OPEN (차단)' : 'CLOSED (정상)'}
          </span>
        </div>
      </div>

      {/* 4. Real-time Strategy Parameter Sliders */}
      <div className="flex-1 bg-slate-950/80 p-3 rounded-lg border border-slate-800 flex flex-col justify-between space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5">
            <Sliders className="w-3.5 h-3.5 text-purple-400" />
            실시간 퀀트 파라미터 튜닝
          </span>
          {applyResult && (
            <span className="text-[11px] font-bold text-emerald-400 animate-fade-in">
              {applyResult}
            </span>
          )}
        </div>

        {/* K-Breakout Slider */}
        <div className="space-y-1">
          <div className="flex justify-between text-[11px]">
            <span className="text-slate-400">ATR 변동성 돌파 계수 (k)</span>
            <span className="font-bold text-purple-400">{kVal.toFixed(2)}</span>
          </div>
          <input
            type="range"
            min="0.1"
            max="1.0"
            step="0.05"
            value={kVal}
            onChange={e => setKVal(parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-purple-500"
          />
          <div className="flex justify-between text-[9px] text-slate-500">
            <span>0.10 (공격적)</span>
            <span>0.50 (표준)</span>
            <span>1.00 (보수적)</span>
          </div>
        </div>

        {/* Fractional Kelly Slider */}
        <div className="space-y-1">
          <div className="flex justify-between text-[11px]">
            <span className="text-slate-400">프랙셔널 켈리 자산 배분 비중 (f*)</span>
            <span className="font-bold text-blue-400">{(kellyVal * 100).toFixed(0)}%</span>
          </div>
          <input
            type="range"
            min="0.05"
            max="0.5"
            step="0.05"
            value={kellyVal}
            onChange={e => setKellyVal(parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
          <div className="flex justify-between text-[9px] text-slate-500">
            <span>5% (최소)</span>
            <span>20% (표준 1/N)</span>
            <span>50% (공격적)</span>
          </div>
        </div>

        {/* Apply Button */}
        <button
          onClick={handleApplyParams}
          disabled={isApplying}
          className="w-full py-2 bg-purple-600/90 hover:bg-purple-500 text-white rounded-lg text-xs font-bold transition flex items-center justify-center gap-1.5 shadow-md shadow-purple-950/40"
        >
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>{isApplying ? '적용 중...' : '전략 파라미터 실시간 즉시 반영'}</span>
        </button>
      </div>
    </div>
  );
};
