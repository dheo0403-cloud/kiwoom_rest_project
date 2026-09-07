import React, { useState } from 'react';
import { ListFilter, Search, TrendingUp, TrendingDown, Eye, CheckCircle2 } from 'lucide-react';
import { WatchlistItem } from '../types';

interface WatchlistBentoProps {
  watchlist: WatchlistItem[];
  onSelectStock: (code: string, name: string) => void;
  selectedStockCode: string;
  colorMode: 'KRX' | 'GLOBAL';
}

export const WatchlistBento: React.FC<WatchlistBentoProps> = ({
  watchlist,
  onSelectStock,
  selectedStockCode,
  colorMode
}) => {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredWatchlist = watchlist.filter(item => {
    const term = searchTerm.toLowerCase().trim();
    return (
      (item.name && item.name.toLowerCase().includes(term)) ||
      (item.code && item.code.includes(term))
    );
  });

  return (
    <div className="bento-card p-4 flex flex-col h-full">
      {/* Header with Search */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-400">
            <ListFilter className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              거래대금 상위 30 주도주 (Universe)
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">
                {watchlist.length}종목 감시중
              </span>
            </h2>
          </div>
        </div>

        {/* Search Input */}
        <div className="relative w-40 sm:w-48">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
          <input
            type="text"
            placeholder="종목명 또는 코드 검색..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-8 pr-2.5 py-1 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition"
          />
        </div>
      </div>

      {/* Table Header */}
      <div className="grid grid-cols-12 gap-2 px-3 py-2 text-[11px] font-semibold text-slate-400 border-b border-white/5 mt-1">
        <div className="col-span-1 text-center">#</div>
        <div className="col-span-4">종목명 / 코드</div>
        <div className="col-span-3 text-right">현재가</div>
        <div className="col-span-4 text-center">피보나치 상태</div>
      </div>

      {/* Table Body List */}
      <div className="flex-1 overflow-y-auto space-y-1 mt-1 pr-1 max-h-[300px]">
        {filteredWatchlist.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-slate-500 text-xs">
            <Eye className="w-8 h-8 stroke-1 mb-1 text-slate-600" />
            <p>검색 조건에 일치하는 감시 종목이 없습니다.</p>
          </div>
        ) : (
          filteredWatchlist.map((item, idx) => {
            const isSelected = selectedStockCode === item.code;
            const curPrice = item.current_price || 0;
            const fib382 = item.fib_382 || 0;
            const fib618 = item.fib_618 || 0;

            // Determine Fibonacci Status Badge
            let fibBadge = { text: '관망/대기', bg: 'bg-slate-800 text-slate-400 border-slate-700' };
            if (curPrice > 0 && fib382 > 0 && fib618 > 0) {
              if (curPrice >= fib618 && curPrice <= fib382) {
                fibBadge = { text: '🟢 38.2% 지지반등', bg: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' };
              } else if (curPrice > fib382) {
                fibBadge = { text: '🟡 ATR 돌파진행', bg: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
              } else {
                fibBadge = { text: '🔴 61.8% 깊은눌림', bg: 'bg-rose-500/20 text-rose-400 border-rose-500/30' };
              }
            }

            return (
              <div
                key={item.code}
                onClick={() => onSelectStock(item.code, item.name)}
                className={`grid grid-cols-12 gap-2 items-center px-3 py-2 rounded-lg text-xs transition cursor-pointer border ${
                  isSelected
                    ? 'bg-blue-600/15 border-blue-500/50 text-white font-medium'
                    : 'bg-slate-900/40 border-transparent hover:border-slate-800 hover:bg-slate-850 text-slate-300'
                }`}
              >
                <div className="col-span-1 text-center font-mono text-slate-500 text-[11px]">
                  {idx + 1}
                </div>
                <div className="col-span-4 flex flex-col min-w-0">
                  <span className="font-bold text-slate-100 truncate">{item.name}</span>
                  <span className="text-[10px] font-mono text-slate-400">{item.code}</span>
                </div>
                <div className="col-span-3 text-right font-mono font-bold tabular-nums text-slate-100">
                  {curPrice > 0 ? `${Math.round(curPrice).toLocaleString()}원` : '-'}
                </div>
                <div className="col-span-4 flex justify-center">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${fibBadge.bg}`}>
                    {fibBadge.text}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
