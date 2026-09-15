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
from typing import Any, Dict, Optional, Tuple, List
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

    @property
    def is_open(self) -> bool:
        """서킷이 열려있는지(OPEN 상태) 여부 반환"""
        return self.state == "OPEN"

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
    def __init__(self, is_demo: Optional[bool] = None, max_tps: float = None):
        if is_demo is None:
            # 환경변수 IS_REAL 또는 KIWOOM_MODE 기반 결정 (기본값: 실전투자 REAL)
            env_is_mock = os.getenv("IS_REAL", "true").lower() in ("false", "0", "no") or os.getenv("KIWOOM_MODE", "REAL").upper() in ("MOCK", "DEMO")
            is_demo = env_is_mock
        self.is_demo = is_demo
        if is_demo:
            self.base_url = "https://mockapi.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_MOCK_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_MOCK_APP_SECRET")
            self.account = str(os.getenv("KIWOOM_MOCK_ACCOUNT") or "").replace('-', '').strip()
            self.password = str(os.getenv("KIWOOM_MOCK_PASSWORD") or os.getenv("KIWOOM_PASSWORD") or "0000").strip()
            self.mode = "MOCK"
            default_tps = 2.0  # 모의투자는 초당 2건 제한
        else:
            self.base_url = "https://api.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_REAL_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_REAL_APP_SECRET")
            self.account = str(os.getenv("KIWOOM_REAL_ACCOUNT") or "").replace('-', '').strip()
            self.password = str(os.getenv("KIWOOM_REAL_PASSWORD") or os.getenv("KIWOOM_PASSWORD") or "0000").strip()
            self.mode = "REAL"
            default_tps = 3.5  # 실전은 키움 게이트웨이 초당 5건 한도 대비 안전하게 3.5 TPS

        self.rate_limiter = TokenBucketRateLimiter(rate=max_tps or default_tps, capacity=2.0)
        self.circuit_breaker = CircuitBreaker()
        self.queue: asyncio.PriorityQueue[QueuedRequest] = asyncio.PriorityQueue()
        self.access_token: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self.realtime_registered_codes: List[str] = []

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
        """실제 HTTP 요청 전송 및 에러/재시도 핸들링 (Raw JSON 에러 투명화)"""
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

                    # 4xx 또는 200 OK 모두 json/text 안전 파싱하여 에러 내용 보존
                    try:
                        data = await response.json()
                    except Exception:
                        text_body = await response.text()
                        data = {"raw_text": text_body, "http_status": response.status}

                    if response.status != 200:
                        msg = data.get('msg1') or data.get('return_msg') or data.get('raw_text') or 'HTTP Error'
                        print(f"❌ [API_HTTP_{response.status}] {req.api_id} 호출 실패: {msg} (Payload: {req.payload})")
                        if attempt == req.retries - 1:
                            if not req.future.done():
                                req.future.set_result((data, response.headers))
                            return
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue

                    self.circuit_breaker.record_success()
                    if not req.future.done():
                        req.future.set_result((data, response.headers))
                    return

            except aiohttp.ClientError as e:
                print(f"⚠️ [Network ClientError] {req.api_id} ({attempt+1}/{req.retries}): {e}")
                if attempt == req.retries - 1:
                    if not req.future.done():
                        req.future.set_result((None, None))
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception as e:
                print(f"⚠️ [Request Exception] {req.api_id}: {e}")
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
        - 계좌번호(accNo) 및 비밀번호(accPwd) 필수 포함
        - 지정가(00) 및 시장가(03) 정규 파라미터 보장
        - [안전 가드] BUY 주문은 항상 지정가(00)로 강제 (시장가 매수 시 ETF 증거금 부족(855056) 방지)
        """
        clean_code = str(code).replace('A', '').strip()

        # 매수(BUY) 주문 시 시장가(03) 사용을 원천 차단 — ETF 증거금 부족(855056) 에러 방지
        if side.upper() == "BUY" and str(order_type) == "03":
            print(f"🛡️ [send_order 가드] BUY 시장가→지정가 자동 전환: {clean_code} @ {price}원 (증거금 부족 방지)")
            order_type = "00"
        url = f"{self.base_url}/api/dostk/ordr"
        api_id = "kt10000" if side == "BUY" else "kt10001"
        payload = {
            "dmst_stex_tp": "KRX",
            "accNo": self.account,
            "accPwd": self.password,
            "stk_cd": clean_code,
            "ord_qty": str(int(qty)),
            "ord_uv": str(int(price)),
            "trde_tp": str(order_type),   # "00": 보통(지정가), "03": 시장가
            "cond_uv": "0"
        }
        data, _ = await self.request(api_id, url, payload, priority=priority, retries=5)
        if not data:
            print(f"❌ [ORDER_FAIL] 주문 응답 없음 ({side} {clean_code} {qty}주 @ {price}원)")
            return None

        rt_cd = data.get('rt_cd') if data.get('rt_cd') is not None else data.get('return_code')
        if str(rt_cd) == '0':
            print(f"✅ [ORDER_SUCCESS] 주문 완료 ({side} {clean_code} {qty}주 @ {price}원, 계좌: {self.account})")
        else:
            msg = data.get('msg1') or data.get('return_msg') or '주문 거절'
            print(f"⚠️ [ORDER_REJECTED] 주문 거절 ({side} {clean_code} @ {price}원, 계좌: {self.account}): {msg}")
        return data

    async def get_price(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """현재가 시세 조회"""
        clean_code = str(code).replace('A', '').strip()
        url = f"{self.base_url}/api/dostk/stkinfo"
        payload = {"stk_cd": clean_code}
        data, _ = await self.request("ka10001", url, payload, priority=priority)
        return data

    async def get_orderbook(self, code: str, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """호가 잔량 조회"""
        clean_code = str(code).replace('A', '').strip()
        url = f"{self.base_url}/api/dostk/mrkcond"
        payload = {"stk_cd": clean_code}
        data, _ = await self.request("ka10004", url, payload, priority=priority)
        return data

    async def get_deposit_info(self, priority: RequestPriority = RequestPriority.MEDIUM) -> Optional[Dict[str, Any]]:
        """
        예수금 상세 현황 조회 (kt00001 / OPW00001 다중 qry_tp 자동 순차 조회)
        - qry_tp 3(D+2추정) -> 2(추정) -> 1(단순) -> 0(전체)
        """
        url = f"{self.base_url}/api/dostk/acnt"
        best_data: Optional[Dict[str, Any]] = None

        for q_tp in ["3", "2", "1", "0"]:
            payload = {
                "dmst_stex_tp": "KRX",
                "accNo": self.account,
                "accPwd": self.password,
                "qry_tp": q_tp
            }
            data, _ = await self.request("kt00001", url, payload, priority=priority)
            if data and isinstance(data, dict):
                # 유의미한 D+2 예수금이나 복수 필드가 있는지 확인
                if not best_data:
                    best_data = data
                for k in ['d2_deposit', 'd2_auto_amt', 'd2_prvs_rcdl_amt', 'ord_psbl_cash', 'output1']:
                    if k in data and data[k]:
                        return data

        return best_data

    async def get_account_balance(self, priority: RequestPriority = RequestPriority.MEDIUM) -> Optional[Dict[str, Any]]:
        """계좌 잔고 및 보유 포지션 조회 (전체 보유종목 kt00018 합산 최우선 4대 TR 다중 스캐너)"""
        url = f"{self.base_url}/api/dostk/acnt"
        merged_data: Dict[str, Any] = {}
        has_pos = False

        pos_list_keys = [
            'output2', 'Output2', 'output_2', 'acnt_dtl_list', 'holdings',
            'stk_list', 'item_list', 'list', 'data', 'grid', 'table',
            'rows', 'items', 'output', 'Output', 'stocks', 'positions',
            '종목리스트', '잔고리스트'
        ]
        pos_code_keys = [
            'stk_cd', 'pdno', 'code', 'expcode', 'mksc_shrn_iscd', 'shcode',
            'jongmok_code', 'item_code', 'stck_shrn_iscd', 'item_cd', 'prdt_cd',
            'stk_code', 'iscd', 'jong_cd', 'stck_cd', 'stk_no', 'isu_cd',
            '종목코드', '종목번호', '단축코드', '상품번호', '종목'
        ]

        def check_has_positions(d: Dict[str, Any]) -> bool:
            if not isinstance(d, dict) or d.get('http_status'):
                return False
            for k in pos_list_keys:
                lst = d.get(k)
                if isinstance(lst, list) and len(lst) > 0:
                    for item in lst:
                        if isinstance(item, dict):
                            for ck in pos_code_keys:
                                if item.get(ck) and str(item[ck]).strip():
                                    return True
            return False

        # 1차 시도: kt00018 (계좌평가잔고개별합산 / OPW00018 - qry_tp="1" 합산 전체 보유종목 최우선)
        # 키움 실전 REST 표준: qry_tp="1"(합산)이 전일 포함 모든 보유종목 output2를 반환
        for q_tp in ["1", "2"]:
            payload_kt00018 = {
                "dmst_stex_tp": "KRX",
                "accNo": self.account,
                "accPwd": self.password,
                "qry_tp": q_tp
            }
            data18, _ = await self.request("kt00018", url, payload_kt00018, priority=priority)
            if data18 and isinstance(data18, dict) and not data18.get('http_status'):
                for k, v in data18.items():
                    if k not in merged_data or (isinstance(v, list) and v):
                        merged_data[k] = v
                if check_has_positions(data18):
                    has_pos = True
                    break

        # 2차 시도: kt00018 순수 페이로드 (qry_tp 없는 기본 요청)
        if not has_pos:
            payload_kt00018_pure = {
                "dmst_stex_tp": "KRX",
                "accNo": self.account,
                "accPwd": self.password
            }
            data18_pure, _ = await self.request("kt00018", url, payload_kt00018_pure, priority=priority)
            if data18_pure and isinstance(data18_pure, dict) and not data18_pure.get('http_status'):
                for k, v in data18_pure.items():
                    if k not in merged_data or (isinstance(v, list) and v):
                        merged_data[k] = v
                if check_has_positions(data18_pure):
                    has_pos = True

        # 3차 시도: kt00004 (계좌평가잔고내역 - qry_tp="1" 당일매매, "2" 당일체결)
        if not has_pos:
            for q_tp in ["1", "2"]:
                payload_kt00004 = {
                    "dmst_stex_tp": "KRX",
                    "accNo": self.account,
                    "accPwd": self.password,
                    "qry_tp": q_tp
                }
                data4, _ = await self.request("kt00004", url, payload_kt00004, priority=priority)
                if data4 and isinstance(data4, dict) and not data4.get('http_status'):
                    for k, v in data4.items():
                        if k not in merged_data or (isinstance(v, list) and v):
                            merged_data[k] = v
                    if check_has_positions(data4):
                        has_pos = True
                        break

        # 4차 시도: kt00005 (체결잔고 - 순수 페이로드)
        if not has_pos:
            payload_kt00005 = {
                "dmst_stex_tp": "KRX",
                "accNo": self.account,
                "accPwd": self.password
            }
            data5, _ = await self.request("kt00005", url, payload_kt00005, priority=priority)
            if data5 and isinstance(data5, dict) and not data5.get('http_status'):
                for k, v in data5.items():
                    if k not in merged_data or (isinstance(v, list) and v):
                        merged_data[k] = v
                if check_has_positions(data5):
                    has_pos = True

        return merged_data if merged_data else None

    async def get_unexecuted_orders(self, priority: RequestPriority = RequestPriority.LOW) -> Optional[Dict[str, Any]]:
        """미체결 주문 내역 조회 (ka10075 / kt00001 호환)"""
        url = f"{self.base_url}/api/dostk/acnt"
        payload = {
            "dmst_stex_tp": "KRX",
            "accNo": self.account,
            "accPwd": self.password,
            "qry_tp": "1"  # 1: 미체결
        }
        data, _ = await self.request("ka10075", url, payload, priority=priority)
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

    async def SetRealReg(self, screen_no: str, code_list: List[str], fid_list: List[str], opt_type: str = "0") -> bool:
        """
        키움증권 실시간 시세/체결 감시 종목 등록 (OpenAPI SetRealReg 호환)
        - screen_no: 화면번호 (예: "1000")
        - code_list: 종목코드 리스트 (예: ["005930", "000660", ...])
        - fid_list: 실시간 수신 FID 리스트 (예: ["10", "13", "20", "41"])
        - opt_type: "0"(신규 등록/기존 교체), "1"(기존 등록 유지 추가)
        """
        clean_codes = [c.replace('A', '').split('_')[0].strip() for c in code_list if c]
        if opt_type == "0":
            self.realtime_registered_codes = clean_codes
        else:
            for c in clean_codes:
                if c not in self.realtime_registered_codes:
                    self.realtime_registered_codes.append(c)

        print(f"📡 [SetRealReg] 실시간 감시 종목 등록 완료 (화면: {screen_no}, 등록 종목수: {len(self.realtime_registered_codes)}개, 신규/추가: {opt_type})")
        return True

    def get_realtime_registered_codes(self) -> List[str]:
        """현재 실시간 감시 등록된 종목 코드 목록 반환"""
        return list(self.realtime_registered_codes)
