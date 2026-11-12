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
  // 브라우저 WebSocket은 헤더를 못 붙이므로 토큰을 쿼리로 보낸다 (서버 미설정이면 무시됨)
  const token = readToken();
  const query = token && VALID_TOKEN.test(token) ? `?token=${encodeURIComponent(token)}` : '';
  return `${wsProto}//${wsHost}${getWsBase()}${cleanEndpoint}${query}`;
}

// ===== API 인증 (조회·변경 모두) =====
// 서버에 API_AUTH_TOKEN이 설정되면 X-API-Token 헤더가 필요하다. 401을 받으면 토큰을 한 번 입력받아
// 이 브라우저에만 저장하고 재시도한다. (서버 미설정이면 헤더가 무시되어 기존과 동일)
const TOKEN_KEY = 'kiwoom_api_token';

function readToken(): string {
  try { return window.localStorage.getItem(TOKEN_KEY) || ''; } catch { return memToken; }
}

// localStorage를 못 쓰는 환경(사생활 보호 모드 등)에서는 이 탭 메모리에만 보관
let memToken = '';

function saveToken(token: string): void {
  memToken = token;
  try { window.localStorage.setItem(TOKEN_KEY, token); } catch { /* 메모리 값만 사용 */ }
}

function clearToken(): void {
  memToken = '';
  try { window.localStorage.removeItem(TOKEN_KEY); } catch { /* 무시 */ }
}

// 헤더에 넣을 수 있는 값(공백 없는 ASCII)만 허용 — 한글 등이 섞이면 fetch가 요청 전에 TypeError로 실패한다 (10/26 사례)
const VALID_TOKEN = /^[!-~]+$/;

// 화면 첫 로드 때 조회 요청 여러 개가 동시에 401을 받아도 입력창은 한 번만 띄운다
let pendingAsk: Promise<string> | null = null;
// 조회(자동 폴링) 중 입력을 취소하면 3초마다 다시 묻지 않는다. 버튼 클릭(POST)은 다시 묻는다.
let askDeclined = false;

function askToken(): Promise<string> {
  if (!pendingAsk) {
    pendingAsk = Promise.resolve().then(() => {
      const entered = window.prompt('API 인증 토큰을 입력하세요 (이 브라우저에 저장됩니다)')?.trim() || '';
      if (entered && !VALID_TOKEN.test(entered)) {
        alert('토큰에 쓸 수 없는 문자(한글·공백 등)가 있습니다. 영문·숫자로 된 토큰 한 줄만 붙여 넣으세요.');
        askDeclined = true;
        return '';
      }
      askDeclined = !entered;
      if (entered) saveToken(entered);  // 바로 저장해 동시에 401을 받은 다른 요청이 다시 묻지 않게 함 (틀리면 재시도에서 지움)
      return entered;
    }).finally(() => { pendingAsk = null; });
  }
  return pendingAsk;
}

async function authFetch(endpoint: string, init: RequestInit, interactive: boolean): Promise<Response> {
  const send = (token: string) => fetch(getApiUrl(endpoint), {
    ...init,
    headers: { ...(init.headers as Record<string, string> | undefined), ...(token ? { 'X-API-Token': token } : {}) },
  });
  // 예전에 저장된 잘못된 값은 버리고 서버가 401을 주면 다시 입력받는다
  let used = readToken();
  if (used && !VALID_TOKEN.test(used)) {
    clearToken();
    used = '';
  }
  const res = await send(used);
  if (res.status !== 401) return res;
  // 그 사이 다른 요청에서 새 토큰이 입력됐으면 그것으로 재시도
  const latest = readToken();
  if (latest && latest !== used) return send(latest);
  if (used) clearToken();
  if (!interactive && askDeclined) return res;
  const entered = await askToken();
  if (!entered) return res;
  const retry = await send(entered);
  if (retry.status === 401 && readToken() === entered) clearToken();  // 서버가 받아들인 토큰만 남긴다
  return retry;
}

// 조회 API: 기존 fetch(getApiUrl(...)) 대신 사용
export function getApi(endpoint: string): Promise<Response> {
  return authFetch(endpoint, {}, false);
}

export async function postApi(endpoint: string, body?: unknown): Promise<Response> {
  return authFetch(endpoint, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }, true);
}
