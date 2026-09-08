import React, { useState, useRef, useEffect } from 'react';
import { Terminal, Filter, Trash2, CheckCircle2, Sliders, Play } from 'lucide-react';
import { LogMessage } from '../types';
import { getApiUrl } from '../utils/apiConfig';

interface LiveTerminalBentoProps {
  logs: LogMessage[];
  onClearLogs?: () => void;
}

export const LiveTerminalBento: React.FC<LiveTerminalBentoProps> = ({ logs, onClearLogs }) => {
  const [filterLevel, setFilterLevel] = useState<string>('ALL');
  const [kVal, setKVal] = useState<number>(0.5);
  const [kellyVal, setKellyVal] = useState<number>(0.2);
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [applyResult, setApplyResult] = useState<string | null>(null);

  const logsEndRef = useRef<HTMLDivElement>(null);

  const filteredLogs = logs.filter(log => {
    if (filterLevel === 'ALL') return true;
    if (filterLevel === 'TRADE') return log.level === 'TRADE' || log.level === 'MANUAL_ORDER';
    if (filterLevel === 'ALERT') return log.level === 'CRITICAL' || log.level === 'WARNING' || log.level === 'ERROR';
    if (filterLevel === 'SYSTEM') return log.level === 'SYSTEM' || log.level === 'INFO';
    return log.level === filterLevel;
  });

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

  return (
    <div className="bento-card p-4 flex flex-col h-full">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-white/5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-blue-500/10 text-blue-400">
            <Terminal className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              실시간 텔레메트리 & 로그 스트림
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            </h2>
          </div>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1 bg-slate-900/80 p-0.5 rounded-lg border border-slate-800 text-[11px]">
          {(['ALL', 'TRADE', 'ALERT', 'SYSTEM'] as const).map(lvl => (
            <button
              key={lvl}
              onClick={() => setFilterLevel(lvl)}
              className={`px-2 py-0.5 rounded-md font-medium transition ${
                filterLevel === lvl
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {lvl === 'ALL' ? '전체' : lvl === 'TRADE' ? '체결' : lvl === 'ALERT' ? '경보' : '시스템'}
            </button>
          ))}
        </div>
      </div>

      {/* Terminal Log Console */}
      <div className="flex-1 bg-slate-950/80 rounded-lg p-2.5 mt-2 overflow-y-auto font-mono text-[11px] leading-relaxed border border-slate-900 space-y-1.5 min-h-[160px] max-h-[190px]">
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-full text-slate-600">
            실시간 이벤트 대기 중...
          </div>
        ) : (
          filteredLogs.slice(0, 50).map((log, idx) => {
            let levelBadge = 'text-slate-400 bg-slate-800';
            if (log.level === 'TRADE') levelBadge = 'text-emerald-400 bg-emerald-950/80 border border-emerald-800/50';
            else if (log.level === 'CRITICAL' || log.level === 'ERROR') levelBadge = 'text-rose-400 bg-rose-950/80 border border-rose-800/50 animate-pulse';
            else if (log.level === 'WARNING') levelBadge = 'text-amber-400 bg-amber-950/80 border border-amber-800/50';
            else if (log.level === 'SYSTEM') levelBadge = 'text-blue-400 bg-blue-950/80 border border-blue-800/50';

            return (
              <div key={idx} className="flex items-start gap-2 hover:bg-white/5 px-1 rounded transition">
                <span className="text-slate-500 shrink-0 text-[10px]">{log.timestamp}</span>
                <span className={`px-1 rounded text-[10px] font-bold shrink-0 ${levelBadge}`}>
                  {log.level}
                </span>
                <span className="text-slate-200 break-all">{log.message}</span>
              </div>
            );
          })
        )}
        <div ref={logsEndRef} />
      </div>

      {/* Bottom Inline Parameter Tuner */}
      <div className="mt-3 pt-2.5 border-t border-white/5">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5">
            <Sliders className="w-3.5 h-3.5 text-indigo-400" />
            런타임 퀀트 파라미터 무중단 튜닝
          </span>
          {applyResult && (
            <span className="text-[11px] font-medium text-emerald-400 animate-fadeIn">
              {applyResult}
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 items-center text-xs">
          {/* k_breakout Slider */}
          <div className="sm:col-span-5 flex items-center gap-2">
            <span className="text-slate-400 text-[11px] shrink-0">ATR 돌파 (k):</span>
            <input
              type="range"
              min="0.3"
              max="0.8"
              step="0.05"
              value={kVal}
              onChange={(e) => setKVal(parseFloat(e.target.value))}
              className="w-full accent-blue-500 h-1.5 bg-slate-800 rounded-lg cursor-pointer"
            />
            <span className="font-mono font-bold text-blue-400 shrink-0">{kVal.toFixed(2)}</span>
          </div>

          {/* Kelly Fraction Slider */}
          <div className="sm:col-span-5 flex items-center gap-2">
            <span className="text-slate-400 text-[11px] shrink-0">켈리 비중:</span>
            <input
              type="range"
              min="0.05"
              max="0.4"
              step="0.05"
              value={kellyVal}
              onChange={(e) => setKellyVal(parseFloat(e.target.value))}
              className="w-full accent-indigo-500 h-1.5 bg-slate-800 rounded-lg cursor-pointer"
            />
            <span className="font-mono font-bold text-indigo-400 shrink-0">{Math.round(kellyVal * 100)}%</span>
          </div>

          {/* Apply Button */}
          <div className="sm:col-span-2 flex justify-end">
            <button
              onClick={handleApplyParams}
              disabled={isApplying}
              className="w-full py-1 px-3 text-xs font-bold rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition flex items-center justify-center gap-1 active:scale-95 disabled:opacity-50"
            >
              <Play className="w-3 h-3 fill-current" />
              {isApplying ? '적용중...' : '적용'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
