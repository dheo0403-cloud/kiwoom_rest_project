"""
Gate Info:
- Importers/callers: kiwoom_rest_project/main_rest_async.py, kiwoom_rest_project/api_server.py, kiwoom_rest_project/test_async_core.py
- Affected API: Kiwoom OpenAPI REST (/oauth2/token, /api/dostk/ordr, /api/dostk/stkinfo, /api/dostk/mrkcond, /api/dostk/acnt, /api/dostk/rkinfo, /api/dostk/chart)
- Data schemas: JSON API request/response with PriorityQueue and TokenBucket Rate Limiting
- User's verbatim instruction: "미안 AKS에 대한 부분은 삭제해줘 마이그레이션 후 별도로 문의할께"
"""
import asyncio
import os
import time
import dataclasses
from typing import Any, Dict, Optional, Tuple
from enum import IntEnum
import aiohttp
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

class RequestPriority(IntEnum):
    CRITICAL = 0   # 하드 스탑로스, 트레일링 스탑, 긴급 시장가 매도
    HIGH = 1       # 정규 매수 주문, 단계별 분할 익절 주문
    MEDIUM = 2     # 계좌 잔고 동기화, 예수금/포지션 정합성 검증
    LOW = 3        # 감시 종목 시세 조회, 과거 일봉/분봉 차트 수집

@dataclasses.dataclass(order=True)
class QueuedRequest:
    priority: int
    timestamp: float
    api_id: str = dataclasses.field(compare=False)
    url: str = dataclasses.field(compare=False)
    payload: Dict[str, Any] = dataclasses.field(compare=False)
    headers_override: Optional[Dict[str, str]] = dataclasses.field(default=None, compare=False)
    future: asyncio.Future = dataclasses.field(default=None, compare=False)
    retries: int = dataclasses.field(default=3, compare=False)

class TokenBucketRateLimiter:
    """
    토큰 버킷 기반 비동기 Rate Limiter
    - rate: 초당 생성 토큰 수 (TPS, 예: 실전 5.0, 모의투자 2.0)
    - capacity: 최대 버스트 용량
    """
    def __init__(self, rate: float = 5.0, capacity: float = 5.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0, is_priority: bool = False):
        async with self._lock:
            while True:
                now = time.time()
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                # 우선순위 주문(Priority 0)인 경우 토큰이 최소치(0.5) 이상이면 즉시 통과
                required = 0.5 if is_priority else tokens
                if self.tokens >= required:
                    self.tokens -= tokens
                    return

                # 토큰 충전까지 필요한 시간 대기
                wait_time = max(0.01, (tokens - self.tokens) / self.rate)
                await asyncio.sleep(wait_time)

