import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi } from 'lightweight-charts';
import { LineChart, Clock } from 'lucide-react';
import { EquityHistoryPoint, PendingOrder } from '../types';
import { getApiUrl } from '../utils/apiConfig';

interface AccountSideBentoProps {
  equityHistory: EquityHistoryPoint[];
}

// 자산 추이(일별 balance 스냅샷 원본 그대로) + 봇이 추적 중인 미체결 주문
export const AccountSideBento: React.FC<AccountSideBentoProps> = ({ equityHistory }) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<IChartApi | null>(null);
  const [orders, setOrders] = useState<PendingOrder[]>([]);
  const [ordersError, setOrdersError] = useState<boolean>(false);

  // 자산 추이 라인 차트
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = createChart(chartRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#94A3B8' },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(148,163,184,0.08)' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      width: chartRef.current.clientWidth,
      height: chartRef.current.clientHeight,
    });
    const series = chart.addLineSeries({
      color: '#60A5FA', lineWidth: 2, priceLineVisible: false,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },  // 원 단위 정수 표시
    });
    // 같은 날짜가 중복되면 차트가 오류를 내므로 날짜별 마지막 값만 사용
    const byDate = new Map<string, number>();
    (equityHistory || []).forEach(p => { if (p.date) byDate.set(p.date.slice(0, 10), p.total_asset); });
    series.setData([...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([time, value]) => ({ time, value })));
    chart.timeScale().fitContent();
    chartApiRef.current = chart;

    const observer = new ResizeObserver(() => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth, height: chartRef.current.clientHeight });
    });
    observer.observe(chartRef.current);
    return () => { observer.disconnect(); chart.remove(); chartApiRef.current = null; };
  }, [equityHistory]);

  // 미체결 주문 5초 주기 조회
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(getApiUrl('/orders/pending'));
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        if (alive) { setOrders(Array.isArray(data.orders) ? data.orders : []); setOrdersError(false); }
      } catch {
        if (alive) setOrdersError(true);
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  return (
    <div className="bento-card p-4 flex flex-col h-full gap-3">
      <div className="flex flex-col flex-1 min-h-0">
        <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5 mb-1.5">
          <LineChart className="w-3.5 h-3.5 text-blue-400" />
          총자산 추이 (일별 잔고 기록)
        </span>
        {(equityHistory || []).length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-[11px] text-slate-500">잔고 기록 없음</div>
        ) : (
          <div ref={chartRef} className="flex-1 min-h-[120px]" />
        )}
      </div>

      <div className="border-t border-white/5 pt-2.5">
        <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5 mb-1.5">
          <Clock className="w-3.5 h-3.5 text-amber-400" />
          미체결 주문
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-400 font-mono">{orders.length}건</span>
        </span>
        <div className="space-y-1 max-h-24 overflow-y-auto text-[11px]">
          {ordersError ? (
            <div className="text-slate-500">미체결 주문 조회 실패</div>
          ) : orders.length === 0 ? (
            <div className="text-slate-500">미체결 주문 없음</div>
          ) : orders.map(o => (
            <div key={o.order_no} className="flex items-center justify-between font-mono text-slate-300">
              <span>
                <strong className={o.side === 'BUY' ? 'text-rose-400' : 'text-blue-400'}>{o.side === 'BUY' ? '매수' : '매도'}</strong>{' '}
                {o.name || o.code} {o.unfilled_qty}/{o.qty}주 @ {Math.round(o.price).toLocaleString()}원
              </span>
              <span className="text-slate-500">{o.elapsed_sec}초</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
