import aiohttp
import asyncio
import os
import time
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

class KiwoomRest:
    def __init__(self, is_demo=True):
        if is_demo:
            self.base_url = "https://mockapi.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_MOCK_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_MOCK_APP_SECRET")
            self.account = os.getenv("KIWOOM_MOCK_ACCOUNT")
            self.password = os.getenv("KIWOOM_MOCK_PASSWORD")
            self.mode = "MOCK"
        else:
            self.base_url = "https://api.kiwoom.com"
            self.app_key = os.getenv("KIWOOM_REAL_APP_KEY")
            self.app_secret = os.getenv("KIWOOM_REAL_APP_SECRET")
            self.account = os.getenv("KIWOOM_REAL_ACCOUNT")
            self.password = os.getenv("KIWOOM_REAL_PASSWORD")
            self.mode = "REAL"
            
        self.access_token = None
        self.session = None
        self._request_lock = None
        self._last_request_time = 0.0

    async def init_session(self):
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(timeout=timeout)
            self._request_lock = asyncio.Lock()

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_access_token(self):
        await self.init_session()
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
                    print(f"✅ [{self.mode}] 토큰 발급 성공")
                    return self.access_token
                else:
                    print(f"⚠️ [{self.mode}] 토큰 파싱 에러: {data}")
                    return None
        except Exception as e:
            print(f"❌ [{self.mode}] 토큰 발급 에러: {e}")
            return None

    def _get_headers(self, api_id):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "api-id": api_id
        }

    async def _safe_request(self, api_id, url, payload, headers_override=None, retries=5):
        await self.init_session()
        if not self.access_token:
            await self.get_access_token()
            
        headers = self._get_headers(api_id)
        if headers_override:
            headers.update(headers_override)
            
        if self._request_lock is None:
            self._request_lock = asyncio.Lock()
            
        for attempt in range(retries):
            # 글로벌 Lock: 모든 API 호출을 직렬화하여 Rate Limit 방어
            async with self._request_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < 0.25:  # 초당 4회(4 TPS)로 상향하여 병목 해소
                    await asyncio.sleep(0.25 - elapsed)
                self._last_request_time = time.time()
                
                try:
                    async with self.session.post(url, headers=headers, json=payload) as response:
                        if response.status == 429:
                            # 429를 받으면 _last_request_time을 미래로 밀어서 다른 태스크도 대기하게 함
                            backoff = max(2, 2 ** attempt)
                            self._last_request_time = time.time() + backoff
                            print(f"⚠️ Rate Limit 초과. {backoff}초 대기 후 재시도... ({attempt+1}/{retries})")
                            await asyncio.sleep(backoff)
                            continue
                        
                        if response.status >= 500:
                            print(f"⚠️ 서버 에러({response.status}). 재시도 대기 중... ({attempt+1}/{retries})")
                            await asyncio.sleep(2 ** attempt)
                            continue
                            
                        response.raise_for_status()
                        data = await response.json()
                        return data, response.headers
                except aiohttp.ClientError as e:
                    print(f"⚠️ 네트워크 에러 ({api_id}): {e} - 재시도 ({attempt+1}/{retries})")
                    await asyncio.sleep(2 ** attempt)
                except Exception as e:
                    print(f"❌ 알 수 없는 에러 ({api_id}): {e}")
                    return None, None
                
        print(f"❌ API 요청 최종 실패 ({api_id})")
        return None, None

    async def get_price(self, code):
        url = f"{self.base_url}/api/dostk/stkinfo"
        payload = {"stk_cd": code}
        data, _ = await self._safe_request("ka10001", url, payload)
        return data

    async def get_orderbook(self, code):
        """ka10004 API를 이용하여 매수/매도 1호가(최우선호가)를 조회합니다."""
        url = f"{self.base_url}/api/dostk/mrkcond"
        payload = {"stk_cd": code}
        data, _ = await self._safe_request("ka10004", url, payload)
        return data

    async def send_order(self, code, qty, price, order_type="00", side="BUY"):
        """실제 운영 환경 대응 주문 발송 메서드 (재시도 로직 포함)"""
        url = f"{self.base_url}/api/dostk/ordr"
        api_id = "kt10000" if side == "BUY" else "kt10001"

        # [안전 가드] BUY 주문은 항상 지정가(00)로 강제 (시장가 매수 시 ETF 증거금 부족(855056) 방지)
        if side.upper() == "BUY" and str(order_type) == "03":
            print(f"🛡️ [send_order 가드] BUY 시장가→지정가 자동 전환: {code} @ {price}원 (증거금 부족 방지)")
            order_type = "00"

        # 실전 API 규격: trde_tp(0:보통, 3:시장가), 계좌번호는 토큰에 포함
        payload = {
            "dmst_stex_tp": "KRX",
            "stk_cd": code,
            "ord_qty": str(int(qty)),
            "ord_uv": str(int(price)),
            "trde_tp": order_type,   # "0": 보통, "3": 시장가
            "cond_uv": "0"
        }
        
        # 실제 환경에서는 주문 중요도가 높으므로 재시도 횟수를 늘림 (최대 5회)
        data, _ = await self._safe_request(api_id, url, payload, retries=5)
        
        if not data:
            print(f"❌ [ORDER_FAIL] 주문 발송 완전 실패 ({side} {code} {qty}주)")
            return None
            
        # 키움증권 응답 코드 확인
        rt_cd = data.get('rt_cd') if data.get('rt_cd') is not None else data.get('return_code')
        if str(rt_cd) == '0':
            print(f"✅ [ORDER_SUCCESS] 주문 발송 완료 ({side} {code} {qty}주)")
            return data
        else:
            msg = data.get('msg1') or data.get('return_msg') or '알 수 없는 오류'
            print(f"❌ [ORDER_REJECTED] 주문 거절: {msg}")
            return data

    async def get_account_balance(self):
        """예수금(잔고) 상세 조회"""
        url = f"{self.base_url}/api/dostk/acnt" 
        payload = {
            "dmst_stex_tp": "KRX",
            "accNo": self.account,
            "accPwd": self.password
        }
        # kt00005 (체결잔고요청) - 예수금 및 보유종목 동시 반환
        data, _ = await self._safe_request("kt00005", url, payload)
        return data

    async def get_account_positions(self):
        """계좌 잔고(보유 종목) 내역 상세 조회 (포트폴리오 동기화용)"""
        url = f"{self.base_url}/api/dostk/acnt" 
        payload = {
            "dmst_stex_tp": "KRX",
            "accNo": self.account,
            "accPwd": self.password
        }
        # kt00005 (체결잔고요청)
        data, _ = await self._safe_request("kt00005", url, payload)
        return data

    async def get_top_trading_value(self):
        url = f"{self.base_url}/api/dostk/rkinfo"
        payload = {
            "mrkt_tp": "000",
            "mang_stk_incls": "0",
            "stex_tp": "1" if self.mode == "MOCK" else "3"
        }
        data, _ = await self._safe_request("ka10032", url, payload)
        return data

    async def get_daily_chart(self, code, base_dt):
        url = f"{self.base_url}/api/dostk/chart"
        payload = {
            "stk_cd": code,
            "base_dt": base_dt,
            "upd_stkpc_tp": "1"
        }
        data, _ = await self._safe_request("ka10081", url, payload)
        return data

    async def get_minute_chart(self, code, base_dt, next_key=None):
        url = f"{self.base_url}/api/dostk/chart"
        payload = {
            "stk_cd": code,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": base_dt
        }
        headers_override = {"next-key": next_key} if next_key else None
        data, headers = await self._safe_request("ka10080", url, payload, headers_override)
        return data, headers
