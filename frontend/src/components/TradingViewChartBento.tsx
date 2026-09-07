import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi } from 'lightweight-charts';
import { LineChart, BarChart2, RefreshCw, ChevronDown } from 'lucide-react';
import { WatchlistItem, Position, ChartResponse } from '../types';

interface TradingViewChartBentoProps {
  selectedStockCode: string;
  selectedStockName: string;
  watchlist: WatchlistItem[];
  positions: Position[];
  colorMode: 'KRX' | 'GLOBAL';
  onSelectStock?: (code: string, name?: string) => void;
}

export const TradingViewChartBento: React.FC<TradingViewChartBentoProps> = ({
  selectedStockCode,
  selectedStockName,
  watchlist,
  positions,
  colorMode,
  onSelectStock
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  const [chartPeriod, setChartPeriod] = useState<'1m' | '5m' | 'D'>('1m');
  const [chartData, setChartData] = useState<ChartResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const activePosition = positions.find(p => p.code === selectedStockCode);
  const activeWatchItem = watchlist.find(w => w.code === selectedStockCode);

  const effectiveName = selectedStockName || activePosition?.name || activeWatchItem?.name || chartData?.name || selectedStockCode;
  const curPrice = chartData?.current_price || activePosition?.current_price || activeWatchItem?.current_price || 0;

  // 1. 차트 실데이터 Fetch
  const fetchChartData = useCallback(async () => {
    if (!selectedStockCode) return;
    setIsLoading(true);
    try {
      const res = await fetch(`/api/chart/${selectedStockCode}?period=${chartPeriod}`);
      if (res.ok) {
        const data: ChartResponse = await res.json();
        setChartData(data);
      }
    } catch (e) {
      console.warn("Chart data fetch error:", e);
    } finally {
      setIsLoading(false);
    }
  }, [selectedStockCode, chartPeriod]);

  useEffect(() => {
    fetchChartData();
    const interval = setInterval(fetchChartData, 5000);
    return () => clearInterval(interval);
  }, [fetchChartData]);

  // 2. TradingView Chart 초기화 및 캔들 바인딩
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // 기존 인스턴스 정리
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
        timeVisible: chartPeriod !== 'D',
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
    candleSeriesRef.current = candleSeries;

    // Volume Series
    const volumeSeries = chart.addHistogramSeries({
      color: '#334155',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    volumeSeriesRef.current = volumeSeries;

    // 캔들 데이터 매핑
    const rawCandles = chartData?.candles || [];
    if (rawCandles.length > 0) {
      const sorted = [...rawCandles].sort((a, b) => {
        if (typeof a.time === 'number' && typeof b.time === 'number') {
          return a.time - b.time;
        }
        return String(a.time).localeCompare(String(b.time));
      });

      // 중복 timestamp 제거
      const uniqueCandles: any[] = [];
      const uniqueVolumes: any[] = [];
      const seenTimes = new Set();

      for (const c of sorted) {
        if (!seenTimes.has(c.time) && c.open > 0 && c.close > 0) {
          seenTimes.add(c.time);
          uniqueCandles.push({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close
          });
          uniqueVolumes.push({
            time: c.time,
            value: c.volume || 0,
            color: c.close >= c.open
              ? (colorMode === 'KRX' ? 'rgba(239, 68, 68, 0.45)' : 'rgba(16, 185, 129, 0.45)')
              : (colorMode === 'KRX' ? 'rgba(59, 130, 246, 0.45)' : 'rgba(239, 68, 68, 0.45)'),
          });
        }
      }

      if (uniqueCandles.length > 0) {
        candleSeries.setData(uniqueCandles);
        volumeSeries.setData(uniqueVolumes);
      }
    }

    // 피보나치 3대 지지선 오버레이
    const fib382 = chartData?.fib_382 || activeWatchItem?.fib_382 || 0;
    const fib500 = chartData?.fib_500 || activeWatchItem?.fib_500 || 0;
    const fib618 = chartData?.fib_618 || activeWatchItem?.fib_618 || 0;

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

    // 보유 포지션 매수평단가 라인 오버레이
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
  }, [chartData, chartPeriod, colorMode, activePosition, activeWatchItem]);

  return (
    <div className="flex flex-col h-full bg-slate-900/90 rounded-xl border border-white/10 p-4 backdrop-blur-md relative overflow-hidden">
      {/* Chart Top Header & Quick Selector */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-md bg-indigo-500/10 text-indigo-400">
            <LineChart className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              {/* Dynamic Stock Quick Select Dropdown */}
              <div className="relative inline-block">
                <select
                  value={selectedStockCode}
                  onChange={(e) => {
                    const targetCode = e.target.value;
                    const found = watchlist.find(w => w.code === targetCode) || positions.find(p => p.code === targetCode);
                    if (onSelectStock) {
                      onSelectStock(targetCode, found?.name);
                    }
                  }}
                  className="bg-slate-800 text-white font-bold text-sm sm:text-base rounded-lg px-2.5 py-1 pr-7 border border-slate-700 hover:border-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500 appearance-none cursor-pointer"
                >
                  {positions.length > 0 && (
                    <optgroup label="📦 보유 포지션">
                      {positions.map(p => (
                        <option key={p.code} value={p.code}>
                          {p.name} ({p.code})
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {watchlist.length > 0 && (
                    <optgroup label="📋 감시 종목 유니버스">
                      {watchlist.map(w => (
                        <option key={w.code} value={w.code}>
                          {w.name} ({w.code})
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {positions.length === 0 && watchlist.length === 0 && (
                    <option value={selectedStockCode || '005930'}>
                      {effectiveName} ({selectedStockCode || '005930'})
                    </option>
                  )}
                </select>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
              </div>

              {activePosition && (
                <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                  보유 ({activePosition.qty}주)
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Current Price & Period Selector */}
        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="text-lg font-black font-mono tabular-nums text-white flex items-center justify-end gap-1.5">
              {curPrice > 0 ? (
                `${Math.round(curPrice).toLocaleString()}원`
              ) : isLoading ? (
                <span className="text-xs text-slate-400 animate-pulse">시세 수신 중...</span>
              ) : (
                <span className="text-xs text-slate-400">대기 중</span>
              )}
            </div>
            <div className="text-[10px] text-slate-400 flex items-center gap-1.5 justify-end">
              <span>피보나치 3대 지지선</span>
              <span>•</span>
              <span>실시간 캔들</span>
            </div>
          </div>

          {/* Period Selector Tabs */}
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

          <button
            onClick={fetchChartData}
            title="차트 즉시 새로고침"
            className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-blue-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* TradingView Chart Canvas Container */}
      <div className="flex-1 w-full min-h-[290px] mt-2 relative" ref={chartContainerRef}>
        {/* Shaded Indicator Watermark */}
        <div className="absolute top-2 left-2 pointer-events-none z-10 flex flex-wrap gap-1.5 text-[10px] font-mono">
          <span className="text-emerald-400 bg-emerald-950/40 px-1.5 py-0.5 rounded border border-emerald-800/40">
            Fib 38.2%: {chartData?.fib_382 ? Math.round(chartData.fib_382).toLocaleString() : (activeWatchItem?.fib_382 ? Math.round(activeWatchItem.fib_382).toLocaleString() : '-')}
          </span>
          <span className="text-amber-400 bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-800/40">
            Fib 50.0%: {chartData?.fib_500 ? Math.round(chartData.fib_500).toLocaleString() : (activeWatchItem?.fib_500 ? Math.round(activeWatchItem.fib_500).toLocaleString() : '-')}
          </span>
          <span className="text-rose-400 bg-rose-950/40 px-1.5 py-0.5 rounded border border-rose-800/40">
            Fib 61.8%: {chartData?.fib_618 ? Math.round(chartData.fib_618).toLocaleString() : (activeWatchItem?.fib_618 ? Math.round(activeWatchItem.fib_618).toLocaleString() : '-')}
          </span>
        </div>

        {/* Empty/Loading Overlay */}
        {(!chartData?.candles || chartData.candles.length === 0) && !isLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-xs pointer-events-none">
            실시간 캔들 데이터 수신 대기 중...
          </div>
        )}
      </div>
    </div>
  );
};
