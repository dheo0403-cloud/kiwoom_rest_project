/**
 * 런타임 환경에 따라 REST API 및 WebSocket의 기본 Base Path를 동적으로 결정합니다.
 * - 포털 서브패스 배포 시 (/kiwoom/): apiBase = '/kiwoom/api', wsBase = '/kiwoom/ws'
 * - 단독 로컬 개발/테스트 시 (/): apiBase = '/api', wsBase = '/ws'
 */

export function getApiBase(): string {
  if (typeof window === 'undefined') return '/api';
  const pathname = window.location.pathname;
  if (pathname.startsWith('/kiwoom')) {
    return '/kiwoom/api';
  }
  return '/api';
}

export function getWsBase(): string {
  if (typeof window === 'undefined') return '/ws';
  const pathname = window.location.pathname;
  if (pathname.startsWith('/kiwoom')) {
    return '/kiwoom/ws';
  }
  return '/ws';
}

export function getApiUrl(endpoint: string): string {
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  return `${getApiBase()}${cleanEndpoint}`;
}

export function getWsUrl(endpoint: string): string {
  if (typeof window === 'undefined') return `ws://localhost:8501/ws${endpoint}`;
  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsHost = window.location.host;
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  return `${wsProto}//${wsHost}${getWsBase()}${cleanEndpoint}`;
}
