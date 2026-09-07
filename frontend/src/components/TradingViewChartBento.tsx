import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi } from 'lightweight-charts';
import { LineChart, BarChart2, Maximize2, RefreshCw } from 'lucide-react';
import { WatchlistItem, Position } from '../types';

interface TradingViewChartBentoProps {
  selectedStockCode: string;
  selectedStockName: string;
  watchlist: WatchlistItem[];
  positions: Position[];
  colorMode: 'KRX' | 'GLOBAL';
}

export const TradingViewChartBento: React.FC<TradingViewChartBentoProps> = ({
  selectedStockCode,
  selectedStockName,
  watchlist,
  positions,
  colorMode
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<IChartApi | null>(null);
  const [selectedStock, setSelectedStock] = useState<WatchlistItem | null>(null);
  const [chartPeriod, setChartPeriod] = useState<'1m' | '5m' | 'D'>('1m');

  const activePosition = positions.find(p => p.code === selectedStockCode);
  const activeWatchItem = watchlist.find(w => w.code === selectedStockCode);

  useEffect(() => {
    if (activeWatchItem) {
      setSelectedStock(activeWatchItem);
    }
  }, [selectedStockCode, activeWatchItem]);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Clean up existing chart
    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const upColor = colorMode === 'KRX' ? '#EF4444' : '#10B981';
    const downColor = colorMode === 'KRX' ? '#3B82F6' : '#EF4444';

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94A3B8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
      },
      crosshair: {
        vertLine: { color: '#64748B', width: 1, style: 2 },
        horzLine: { color: '#64748B', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: 'rgba(255, 255, 255, 0.08)',
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: 'rgba(255, 255, 255, 0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    });

    chartInstanceRef.current = chart;

    // Candlestick Series
    const candleSeries = chart.addCandlestickSeries({
      upColor,
      downColor,
      borderUpColor: upColor,
      borderDownColor: downColor,
      wickUpColor: upColor,
      wickDownColor: downColor,
    });

    // Volume Series
    const volumeSeries = chart.addHistogramSeries({
      color: '#334155',
      priceFormat: { type: 'volume' },
      priceScaleId: '', // Overlay on separate internal scale
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // Generate Initial Simulation/Historical Candles around current stock price
    const curPrice = activePosition?.current_price || activeWatchItem?.current_price || 75000;
    const periodHigh = activeWatchItem?.period_high || curPrice * 1.05;
    const periodLow = activeWatchItem?.period_low || curPrice * 0.95;
    const fib382 = activeWatchItem?.fib_382 || (periodHigh - (periodHigh - periodLow) * 0.382);
    const fib500 = activeWatchItem?.fib_500 || (periodHigh - (periodHigh - periodLow) * 0.500);
    const fib618 = activeWatchItem?.fib_618 || (periodHigh - (periodHigh - periodLow) * 0.618);

    const nowSec = Math.floor(Date.now() / 1000);
    const sampleCandles = [];
    const sampleVolumes = [];
    let prevClose = periodLow + (curPrice - periodLow) * 0.5;

    for (let i = 50; i >= 0; i--) {
      const time = (nowSec - i * 60) as any;
      const change = (Math.random() - 0.48) * (curPrice * 0.004);
      const open = prevClose;
      const close = open + change;
      const high = Math.max(open, close) + Math.random() * (curPrice * 0.002);
      const low = Math.min(open, close) - Math.random() * (curPrice * 0.002);
      const vol = Math.floor(Math.random() * 5000) + 1000;

      sampleCandles.push({ time, open, high, low, close });
      sampleVolumes.push({
        time,
        value: vol,
        color: close >= open ? (colorMode === 'KRX' ? 'rgba(239, 68, 68, 0.4)' : 'rgba(16, 185, 129, 0.4)') : (colorMode === 'KRX' ? 'rgba(59, 130, 246, 0.4)' : 'rgba(239, 68, 68, 0.4)'),
      });
      prevClose = close;
    }

    candleSeries.setData(sampleCandles);
    volumeSeries.setData(sampleVolumes);

    // Add Fibonacci Lines if available
    if (fib382 > 0) {
      candleSeries.createPriceLine({
        price: fib382,
        color: '#10B981',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'Fib 38.2%',
      });
    }
    if (fib500 > 0) {
      candleSeries.createPriceLine({
        price: fib500,
        color: '#F59E0B',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'Fib 50.0%',
      });
    }
    if (fib618 > 0) {
      candleSeries.createPriceLine({
        price: fib618,
        color: '#EF4444',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'Fib 61.8%',
      });
    }

    // Add Buy Price Line if holding position
    if (activePosition && activePosition.buy_price > 0) {
      candleSeries.createPriceLine({
        price: activePosition.buy_price,
        color: '#3B82F6',
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `매수평단: ${activePosition.buy_price.toLocaleString()}원`,
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current && chartInstanceRef.current) {
        chartInstanceRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartInstanceRef.current) {
        chartInstanceRef.current.remove();
        chartInstanceRef.current = null;
      }
    };
  }, [selectedStockCode, colorMode, activePosition, activeWatchItem]);

  const curPrice = activePosition?.current_price || activeWatchItem?.current_price || 0;

  return (
    <div className="bento-card p-4 flex flex-col h-full">
      {/* Chart Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-md bg-indigo-500/10 text-indigo-400">
            <LineChart className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-black text-white tracking-tight">
                {selectedStockName || selectedStock?.name || '종목 선택'}
              </h2>
              <span className="text-xs font-mono text-slate-400 px-1.5 py-0.5 rounded bg-slate-800">
                {selectedStockCode || '005930'}
              </span>
              {activePosition && (
                <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                  보유중 ({activePosition.qty}주)
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Current Price & Indicator Info */}
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-lg font-black font-mono tabular-nums text-white">
              {curPrice > 0 ? `${Math.round(curPrice).toLocaleString()}원` : '조회 중...'}
            </div>
            <div className="text-[10px] text-slate-400 flex items-center gap-2">
              <span>MA20 지지</span>
              <span>•</span>
              <span>피보나치 3대 레벨 오버레이</span>
            </div>
          </div>

          {/* Period Selector */}
          <div className="flex items-center rounded-lg bg-slate-800/80 p-0.5 border border-slate-700/60 text-xs">
            {(['1m', '5m', 'D'] as const).map(p => (
              <button
                key={p}
                onClick={() => setChartPeriod(p)}
                className={`px-2 py-1 rounded-md text-[11px] font-semibold transition ${
                  chartPeriod === p
                    ? 'bg-blue-600 text-white shadow'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {p === '1m' ? '1분' : p === '5m' ? '5분' : '일봉'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* TradingView Chart Canvas Container */}
      <div className="flex-1 w-full min-h-[300px] mt-2 relative" ref={chartContainerRef}>
        {/* Shaded Indicator Watermark */}
        <div className="absolute top-3 left-3 pointer-events-none z-10 flex flex-wrap gap-2 text-[10px] font-mono">
          <span className="text-emerald-400 bg-emerald-950/40 px-1.5 py-0.5 rounded border border-emerald-800/40">
            Fib 38.2%: {selectedStock?.fib_382 ? Math.round(selectedStock.fib_382).toLocaleString() : '-'}
          </span>
          <span className="text-amber-400 bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-800/40">
            Fib 50.0%: {selectedStock?.fib_500 ? Math.round(selectedStock.fib_500).toLocaleString() : '-'}
          </span>
          <span className="text-rose-400 bg-rose-950/40 px-1.5 py-0.5 rounded border border-rose-800/40">
            Fib 61.8%: {selectedStock?.fib_618 ? Math.round(selectedStock.fib_618).toLocaleString() : '-'}
          </span>
        </div>
      </div>
    </div>
  );
};