class CircuitBreaker:
    """
    429(Rate Limit) 및 5xx 서버 장애 대비 서킷 브레이커
    """
    def __init__(self, failure_threshold: int = 3, recovery_time: float = 5.0):
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.consecutive_failures = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF-OPEN
        self.opened_at = 0.0

    def record_success(self):
        self.consecutive_failures = 0
        self.state = "CLOSED"

    def record_failure(self, is_rate_limit: bool = False):
        self.consecutive_failures += 1
        if is_rate_limit or self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = time.time()
            backoff = 3.0 if is_rate_limit else self.recovery_time
            return backoff
        return 0.5

    def can_proceed(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.opened_at > self.recovery_time:
                self.state = "HALF-OPEN"
                return True
            return False
        return True  # HALF-OPEN

class AsyncKiwoomClient:
    """
    고도화된 비동기 키움증권 REST 클라이언트
    - 4단계 우선순위 큐 (PriorityQueue)
    - Token Bucket Rate Limiter
    - 서킷 브레이커 및 자동 재시도
    - 비동기 워커 디스패처
    """
    def __init__(self, is_demo: bool = True, max_tps: float = None):
        self.is_demo = is_demo
        if is_demo:
            self.base_url = "https://mockapi.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_MOCK_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_MOCK_APP_SECRET")
            self.account = os.getenv("KIWOOM_MOCK_ACCOUNT")
            self.password = os.getenv("KIWOOM_MOCK_PASSWORD")
            self.mode = "MOCK"
            default_tps = 2.0  # 모의투자는 초당 2건 제한
        else:
            self.base_url = "https://api.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_REAL_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_REAL_APP_SECRET")
            self.account = os.getenv("KIWOOM_REAL_ACCOUNT")
            self.password = os.getenv("KIWOOM_REAL_PASSWORD")
            self.mode = "REAL"
            default_tps = 3.5  # 실전은 키움 게이트웨이 초당 5건 한도 대비 안전하게 3.5 TPS

        self.rate_limiter = TokenBucketRateLimiter(rate=max_tps or default_tps, capacity=2.0)
        self.circuit_breaker = CircuitBreaker()
        self.queue: asyncio.PriorityQueue[QueuedRequest] = asyncio.PriorityQueue()
        self.access_token: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """클라이언트 및 비동기 디스패처 워커 시작"""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(timeout=timeout)
        self._running = True
        self._worker_task = asyncio.create_task(self._dispatcher_worker())
        await self.get_access_token()
        print(f"🚀 [AsyncKiwoomClient] {self.mode} 엔진 및 우선순위 디스패처 가동 완료 (TPS: {self.rate_limiter.rate})")

    async def stop(self):
        """클라이언트 종료 및 큐 정리"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self.session and not self.session.closed:
            await self.session.close()
        print("🛑 [AsyncKiwoomClient] 세션 및 디스패처 정상 종료")

    async def get_access_token(self) -> Optional[str]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }

        try:
            async with self.session.post(url, headers=headers, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                self.access_token = data.get("access_token") or data.get("token")
                if self.access_token:
                    print(f"✅ [{self.mode}] OAuth2 토큰 갱신 완료")
                    return self.access_token
        except Exception as e:
            print(f"❌ [{self.mode}] 토큰 발급 에러: {e}")
        return None

    def _get_headers(self, api_id: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "api-id": api_id
        }

    async def _dispatcher_worker(self):
        """우선순위 큐로부터 요청을 꺼내 RateLimiter 제어 하에 처리하는 코어 워커"""
        while self._running:
            try:
                req: QueuedRequest = await self.queue.get()

                # 서킷 브레이커 체크
                if not self.circuit_breaker.can_proceed():
                    await asyncio.sleep(0.5)

                # 토큰 획득 (긴급 주문의 경우 우선 통과)
                is_urgent = req.priority == RequestPriority.CRITICAL
                await self.rate_limiter.acquire(tokens=1.0, is_priority=is_urgent)

                # HTTP 요청 실행 (백그라운드 태스크로 분기하여 네트워크 I/O 동안 다음 큐 처리 대기하지 않음)
                asyncio.create_task(self._execute_request(req))
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [Dispatcher Error] {e}")
                await asyncio.sleep(0.1)

    async def _execute_request(self, req: QueuedRequest):
        """실제 HTTP 요청 전송 및 에러/재시도 핸들링"""
        if not self.access_token:
            await self.get_access_token()

        headers = self._get_headers(req.api_id)
        if req.headers_override:
            headers.update(req.headers_override)

        for attempt in range(req.retries):
            try:
                async with self.session.post(req.url, headers=headers, json=req.payload) as response:
                    if response.status == 429:
                        backoff = self.circuit_breaker.record_failure(is_rate_limit=True)
                        print(f"⚠️ [429 Rate Limit] {req.api_id} 백오프 {backoff}초 후 재시도 ({attempt+1}/{req.retries})")
                        await asyncio.sleep(backoff)
                        continue

                    if response.status >= 500:
                        self.circuit_breaker.record_failure()
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue

                    response.raise_for_status()
                    data = await response.json()
                    self.circuit_breaker.record_success()

                    if not req.future.done():
                        req.future.set_result((data, response.headers))
                    return

            except aiohttp.ClientError as e:
                if attempt == req.retries - 1:
                    if not req.future.done():
                        req.future.set_result((None, None))
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if not req.future.done():
                    req.future.set_result((None, None))
                return

        if not req.future.done():
            req.future.set_result((None, None))

    async def request(self, api_id: str, url: str, payload: Dict[str, Any],
                      priority: RequestPriority = RequestPriority.LOW,
                      headers_override: Optional[Dict[str, str]] = None,
                      retries: int = 3) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """우선순위 큐에 요청 등록 및 결과 비동기 대기"""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        queued_req = QueuedRequest(
            priority=int(priority),
            timestamp=time.time(),
            api_id=api_id,
            url=url,
            payload=payload,
            headers_override=headers_override,
            future=fut,
            retries=retries
        )
        await self.queue.put(queued_req)
        return await fut

    # ================= 비즈니스 API 엔드포인트 =================

    async def send_order(self, code: str, qty: int, price: int,
                         order_type: str = "00", side: str = "BUY",
                         priority: RequestPriority = RequestPriority.HIGH) -> Optional[Dict[str, Any]]:
        """
        주문 발송 (기본 Priority: HIGH, 긴급 매도시 CRITICAL 지정 가능)
        """
        url = f"{self.base_url}/api/dostk/ordr"
        api_id = "kt10000" if side == "BUY" else "kt10001"
        payload = {
            "dmst_stex_tp": "KRX",
            "stk_cd": code,
            "ord_qty": str(int(qty)),
            "ord_uv": str(int(price)),
            "trde_tp": order_type,   # "00": 보통, "03": 시장가
            "cond_uv": "0"
        }
        data, _ = await self.request(api_id, url, payload, priority=priority, retries=5)
        if not data:
            print(f"❌ [ORDER_FAIL] 주문 응답 없음 ({side} {code} {qty}주)")
            return None

        rt_cd = data.get('rt_cd') if data.get('rt_cd') is not None else data.get('return_code')
        if str(rt_cd) == '0':
            print(f"✅ [ORDER_SUCCESS] 주문 완료 ({side} {code} {qty}주 @ {price}원)")
        else:
            msg = data.get('msg1') or data.get('return_msg') or '주문 거절'
            print(f"⚠️ [ORDER_REJECTED] 주문 거절 ({side} {code}): {msg}")
        return data

    async def get_price(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """현재가 시세 조회"""
        url = f"{self.base_url}/api/dostk/stkinfo"
        payload = {"stk_cd": code}
        data, _ = await self.request("ka10001", url, payload, priority=priority)
        return data

    async def get_orderbook(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """호가 잔량 조회"""
        url = f"{self.base_url}/api/dostk/mrkcond"
        payload = {"stk_cd": code}
        data, _ = await self.request("ka10004", url, payload, priority=priority)
        return data

    async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM) -> Optional[Dict[str, Any]]:
        """계좌 잔고 및 예수금 조회"""
        url = f"{self.base_url}/api/dostk/acnt"
        payload = {
            "dmst_stex_tp": "KRX",
            "accNo": self.account,
            "accPwd": self.password
        }
        data, _ = await self.request("kt00005", url, payload, priority=priority)
        return data

    async def get_top_trading_value(self, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """거래대금 상위 종목 조회 (KRX/통합 다중 거래소 파라미터 방어 지원)"""
        url = f"{self.base_url}/api/dostk/rkinfo"
        stex = "1" if self.mode == "MOCK" else "3"
        payload = {
            "mrkt_tp": "000",
            "mang_stk_incls": "0",
            "stex_tp": stex
        }
        data, _ = await self.request("ka10032", url, payload, priority=priority)
        # 만약 통합(3) 조회 응답에 종목 리스트가 없다면 KRX(1)로 1회 안전 재시도
        if not data or (isinstance(data, dict) and not any(k in data for k in ['trde_prica_upper', 'output', 'Output', 'list', 'trde_val_upper', 'data'])):
            if stex != "1":
                payload["stex_tp"] = "1"
                data, _ = await self.request("ka10032", url, payload, priority=priority)
        return data

    async def get_daily_chart(self, code: str, base_dt: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """일봉 차트 조회"""
        url = f"{self.base_url}/api/dostk/chart"
        payload = {
            "stk_cd": code,
            "base_dt": base_dt,
            "upd_stkpc_tp": "1"
        }
        data, _ = await self.request("ka10081", url, payload, priority=priority)
        return data

    async def get_minute_chart(self, code: str, base_dt: str, next_key: Optional[str] = None,
                               priority: RequestPriority = RequestPriority.LOW) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """1분봉 차트 조회"""
        url = f"{self.base_url}/api/dostk/chart"
        payload = {
            "stk_cd": code,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": base_dt
        }
        headers_override = {"next-key": next_key} if next_key else None
        return await self.request("ka10080", url, payload, priority=priority, headers_override=headers_override)
