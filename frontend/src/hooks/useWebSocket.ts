import { useState, useEffect, useRef, useCallback } from 'react';
import { PortfolioSnapshot, LogMessage, BotStatus } from '../types';

export function useTradingWebSocket() {
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>({
    total_asset: 0,
    current_capital: 0,
    invested_capital: 0,
    stock_count: 0,
    unrealized_pnl: 0,
    total_yield_rate: 0,
    positions: []
  });
  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [botStatus, setBotStatus] = useState<BotStatus>({
    running: true,
    is_demo: false,
    market_filter_passed: true,
    kodex200_change_rate: 0.0,
    watchlist_count: 0,
    active_positions_count: 0,
    circuit_breaker_open: false
  });
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [latencyMs, setLatencyMs] = useState<number>(0);

  const portWsRef = useRef<WebSocket | null>(null);
  const logWsRef = useRef<WebSocket | null>(null);

  // REST API 초기 데이터 및 폴백 동기화
  const fetchRestData = useCallback(async () => {
    try {
      const [portRes, statusRes, watchRes] = await Promise.allSettled([
        fetch('/api/portfolio'),
        fetch('/api/status'),
        fetch('/api/watchlist')
      ]);

      if (portRes.status === 'fulfilled' && portRes.value.ok) {
        const portData = await portRes.value.json();
        setPortfolio(prev => ({
          ...prev,
          total_asset: portData.total_asset || prev.total_asset,
          current_capital: portData.current_capital || prev.current_capital,
          invested_capital: portData.invested_capital || prev.invested_capital,
          stock_count: portData.stock_count ?? (portData.positions?.length || 0),
          unrealized_pnl: portData.unrealized_pnl || 0,
          total_yield_rate: portData.total_yield_rate || 0,
          positions: portData.positions || []
        }));
      }

      if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
        const statusData = await statusRes.value.json();
        setBotStatus(statusData);
      }
    } catch (e) {
      console.warn("REST fallback fetch error:", e);
    }
  }, []);

  useEffect(() => {
    fetchRestData();
    const restInterval = setInterval(fetchRestData, 3000);

    // WebSocket 프로토콜 결정
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost = window.location.host;

    let portWs: WebSocket | null = null;
    let logWs: WebSocket | null = null;
    let isSubscribed = true;

    function connectSockets() {
      if (!isSubscribed) return;

      const portUrl = `${wsProto}//${wsHost}/ws/portfolio`;
      const logUrl = `${wsProto}//${wsHost}/ws/logs`;

      try {
        portWs = new WebSocket(portUrl);
        logWs = new WebSocket(logUrl);

        portWs.onopen = () => {
          setIsConnected(true);
        };

        portWs.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'PORTFOLIO_UPDATE' && msg.data) {
              setPortfolio(msg.data);
            }
          } catch (e) {
            console.error("Portfolio WS parse error:", e);
          }
        };

        portWs.onclose = () => {
          setIsConnected(false);
          setTimeout(connectSockets, 3000);
        };

        portWs.onerror = () => {
          setIsConnected(false);
        };

        logWs.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'LOG_EVENT' && msg.data) {
              setLogs(prev => [msg.data, ...prev.slice(0, 99)]);
            } else if (msg.type === 'LOGS_INIT' && Array.isArray(msg.data)) {
              setLogs(msg.data);
            }
          } catch (e) {
            console.error("Log WS parse error:", e);
          }
        };

        portWsRef.current = portWs;
        logWsRef.current = logWs;
      } catch (err) {
        console.warn("WebSocket init error:", err);
        setIsConnected(false);
      }
    }

    connectSockets();

    return () => {
      isSubscribed = false;
      clearInterval(restInterval);
      if (portWsRef.current) portWsRef.current.close();
      if (logWsRef.current) logWsRef.current.close();
    };
  }, [fetchRestData]);

  const addManualLog = useCallback((level: LogMessage['level'], message: string) => {
    const newLog: LogMessage = {
      id: Math.random().toString(36).substr(2, 9),
      timestamp: new Date().toTimeString().split(' ')[0],
      level,
      message
    };
    setLogs(prev => [newLog, ...prev.slice(0, 99)]);
  }, []);

  return {
    portfolio,
    logs,
    botStatus,
    isConnected,
    latencyMs,
    addManualLog,
    refreshData: fetchRestData
  };
}
