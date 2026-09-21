import React, { useState, memo, useCallback } from 'react';
import { Package, TrendingUp, TrendingDown, Layers, ArrowUpRight, AlertCircle, CheckCircle2 } from 'lucide-react';
import { Position } from '../types';
import { getApiUrl } from '../utils/apiConfig';

interface PositionCardItemProps {
  pos: Position;
  colorMode: 'KRX' | 'GLOBAL';
  isSelected: boolean;
  onSelect: (code: string) => void;
  onManualSell: (code: string, qty: number, side?: string) => void;
  isLoading: boolean;
}

// 개별 종목 카드 React.memo 분리 (해당 종목의 데이터가 변하지 않으면 리렌더링 건너뜀 -> 깜빡임 100% 차단)
const PositionCardItem = memo<PositionCardItemProps>(({
  pos,
  colorMode,
  isSelected,
  onSelect,
  onManualSell,
  isLoading
}) => {
  const rawBuyPrice = typeof pos.buy_price === 'number' ? pos.buy_price : parseFloat(String(pos.buy_price || 0));
  const rawCurPrice = typeof pos.current_price === 'number' ? pos.current_price : parseFloat(String(pos.current_price || 0));
  const buyPrice = rawBuyPrice > 1 ? rawBuyPrice : (rawCurPrice > 1 ? rawCurPrice : (rawBuyPrice > 0 ? rawBuyPrice : 0));
  const curPrice = rawCurPrice > 0 ? rawCurPrice : buyPrice;
  const qty = pos.qty || 0;
  const pnl = pos.pnl !== undefined && !isNaN(pos.pnl) ? pos.pnl : ((curPrice - buyPrice) * qty);
  const yieldRate = pos.yield_rate !== undefined && !isNaN(pos.yield_rate)
    ? pos.yield_rate
    : (buyPrice > 0 ? ((curPrice / buyPrice) - 1) * 100 : 0);
  const isProfit = pnl >= 0;
  const stage = pos.sell_stage || 0;

  const pnlTextColor = isProfit
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400');

  return (
    <div
      onClick={() => onSelect(pos.code)}
      className={`p-3 rounded-xl border transition cursor-pointer relative group ${
        isSelected
          ? 'bg-slate-850 border-blue-500/60 shadow-lg shadow-blue-500/10'
          : 'bg-slate-900/80 border-slate-800/80 hover:border-slate-700 hover:bg-slate-850/60'
      }`}
    >
      {/* Top Row: Name, Code, Qty, Live PnL */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm text-white group-hover:text-blue-400 transition">
              {pos.name || pos.code}
            </span>
            <span className="text-[10px] font-mono text-slate-400 px-1.5 py-0.5 rounded bg-slate-800">
              {pos.code}
            </span>
            <span className="text-[10px] font-bold text-slate-300 font-mono">
              {qty.toLocaleString()}주
            </span>
          </div>
          <div className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 font-mono">
            <span>매수가: {Math.round(buyPrice).toLocaleString()}원</span>
            <span>•</span>
            <span>현재가: {Math.round(curPrice).toLocaleString()}원</span>
          </div>
        </div>

        {/* PnL & Yield Badge */}
        <div className="text-right">
          <div className={`text-sm font-black font-mono tabular-nums ${pnlTextColor}`}>
            {isProfit ? `+${yieldRate.toFixed(2)}%` : `${yieldRate.toFixed(2)}%`}
          </div>
          <div className={`text-[11px] font-mono tabular-nums ${pnlTextColor}`}>
            {isProfit ? `+${Math.round(pnl).toLocaleString()}` : Math.round(pnl).toLocaleString()}원
          </div>
        </div>
      </div>

      {/* Bottom Row: Stage Progress & Quick Actions */}
      <div className="flex items-center justify-between mt-2.5 pt-2 border-t border-white/5 text-xs">
        {/* Stage Badges */}
        <div className="flex items-center gap-1.5 text-[10px]">
          <span className={`px-1.5 py-0.5 rounded font-medium ${
            stage >= 0 ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30' : 'bg-slate-800 text-slate-500'
          }`}>
            진입
          </span>
          <span className="text-slate-600">➔</span>
          <span className={`px-1.5 py-0.5 rounded font-medium ${
            stage >= 1 ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-slate-800 text-slate-500'
          }`}>
            1차(+3%)
          </span>
          <span className="text-slate-600">➔</span>
          <span className={`px-1.5 py-0.5 rounded font-medium ${
            stage >= 2 ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30' : 'bg-slate-800 text-slate-500'
          }`}>
            2차(+5%)
          </span>
        </div>

        {/* Quick Action Buttons */}
        <div className="flex items-center gap-1.5">
          {qty > 1 && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onManualSell(pos.code, Math.max(1, Math.floor(qty * 0.33)));
              }}
              disabled={isLoading}
              className="px-2 py-0.5 text-[10px] font-semibold rounded bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
            >
              33% 익절
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onManualSell(pos.code, qty);
            }}
            disabled={isLoading}
            className="px-2 py-0.5 text-[10px] font-bold rounded bg-rose-600/80 hover:bg-rose-600 text-white transition"
          >
            전량청산
          </button>
        </div>
      </div>
    </div>
  );
});

