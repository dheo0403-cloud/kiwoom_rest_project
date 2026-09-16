import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  Terminal,
  Filter,
  Trash2,
  Copy,
  Check,
  Search,
  ArrowDownCircle,
  Pause,
  Play,
  Flame,
  Clock,
  AlertTriangle,
  Info
} from 'lucide-react';
import { LogMessage, PortfolioSnapshot } from '../types';

interface LogViewerProps {
  logs: LogMessage[];
  portfolio?: PortfolioSnapshot;
  onClearLogs?: () => void;
  className?: string;
}

export const LogViewer: React.FC<LogViewerProps> = ({ logs, portfolio, onClearLogs, className = '' }) => {
  const [filterLevel, setFilterLevel] = useState<'ALL' | 'WATCH' | 'TRADE' | 'ALERT' | 'SYSTEM'>('ALL');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [autoScroll, setAutoScroll] = useState<boolean>(true);
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const totalAsset = portfolio?.total_asset || 0;
  const currentCapital = portfolio?.current_capital || 0;
  const positionsCount = portfolio?.positions?.length || 0;

  const terminalBodyRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // 필터링된 로그 목록 (최근 100건 기준, 시간순 또는 최신순)
  const filteredLogs = useMemo(() => {
    return logs.filter(log => {
      // 1. 레벨 필터링
      if (filterLevel !== 'ALL') {
        if (filterLevel === 'WATCH' && log.level !== 'WATCH' && !log.message.includes('실시간 감시') && !log.message.includes('타점')) return false;
        if (filterLevel === 'TRADE' && log.level !== 'TRADE' && log.level !== 'MANUAL_ORDER' && !log.message.includes('체결') && !log.message.includes('BUY') && !log.message.includes('SELL')) return false;
        if (filterLevel === 'ALERT' && log.level !== 'CRITICAL' && log.level !== 'WARNING' && log.level !== 'ERROR' && !log.message.includes('🚨') && !log.message.includes('⚠️')) return false;
        if (filterLevel === 'SYSTEM' && log.level !== 'SYSTEM' && log.level !== 'INFO') return false;
      }

      // 2. 검색어 필터링
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const msg = (log.message || '').toLowerCase();
        const lvl = (log.level || '').toLowerCase();
        const ts = (log.timestamp || '').toLowerCase();
        return msg.includes(query) || lvl.includes(query) || ts.includes(query);
      }

      return true;
    });
  }, [logs, filterLevel, searchQuery]);

  // 자동 스크롤 처리
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [filteredLogs, autoScroll]);

  const handleCopyLogs = async () => {
    if (filteredLogs.length === 0) return;
    const text = filteredLogs
      .map(l => `[${l.timestamp}] [${l.level}] ${l.message}`)
      .join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (e) {
      console.warn("Failed to copy logs:", e);
    }
  };

  // 텍스트 내 주요 키워드 하이라이팅 렌더러
  const formatLogMessage = (message: string) => {
    // ⏱ [실시간 감시] 하이라이팅
    if (message.includes('[실시간 감시]')) {
      return (
        <span className="text-slate-200">
          <span className="text-cyan-400 font-semibold">⏱ [실시간 감시]</span>{' '}
          {message.replace('⏱ [실시간 감시]', '').replace('[실시간 감시]', '')}
        </span>
      );
    }
    if (message.includes('BUY_SIGNAL') || message.includes('매수 체결')) {
      return (
        <span className="text-emerald-300 font-medium">
          {message}
        </span>
      );
    }
    if (message.includes('익절') || message.includes('분할 익절')) {
      return (
        <span className="text-teal-300 font-medium">
          {message}
        </span>
      );
    }
    if (message.includes('긴급 매도') || message.includes('스탑로스') || message.includes('🚨')) {
      return (
        <span className="text-rose-300 font-semibold">
          {message}
        </span>
      );
    }
    if (message.includes('예수금 정산') || message.includes('계좌 싱크')) {
      return (
        <span className="text-amber-200">
          {message}
        </span>
      );
    }
    return <span className="text-slate-300">{message}</span>;
  };

  return (
    <div className={`bento-card flex flex-col h-full bg-slate-950/95 border border-slate-800 shadow-2xl overflow-hidden font-mono ${className}`}>
      {/* 1. Terminal Top Window Bar */}
      <div className="flex flex-wrap items-center justify-between px-3.5 py-2.5 bg-slate-900/90 border-b border-white/5 gap-2 select-none">
        {/* Left: Window Controls & Title */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full bg-rose-500/80 border border-rose-600 inline-block cursor-pointer hover:opacity-80" onClick={onClearLogs} title="로그 지우기" />
            <span className="w-3 h-3 rounded-full bg-amber-500/80 border border-amber-600 inline-block cursor-pointer hover:opacity-80" onClick={() => setAutoScroll(prev => !prev)} title="자동스크롤 토글" />
            <span className="w-3 h-3 rounded-full bg-emerald-500/80 border border-emerald-600 inline-block cursor-pointer hover:opacity-80" title="실시간 활성화됨" />
          </div>

          <div className="flex items-center gap-2 pl-2 border-l border-white/10">
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span className="text-xs font-bold text-slate-200 tracking-wide">
              TERMINAL LOG VIEWER
            </span>
            <span className="flex items-center gap-1 text-[10px] font-semibold text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded-full border border-emerald-800/40">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
              LIVE STREAM
            </span>
            {totalAsset > 0 && (
              <span className="hidden xl:inline-flex items-center gap-1.5 text-[10px] font-bold text-slate-300 bg-slate-900/80 px-2 py-0.5 rounded border border-slate-700">
                <span>총자산: <strong className="text-white">{Math.round(totalAsset).toLocaleString()}원</strong></span>
                <span className="text-slate-600">|</span>
                <span>예수금: <strong className="text-amber-300">{Math.round(currentCapital).toLocaleString()}원</strong></span>
                <span className="text-slate-600">|</span>
                <span>보유: <strong className="text-cyan-400">{positionsCount}개</strong></span>
              </span>
            )}
          </div>
        </div>

        {/* Right: Actions (Auto-Scroll, Copy, Clear) */}
        <div className="flex items-center gap-1.5 text-xs">
          {/* Auto Scroll Toggle */}
          <button
            onClick={() => setAutoScroll(prev => !prev)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold transition border ${
              autoScroll
                ? 'bg-blue-600/20 text-blue-400 border-blue-500/40 hover:bg-blue-600/30'
                : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-white'
            }`}
            title="새 로그 수신 시 최하단으로 자동 스크롤"
          >
            {autoScroll ? <ArrowDownCircle className="w-3.5 h-3.5 text-blue-400 animate-bounce" /> : <Pause className="w-3.5 h-3.5" />}
            <span>Auto-Scroll {autoScroll ? 'ON' : 'OFF'}</span>
          </button>

          {/* Copy Button */}
          <button
            onClick={handleCopyLogs}
            className="flex items-center gap-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-[11px] font-medium transition border border-slate-700"
            title="현재 표시된 로그 복사"
          >
            {isCopied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{isCopied ? '복사됨' : '복사'}</span>
          </button>

          {/* Clear Button */}
          {onClearLogs && (
            <button
              onClick={onClearLogs}
              className="flex items-center gap-1 px-2 py-1 bg-slate-800/80 hover:bg-rose-950/50 hover:text-rose-400 text-slate-400 rounded-md text-[11px] transition border border-slate-700/60"
              title="화면 로그 비우기"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* 2. Filter & Search Controls Bar */}
      <div className="flex flex-wrap items-center justify-between px-3.5 py-2 bg-slate-900/60 border-b border-white/5 gap-2 text-[11px]">
        {/* Left: Category Filter Pills */}
        <div className="flex items-center gap-1">
          <span className="text-slate-500 mr-1 flex items-center gap-1 text-[10px]">
            <Filter className="w-3 h-3" /> 필터:
          </span>
          {(
            [
              { id: 'ALL', label: '전체', icon: null },
              { id: 'WATCH', label: '⏱ 감시', icon: Clock },
              { id: 'TRADE', label: '🔥 체결', icon: Flame },
              { id: 'ALERT', label: '🚨 경보', icon: AlertTriangle },
              { id: 'SYSTEM', label: '⚙ 시스템', icon: Info },
            ] as const
          ).map(tab => (
            <button
              key={tab.id}
              onClick={() => setFilterLevel(tab.id)}
              className={`px-2.5 py-1 rounded-md font-semibold transition flex items-center gap-1 border ${
                filterLevel === tab.id
                  ? 'bg-blue-600 text-white border-blue-500 shadow-sm'
                  : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200 hover:bg-slate-800'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Right: Search Box */}
        <div className="relative flex items-center min-w-[160px] max-w-[220px]">
          <Search className="w-3 h-3 text-slate-500 absolute left-2.5 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="종목/타점/키워드 검색..."
            className="w-full bg-slate-950 text-slate-200 pl-7 pr-2.5 py-1 rounded-md border border-slate-800 focus:border-blue-500 focus:outline-none text-[11px]"
          />
        </div>
      </div>

      {/* 3. Terminal Body (Continuous Log Stream) */}
      <div
        ref={terminalBodyRef}
        className="flex-1 p-3 overflow-y-auto bg-black/90 font-mono text-[11px] leading-relaxed select-text space-y-1 scrollbar-thin scrollbar-thumb-slate-800 scrollbar-track-transparent min-h-[260px]"
      >
        {filteredLogs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full min-h-[200px] text-slate-600 gap-2">
            <Terminal className="w-8 h-8 opacity-40 text-cyan-500" />
            <p className="text-xs">실시간 매매/감시 텔레메트리 수신 대기 중...</p>
            <p className="text-[10px] text-slate-700">봇이 정규 거래 시간에 종목 시세를 수신하면 실시간 로그가 표출됩니다.</p>
          </div>
        ) : (
          filteredLogs.map((log, index) => {
            let badgeClass = 'text-slate-400 bg-slate-800 border-slate-700';
            let badgeLabel: string = log.level;

            if (log.level === 'WATCH' || log.message.includes('실시간 감시')) {
              badgeClass = 'text-cyan-300 bg-cyan-950/80 border-cyan-800/60 shadow-cyan-950/30';
              badgeLabel = 'WATCH';
            } else if (log.level === 'TRADE' || log.level === 'MANUAL_ORDER') {
              badgeClass = 'text-emerald-300 bg-emerald-950/80 border-emerald-800/60 font-bold';
              badgeLabel = 'TRADE';
            } else if (log.level === 'CRITICAL' || log.level === 'ERROR') {
              badgeClass = 'text-rose-300 bg-rose-950/90 border-rose-800/70 animate-pulse font-bold';
              badgeLabel = 'ALERT';
            } else if (log.level === 'WARNING') {
              badgeClass = 'text-amber-300 bg-amber-950/80 border-amber-800/60';
              badgeLabel = 'WARN';
            } else if (log.level === 'SYSTEM') {
              badgeClass = 'text-blue-300 bg-blue-950/80 border-blue-800/60';
              badgeLabel = 'SYSTEM';
            } else if (log.level === 'INFO') {
              badgeClass = 'text-indigo-300 bg-indigo-950/80 border-indigo-800/60';
              badgeLabel = 'INFO';
            }

            return (
              <div
                key={log.id || `log-${index}`}
                className="flex items-start gap-2 px-1.5 py-0.5 rounded hover:bg-slate-900/60 transition group border-l-2 border-transparent hover:border-blue-500"
              >
                {/* Index / Line Number - 다크 테마 접근성 개선 (선명한 text-slate-400 적용) */}
                <span className="text-slate-400 select-none text-[10px] w-7 text-right shrink-0 pt-0.5 font-semibold font-mono tracking-tighter">
                  {index + 1}
                </span>

                {/* Timestamp - 다크 테마 가독성 개선 (선명한 text-slate-300 적용) */}
                <span className="text-slate-300 shrink-0 text-[10px] pt-0.5 font-medium font-mono">
                  {log.timestamp}
                </span>

                {/* Level Badge */}
                <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0 border ${badgeClass}`}>
                  {badgeLabel}
                </span>

                {/* Formatted Message Body */}
                <div className="flex-1 break-all whitespace-pre-wrap">
                  {formatLogMessage(log.message)}
                </div>
              </div>
            );
          })
        )}
        <div ref={logsEndRef} />
      </div>

      {/* 4. Terminal Footer Status */}
      <div className="px-3 py-1.5 bg-slate-950 border-t border-white/5 flex items-center justify-between text-[10px] text-slate-500 select-none">
        <div className="flex items-center gap-2">
          <span>전체 수신 로그: <strong className="text-slate-300 font-semibold">{logs.length}</strong>줄</span>
          <span>•</span>
          <span>필터링된 로그: <strong className="text-cyan-400 font-semibold">{filteredLogs.length}</strong>줄</span>
        </div>
        <div className="flex items-center gap-1 text-slate-400">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
          <span>Kiwoom OpenAPI REST + WebSocket Pipeline</span>
        </div>
      </div>
    </div>
  );
};
