import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { KpiMetricsRow } from './components/KpiMetricsRow';
import { ActivePositionsBento } from './components/ActivePositionsBento';
import { LogViewer } from './components/LogViewer';
import { WatchlistBento } from './components/WatchlistBento';
import { StrategyControlsBento } from './components/StrategyControlsBento';
import { EmergencyModal } from './components/EmergencyModal';
import { ParamsModal } from './components/ParamsModal';
import { useTradingWebSocket } from './hooks/useWebSocket';
import { WatchlistItem } from './types';
import { getApiUrl } from './utils/apiConfig';

export function App() {
  const { portfolio, logs, botStatus, isConnected, refreshData, addManualLog, clearLogs } = useTradingWebSocket();

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [selectedStockCode, setSelectedStockCode] = useState<string>('');
  const [selectedStockName, setSelectedStockName] = useState<string>('');
  const [colorMode, setColorMode] = useState<'KRX' | 'GLOBAL'>('KRX');
  const [isKillSwitchOpen, setIsKillSwitchOpen] = useState<boolean>(false);
  const [isParamsOpen, setIsParamsOpen] = useState<boolean>(false);
  const [isTriggeringKill, setIsTriggeringKill] = useState<boolean>(false);

  // Fetch watchlist from REST API
  const fetchWatchlist = async () => {
    try {
      const res = await fetch(getApiUrl('/watchlist'));
      if (res.ok) {
        const data = await res.json();
        let items: WatchlistItem[] = [];
        if (Array.isArray(data)) {
          items = data;
        } else if (data && data.items) {
          if (Array.isArray(data.items)) {
            items = data.items;
          } else if (typeof data.items === 'object') {
            items = Object.values(data.items);
          }
        }
        if (items.length > 0) {
          setWatchlist(items);
        }
      }
    } catch (e) {
      console.warn("Failed to fetch watchlist:", e);
    }
  };

  useEffect(() => {
    fetchWatchlist();
    const interval = setInterval(fetchWatchlist, 4000);
    return () => clearInterval(interval);
  }, []);

  // 실제 보유 종목(1순위) 또는 감시 종목(2순위)으로 초기 종목 자동 선택
  useEffect(() => {
    if (!selectedStockCode) {
      if (portfolio.positions && portfolio.positions.length > 0) {
        setSelectedStockCode(portfolio.positions[0].code);
        setSelectedStockName(portfolio.positions[0].name);
      } else if (watchlist.length > 0) {
        setSelectedStockCode(watchlist[0].code);
        setSelectedStockName(watchlist[0].name);
      }
    }
  }, [portfolio.positions, watchlist, selectedStockCode]);

  const handleSelectStock = (code: string, name?: string) => {
    setSelectedStockCode(code);
    if (name) {
      setSelectedStockName(name);
    } else {
      const item = watchlist.find(w => w.code === code) || portfolio.positions.find(p => p.code === code);
      if (item) setSelectedStockName(item.name);
    }
  };

  const handleConfirmKillSwitch = async () => {
    setIsTriggeringKill(true);
    try {
      const res = await fetch(getApiUrl('/bot/emergency-stop'), { method: 'POST' });
      if (res.ok) {
        addManualLog('CRITICAL', '🚨 [EMERGENCY KILL-SWITCH] 전 포지션 긴급 시장가 청산이 실행되었습니다!');
        setIsKillSwitchOpen(false);
        refreshData();
      } else {
        alert('❌ 킬스위치 호출 실패');
      }
    } catch (e) {
      alert(`❌ API 서버 통신 에러: ${e}`);
    } finally {
      setIsTriggeringKill(false);
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-slate-950 text-slate-100 selection:bg-blue-500 selection:text-white">
      {/* Top Header */}
      <Header
        botStatus={botStatus}
        portfolio={portfolio}
        isConnected={isConnected}
        onOpenKillSwitch={() => setIsKillSwitchOpen(true)}
        onOpenParams={() => setIsParamsOpen(true)}
        onRefresh={() => {
          refreshData();
          fetchWatchlist();
        }}
        colorMode={colorMode}
        onToggleColorMode={() => setColorMode(prev => prev === 'KRX' ? 'GLOBAL' : 'KRX')}
      />

      {/* Main Cockpit Container */}
      <main className="flex-1 p-3.5 sm:p-5 max-w-[1920px] w-full mx-auto flex flex-col">
        {/* 1. 4-Card Top Bento KPI Metrics */}
        <KpiMetricsRow portfolio={portfolio} colorMode={colorMode} />

        {/* 2. Main Bento Grid Cockpit (2-Column Asymmetric Layout) */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3.5 flex-1">
          {/* Left Column (7/12: 58%): Realtime Log Viewer & Universe Table */}
          <div className="lg:col-span-7 flex flex-col gap-3.5">
            {/* Bento A: Terminal Live Log Viewer */}
            <div className="h-[430px]">
              <LogViewer
                logs={logs}
                portfolio={portfolio}
                onClearLogs={clearLogs}
              />
            </div>

            {/* Bento C: Top 30 Liquid Stocks Universe Table */}
            <div className="h-[360px]">
              <WatchlistBento
                watchlist={watchlist}
                onSelectStock={handleSelectStock}
                selectedStockCode={selectedStockCode}
                colorMode={colorMode}
              />
            </div>
          </div>

          {/* Right Column (5/12: 42%): Active Positions & Strategy Controls */}
          <div className="lg:col-span-5 flex flex-col gap-3.5">
            {/* Bento B: Active Positions Cards */}
            <div className="h-[430px]">
              <ActivePositionsBento
                positions={portfolio.positions}
                colorMode={colorMode}
                onSelectStock={handleSelectStock}
                selectedStockCode={selectedStockCode}
                onRefresh={refreshData}
              />
            </div>

            {/* Bento D: Strategy Parameters & Bot Controls */}
            <div className="h-[360px]">
              <StrategyControlsBento
                botStatus={botStatus}
                onRefresh={refreshData}
              />
            </div>
          </div>
        </div>
      </main>

      {/* Modals */}
      <EmergencyModal
        isOpen={isKillSwitchOpen}
        onClose={() => setIsKillSwitchOpen(false)}
        onConfirmKillSwitch={handleConfirmKillSwitch}
        isTriggering={isTriggeringKill}
      />

      <ParamsModal
        isOpen={isParamsOpen}
        onClose={() => setIsParamsOpen(false)}
        onRefresh={refreshData}
      />
    </div>
  );
}
export default App;
