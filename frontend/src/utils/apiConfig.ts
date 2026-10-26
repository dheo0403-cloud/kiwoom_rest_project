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

// ===== 변경성 API(주문·봇 제어·파라미터) 인증 =====
// 서버에 API_AUTH_TOKEN이 설정되면 X-API-Token 헤더가 필요하다. 401을 받으면 토큰을 한 번 입력받아
// 이 브라우저에만 저장하고 재시도한다. (서버 미설정이면 헤더가 무시되어 기존과 동일)
const TOKEN_KEY = 'kiwoom_api_token';

function readToken(): string {
  try { return window.localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

function saveToken(token: string): void {
  try { window.localStorage.setItem(TOKEN_KEY, token); } catch { /* 저장 불가 환경이면 이번 요청에만 사용 */ }
}

function clearToken(): void {
  try { window.localStorage.removeItem(TOKEN_KEY); } catch { /* 무시 */ }
}

// 헤더에 넣을 수 있는 값(공백 없는 ASCII)만 허용 — 한글 등이 섞이면 fetch가 요청 전에 TypeError로 실패한다 (10/26 사례)
const VALID_TOKEN = /^[\x21-\x7E]+$/;

export async function postApi(endpoint: string, body?: unknown): Promise<Response> {
  const send = (token: string) => fetch(getApiUrl(endpoint), {
    method: 'POST',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { 'X-API-Token': token } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  // 예전에 저장된 잘못된 값은 버리고 서버가 401을 주면 다시 입력받는다
  let saved = readToken();
  if (saved && !VALID_TOKEN.test(saved)) {
    clearToken();
    saved = '';
  }
  const res = await send(saved);
  if (res.status !== 401) return res;
  clearToken();
  const entered = window.prompt('API 인증 토큰을 입력하세요 (이 브라우저에 저장됩니다)')?.trim();
  if (!entered) return res;
  if (!VALID_TOKEN.test(entered)) {
    alert('토큰에 쓸 수 없는 문자(한글·공백 등)가 있습니다. 영문·숫자로 된 토큰 한 줄만 붙여 넣으세요.');
    return res;
  }
  const retry = await send(entered);
  if (retry.status !== 401) saveToken(entered);  // 서버가 받아들인 토큰만 저장
  return retry;
}
