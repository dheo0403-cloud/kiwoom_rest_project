import { useState, useEffect, useRef, useCallback } from 'react';
import { PortfolioSnapshot, Position, LogMessage, BotStatus, QuantPerformanceMetrics, MacroStatus } from '../types';
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

  // 3. 퀀트 핵심 성과 지표(KPI) 및 시장 레짐 상태
  const [quantPerformance, setQuantPerformance] = useState<QuantPerformanceMetrics>({
    daily_return_pct: 0.0,
    cumulative_return_pct: 0.0,
    win_rate_pct: 0.0,
    total_trades: 0,
    winning_trades: 0,
    losing_trades: 0,
    mdd_pct: 0.0,
    profit_factor: 0.0,
    total_profit: 0.0,
    total_loss: 0.0,
    recent_closed_trades: [],
    equity_history: []
  });

  const [macroStatus, setMacroStatus] = useState<MacroStatus>({
    regime: 'BULL_TREND',
    regime_reason: '시장 안정 상승 (정상 진입)',
    kodex200_change_rate: 0.0,
    vix_value: 18.0,
    usdkrw_change_pct: 0.0,
    market_filter_passed: true,
    kelly_multiplier: 1.0,
    is_buy_allowed: true,
    target_code: '005930',
    orderbook_imbalance: {
      imbalance_ratio: 0.25,
      total_bid_qty: 250000,
      total_ask_qty: 150000,
      bid_ask_spread: 100
    },
    volume_power: 128.5
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
  const emptyPositionsGraceCountRef = useRef<number>(0);

  // 지능형 포지션 병합 (In-Place Smart Merge + 낙관적 보존 Optimistic Retention)
  const smartMergePositions = (prevPositions: Position[], nextPositions: Position[]): Position[] => {
    // 1. 새 데이터가 비어있는 경우
    if (!nextPositions || nextPositions.length === 0) {
      if (!prevPositions || prevPositions.length === 0) {
        return [];
      }
      // 순간적인 네트워크/TR 폴링 1틱 튐으로 인한 깜빡임 방지: 3회 연속 빈 데이터 수신 시에만 0건으로 확정
      emptyPositionsGraceCountRef.current += 1;
      if (emptyPositionsGraceCountRef.current < 3) {
        return prevPositions; // 기존 포지션 낙관적 유지 (깜빡임 100% 방어)
      }
      return [];
    }

    // 새 데이터가 유효하게 존재하면 카운터 즉시 리셋
    emptyPositionsGraceCountRef.current = 0;

    if (!prevPositions || prevPositions.length === 0) {
      return nextPositions;
    }

    const prevMap = new Map<string, Position>();
    prevPositions.forEach(p => {
      if (p && p.code) prevMap.set(p.code, p);
    });

    let hasAnyChanges = false;
    const merged: Position[] = [];

    for (const nextPos of nextPositions) {
      if (!nextPos || !nextPos.code) continue;
      const prevPos = prevMap.get(nextPos.code);

      if (!prevPos) {
        // 신규 진입 종목
        merged.push(nextPos);
        hasAnyChanges = true;
      } else {
        // 기존 종목 속성 비교
        const isPriceSame = Math.abs(prevPos.current_price - nextPos.current_price) < 0.01;
        const isQtySame = prevPos.qty === nextPos.qty;
        const isBuyPriceSame = Math.abs(prevPos.buy_price - nextPos.buy_price) < 0.01;
        const isPnlSame = Math.abs((prevPos.pnl || 0) - (nextPos.pnl || 0)) < 0.01;
        const isYieldSame = Math.abs((prevPos.yield_rate || 0) - (nextPos.yield_rate || 0)) < 0.01;
        const isStageSame = prevPos.sell_stage === nextPos.sell_stage;

        if (isPriceSame && isQtySame && isBuyPriceSame && isPnlSame && isYieldSame && isStageSame) {
          // 변경 없음 -> 기존 객체 참조 유지 (Virtual DOM 리렌더링 완전 방지)
          merged.push(prevPos);
        } else {
          // 변경된 속성만 In-Place 병합
          merged.push({
            ...prevPos,
            ...nextPos,
            buy_price: nextPos.buy_price > 1 ? nextPos.buy_price : prevPos.buy_price,
            current_price: nextPos.current_price > 0 ? nextPos.current_price : prevPos.current_price
          });
          hasAnyChanges = true;
        }
      }
    }

    // 종목 수가 달라졌거나 변경사항이 있으면 새 merged 배열 반환, 아니면 이전 배열 참조 유지
    if (merged.length !== prevPositions.length || hasAnyChanges) {
      return merged;
    }
    return prevPositions;
  };

  // 실시간 포트폴리오 변경 시 실제 수치 변경이 있을 때만 안전 동기화
  const updateBothStates = useCallback((newSnap: PortfolioSnapshot) => {
    setPortfolio(prev => {
      const mergedPositions = smartMergePositions(prev.positions || [], newSnap.positions || []);
      const mergedSnap: PortfolioSnapshot = {
        ...newSnap,
        positions: mergedPositions
      };

      if (
        prev.total_asset === mergedSnap.total_asset &&
        prev.current_capital === mergedSnap.current_capital &&
        prev.invested_capital === mergedSnap.invested_capital &&
        prev.unrealized_pnl === mergedSnap.unrealized_pnl &&
        prev.total_yield_rate === mergedSnap.total_yield_rate &&
        prev.positions === mergedSnap.positions
      ) {
        return prev;
      }
      return mergedSnap;
    });

    setDisplayPortfolio(prev => {
      const mergedPositions = smartMergePositions(prev.positions || [], newSnap.positions || []);
      const mergedSnap: PortfolioSnapshot = {
        ...newSnap,
        positions: mergedPositions
      };

      if (
        prev.total_asset === mergedSnap.total_asset &&
        prev.current_capital === mergedSnap.current_capital &&
        prev.invested_capital === mergedSnap.invested_capital &&
        prev.unrealized_pnl === mergedSnap.unrealized_pnl &&
        prev.total_yield_rate === mergedSnap.total_yield_rate &&
        prev.positions === mergedSnap.positions
      ) {
        return prev;
      }
      return mergedSnap;
    });

    if (newSnap.quant_performance) {
      setQuantPerformance(newSnap.quant_performance);
    }
    if (newSnap.macro_status) {
      setMacroStatus(prev => ({ ...prev, ...newSnap.macro_status }));
    }
    const now = new Date();
    setLastDisplaySyncTime(now.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }));
  }, []);

  const portWsRef = useRef<WebSocket | null>(null);
  const logWsRef = useRef<WebSocket | null>(null);

  // REST API 초기 데이터 및 폴백 동기화
  const fetchRestData = useCallback(async () => {
    try {
      const isWsActive = isConnectedRef.current;
      const fetchList: Promise<any>[] = [
        fetch(getApiUrl('/status')),
        fetch(getApiUrl('/watchlist')),
        fetch(getApiUrl('/logs?limit=100')),
        fetch(getApiUrl('/quant/performance')),
        fetch(getApiUrl('/quant/status'))
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
      let perfRes: PromiseSettledResult<any>;
      let macroRes: PromiseSettledResult<any>;

      if (!isWsActive) {
        [portRes, statusRes, watchRes, logsRes, perfRes, macroRes] = results;
      } else {
        [statusRes, watchRes, logsRes, perfRes, macroRes] = results;
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
          last_synced_at: portData.last_synced_at,
          quant_performance: portData.quant_performance,
          macro_status: portData.macro_status
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

      if (perfRes && perfRes.status === 'fulfilled' && perfRes.value.ok) {
        const perfData = await perfRes.value.json();
        setQuantPerformance(perfData);
      }

      if (macroRes && macroRes.status === 'fulfilled' && macroRes.value.ok) {
        const macroData = await macroRes.value.json();
        setMacroStatus(macroData);
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
                last_synced_at: d.last_synced_at,
                quant_performance: d.quant_performance,
                macro_status: d.macro_status
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
  }, [fetchRestData, updateBothStates]);

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
    quantPerformance,    // 퀀트 핵심 성과 지표(KPI)
    macroStatus,         // 거시 시장 레짐 및 미시 수급 지표
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
