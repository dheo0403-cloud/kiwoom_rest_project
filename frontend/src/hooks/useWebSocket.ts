import { useState, useEffect, useRef, useCallback } from 'react';
import { PortfolioSnapshot, LogMessage, BotStatus } from '../types';
import { getApiUrl, getWsUrl } from '../utils/apiConfig';

export function useTradingWebSocket() {
  // 1. Engine용 실시간 계좌 상태 (실제 매매/내부 로직용 초저지연 상태)
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>({
    total_asset: 0,
    current_capital: 0,
    invested_capital: 0,
    stock_count: 0,
    unrealized_pnl: 0,
    total_yield_rate: 0,
    positions: []
  });

  // 2. 화면 표시 전용 상태 (Display State: 10분 주기 갱신으로 깜빡임 및 빈번한 리렌더링 완전 방지)
  const [displayPortfolio, setDisplayPortfolio] = useState<PortfolioSnapshot>({
    total_asset: 0,
    current_capital: 0,
    invested_capital: 0,
    stock_count: 0,
    unrealized_pnl: 0,
    total_yield_rate: 0,
    positions: []
  });

  const [lastDisplaySyncTime, setLastDisplaySyncTime] = useState<string>('');
  const latestPortfolioRef = useRef<PortfolioSnapshot>(portfolio);
  latestPortfolioRef.current = portfolio;

  // 10분 주기 화면 표시 상태 동기화 함수
  const syncDisplayState = useCallback(() => {
    const cur = latestPortfolioRef.current;
    if (cur.total_asset > 0 || (cur.positions && cur.positions.length > 0)) {
      setDisplayPortfolio({ ...cur });
      const now = new Date();
      setLastDisplaySyncTime(now.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit' }));
    }
  }, []);

  // 첫 데이터 수신 시 즉시 화면 표시 상태 1회 동기화
  useEffect(() => {
    if (displayPortfolio.total_asset === 0 && (portfolio.total_asset > 0 || portfolio.positions.length > 0)) {
      setDisplayPortfolio({ ...portfolio });
      const now = new Date();
      setLastDisplaySyncTime(now.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit' }));
    }
  }, [portfolio, displayPortfolio.total_asset]);

  // 10분(600,000ms) 간격 디스플레이 갱신 타이머
  useEffect(() => {
    const displayInterval = setInterval(syncDisplayState, 10 * 60 * 1000); // 10분 주기
    return () => clearInterval(displayInterval);
  }, [syncDisplayState]);

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
      const [portRes, statusRes, watchRes, logsRes] = await Promise.allSettled([
        fetch(getApiUrl('/portfolio')),
        fetch(getApiUrl('/status')),
        fetch(getApiUrl('/watchlist')),
        fetch(getApiUrl('/logs?limit=100'))
      ]);

      if (portRes.status === 'fulfilled' && portRes.value.ok) {
        const portData = await portRes.value.json();
        const rawPositions = Array.isArray(portData.positions) ? portData.positions : [];
        setPortfolio(prev => {
          // WS 연결 중이고 이미 유효한 자산 데이터가 있으면 REST 응답으로 덮어쓰지 않아 요동 방지
          if (isConnected && prev.total_asset > 0) {
            return prev;
          }
          return {
            total_asset: portData.total_asset > 0 ? portData.total_asset : prev.total_asset,
            current_capital: portData.current_capital > 0 ? portData.current_capital : prev.current_capital,
            invested_capital: portData.invested_capital || prev.invested_capital,
            stock_count: rawPositions.length,
            unrealized_pnl: portData.unrealized_pnl ?? prev.unrealized_pnl,
            total_yield_rate: portData.total_yield_rate ?? prev.total_yield_rate,
            positions: rawPositions,
            last_synced_at: portData.last_synced_at || prev.last_synced_at
          };
        });
      }

      if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
        const statusData = await statusRes.value.json();
        setBotStatus(statusData);
      }

      if (logsRes.status === 'fulfilled' && logsRes.value.ok) {
        const logsData = await logsRes.value.json();
        if (logsData && Array.isArray(logsData.logs) && logsData.logs.length > 0) {
          setLogs(prev => {
            if (prev.length === 0) return logsData.logs;
            return prev;
          });
        }
      }
    } catch (e) {
      console.warn("REST fallback fetch error:", e);
    }
  }, [isConnected]);

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

      const portUrl = getWsUrl('/portfolio');
      const logUrl = getWsUrl('/logs');

      try {
        portWs = new WebSocket(portUrl);
        logWs = new WebSocket(logUrl);

        portWs.onopen = () => {
          setIsConnected(true);
        };

        portWs.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if ((msg.type === 'PORTFOLIO_UPDATE' || msg.type === 'PORTFOLIO_INIT') && msg.data) {
              const d = msg.data;
              const rawPositions = Array.isArray(d.positions) ? d.positions : [];
              setPortfolio({
                total_asset: d.total_asset > 0 ? d.total_asset : (d.current_capital || 0),
                current_capital: d.current_capital || 0,
                invested_capital: d.invested_capital || d.invested_eval || 0,
                stock_count: rawPositions.length,
                unrealized_pnl: d.unrealized_pnl ?? d.total_pnl ?? 0,
                total_yield_rate: d.total_yield_rate ?? d.total_yield ?? 0,
                positions: rawPositions,
                last_synced_at: d.last_synced_at
              });
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
      timestamp: new Date().toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false }),
      level,
      message
    };
    setLogs(prev => [newLog, ...prev.slice(0, 99)]);
  }, []);

  const clearLogs = useCallback(() => {
    setLogs([]);
  }, []);

  // 수동 새로고침 시 실시간 조회 및 화면 표시 상태 즉시 동기화
  const handleRefresh = useCallback(async () => {
    await fetchRestData();
    syncDisplayState();
  }, [fetchRestData, syncDisplayState]);

  return {
    portfolio,           // 실시간 엔진용 포트폴리오
    displayPortfolio,    // 화면 표출 전용 포트폴리오 (10분 주기 갱신으로 깜빡임 방지)
    lastDisplaySyncTime,
    logs,
    botStatus,
    isConnected,
    latencyMs,
    addManualLog,
    clearLogs,
    refreshData: handleRefresh
  };
}
