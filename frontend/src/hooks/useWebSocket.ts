import { useState, useEffect, useRef, useCallback } from 'react';
import { PortfolioSnapshot, LogMessage, BotStatus } from '../types';
import { getApiUrl, getWsUrl } from '../utils/apiConfig';

export function useTradingWebSocket() {
  // 1. 실시간 계좌 상태 (Realtime Portfolio State)
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>({
    total_asset: 0,
    current_capital: 0,
    invested_capital: 0,
    stock_count: 0,
    unrealized_pnl: 0,
    total_yield_rate: 0,
    positions: []
  });

  // 2. 화면 표시 전용 상태 (Display State: 실시간 즉시 연동 및 부드러운 전환)
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
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [latencyMs, setLatencyMs] = useState<number>(0);
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

  const isConnectedRef = useRef<boolean>(false);
  isConnectedRef.current = isConnected;

  // 두 포트폴리오 스냅샷의 동일성 비교 (플리커링 및 불필요한 DOM 리렌더링 방지)
  const isSnapshotEqual = (a: PortfolioSnapshot, b: PortfolioSnapshot): boolean => {
    if (a.total_asset !== b.total_asset) return false;
    if (a.current_capital !== b.current_capital) return false;
    if (a.invested_capital !== b.invested_capital) return false;
    if (a.stock_count !== b.stock_count) return false;
    if (a.unrealized_pnl !== b.unrealized_pnl) return false;
    if (a.total_yield_rate !== b.total_yield_rate) return false;
    const aPos = a.positions || [];
    const bPos = b.positions || [];
    if (aPos.length !== bPos.length) return false;
    for (let i = 0; i < aPos.length; i++) {
      if (aPos[i].code !== bPos[i].code) return false;
      if (aPos[i].qty !== bPos[i].qty) return false;
      if (aPos[i].current_price !== bPos[i].current_price) return false;
      if (aPos[i].buy_price !== bPos[i].buy_price) return false;
    }
    return true;
  };

  // 실시간 포트폴리오 변경 시 실제 수치 변경이 있을 때만 안전 동기화
  const updateBothStates = useCallback((newSnap: PortfolioSnapshot) => {
    setPortfolio(prev => {
      if (isSnapshotEqual(prev, newSnap)) return prev;
      return newSnap;
    });
    setDisplayPortfolio(prev => {
      if (isSnapshotEqual(prev, newSnap)) return prev;
      return newSnap;
    });
    const now = new Date();
    setLastDisplaySyncTime(now.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }));
  }, []);

  const portWsRef = useRef<WebSocket | null>(null);
  const logWsRef = useRef<WebSocket | null>(null);

  // REST API 초기 데이터 및 폴백 동기화 (WebSocket 활성화 중에는 상태 경합 방지를 위해 REST 포트폴리오 덮어쓰기 스킵)
  const fetchRestData = useCallback(async () => {
    try {
      const isWsActive = isConnectedRef.current;
      const fetchList: Promise<any>[] = [
        fetch(getApiUrl('/status')),
        fetch(getApiUrl('/watchlist')),
        fetch(getApiUrl('/logs?limit=100'))
      ];

      // WebSocket이 끊겼거나 초기 로드일 때만 REST /portfolio를 폴백으로 요청
      if (!isWsActive) {
        fetchList.unshift(fetch(getApiUrl('/portfolio')));
      }

      const results = await Promise.allSettled(fetchList);
      let portRes: PromiseSettledResult<any> | null = null;
      let statusRes: PromiseSettledResult<any>;
      let watchRes: PromiseSettledResult<any>;
      let logsRes: PromiseSettledResult<any>;

      if (!isWsActive) {
        [portRes, statusRes, watchRes, logsRes] = results;
      } else {
        [statusRes, watchRes, logsRes] = results;
      }

      if (portRes && portRes.status === 'fulfilled' && portRes.value.ok) {
        const portData = await portRes.value.json();
        const rawPositions = Array.isArray(portData.positions) ? portData.positions : [];
        const newSnap: PortfolioSnapshot = {
          total_asset: portData.total_asset > 0 ? portData.total_asset : (portData.current_capital || 0),
          current_capital: portData.current_capital || 0,
          invested_capital: portData.invested_capital || 0,
          stock_count: rawPositions.length,
          unrealized_pnl: portData.unrealized_pnl ?? 0,
          total_yield_rate: portData.total_yield_rate ?? 0,
          positions: rawPositions,
          last_synced_at: portData.last_synced_at
        };

        updateBothStates(newSnap);
      }

      if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
        const statusData = await statusRes.value.json();
        setBotStatus(prev => {
          if (prev.running === statusData.running && prev.circuit_breaker_open === statusData.circuit_breaker_open) {
            return prev;
          }
          return statusData;
        });
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
  }, [updateBothStates]);

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
              const newSnap: PortfolioSnapshot = {
                total_asset: d.total_asset > 0 ? d.total_asset : (d.current_capital || 0),
                current_capital: d.current_capital || 0,
                invested_capital: d.invested_capital || d.invested_eval || 0,
                stock_count: rawPositions.length,
                unrealized_pnl: d.unrealized_pnl ?? d.total_pnl ?? 0,
                total_yield_rate: d.total_yield_rate ?? d.total_yield ?? 0,
                positions: rawPositions,
                last_synced_at: d.last_synced_at
              };
              updateBothStates(newSnap);
            }
          } catch (e) {
            console.error("Portfolio WS parse error:", e);
          }
        };

        portWs.onclose = () => {
          setIsConnected(false);
          setTimeout(connectSockets, 10000); // 10초 재연결 백오프로 깜빡임 방지
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
  }, [fetchRestData]);

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