PositionCardItem.displayName = 'PositionCardItem';

interface ActivePositionsBentoProps {
  positions: Position[];
  colorMode: 'KRX' | 'GLOBAL';
  onSelectStock: (code: string) => void;
  selectedStockCode: string;
  onRefresh: () => void;
}

export const ActivePositionsBento: React.FC<ActivePositionsBentoProps> = memo(({
  positions,
  colorMode,
  onSelectStock,
  selectedStockCode,
  onRefresh
}) => {
  const [loadingCode, setLoadingCode] = useState<string | null>(null);

  const handleManualSell = useCallback(async (code: string, qty: number, side: string = 'SELL') => {
    if (!window.confirm(`${code} 종목 ${qty}주를 시장가로 즉시 매도하시겠습니까?`)) {
      return;
    }
    setLoadingCode(code);
    try {
      const res = await fetch(getApiUrl('/order/manual'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, side, qty, price: 0 })
      });
      if (res.ok) {
        alert(`✅ ${code} ${qty}주 시장가 매도 주문이 접수되었습니다.`);
        onRefresh();
      } else {
        const err = await res.text();
        alert(`❌ 주문 실패: ${err}`);
      }
    } catch (e) {
      alert(`❌ API 서버 통신 오류: ${e}`);
    } finally {
      setLoadingCode(null);
    }
  }, [onRefresh]);

  const safePositions = Array.isArray(positions) ? positions : [];

  return (
    <div className="bento-card p-4 flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-blue-500/10 text-blue-400">
            <Package className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              현재 보유 포지션 (Active Positions)
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400 font-mono">
                {safePositions.length}개 보유
              </span>
            </h2>
          </div>
        </div>
        <span className="text-[11px] text-slate-400">실시간 PnL 감시 및 3단계 분할 익절</span>
      </div>

      {/* Positions List */}
      <div className="mt-3 flex-1 overflow-y-auto space-y-2.5 pr-1 max-h-[360px]">
        {safePositions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-center text-slate-500">
            <Package className="w-10 h-10 stroke-1 mb-2 text-slate-600" />
            <p className="text-xs font-medium text-slate-400">현재 보유 중인 포지션이 없습니다.</p>
            <p className="text-[11px] text-slate-600 mt-1">감시종목의 피보나치 눌림목 및 ATR 돌파 시 자동 매수됩니다.</p>
          </div>
        ) : (
          safePositions.map((pos) => (
            <PositionCardItem
              key={pos.code}
              pos={pos}
              colorMode={colorMode}
              isSelected={selectedStockCode === pos.code}
              onSelect={onSelectStock}
              onManualSell={handleManualSell}
              isLoading={loadingCode === pos.code}
            />
          ))
        )}
      </div>
    </div>
  );
});

ActivePositionsBento.displayName = 'ActivePositionsBento';
