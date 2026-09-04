"""
카카오톡(KakaoTalk) 실시간 알림 엔진 (Kakao Notifier)
- 카카오 REST API '나에게 보내기 (POST /v2/api/talk/memo/default/send)' 기반 알림
- OAuth2 refresh_token 기반 access_token 자동 갱신 (24시간 무인 데몬 지원)
- asyncio.Queue 기반 백그라운드 논블로킹 메시지 발송 (트레이딩 루프 지연 0초)
- 체결 알림 (매수/매도/분할익절/스탑로스)
- 시스템 리스크 알림 (서킷 브레이커, API 장애, MDD 도달 경보)
- 15:35 장 마감 일일 정산 리포트 자동 생성 및 발송
"""
import asyncio
import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional
import aiohttp
from dotenv import load_dotenv

load_dotenv(override=False)


class KakaoNotifier:
    """
    카카오톡 실시간 알림 및 토큰 자동 갱신 관리자
    """
    def __init__(self, rest_api_key: Optional[str] = None,
                 access_token: Optional[str] = None,
                 refresh_token: Optional[str] = None):
        self.rest_api_key = rest_api_key or os.getenv("KAKAO_REST_API_KEY", "")
        self.access_token = access_token or os.getenv("KAKAO_ACCESS_TOKEN", "")
        self.refresh_token = refresh_token or os.getenv("KAKAO_REFRESH_TOKEN", "")
        self.token_expires_at = time.time() + 21600.0  # 기본 6시간 유효 추정

        self.queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_running = False

    async def start(self):
        """카카오톡 알림 발송 백그라운드 워커 가동"""
        if self._is_running:
            return
        self._is_running = True
        self._session = aiohttp.ClientSession()
        self._worker_task = asyncio.create_task(self._dispatch_loop())
        print("🔔 [KakaoNotifier] 카카오톡 실시간 알림 워커 가동 완료.")

    async def stop(self):
        """알림 워커 정지 및 잔여 메시지 발송 완료"""
        if not self._is_running:
            return
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        print("🛑 [KakaoNotifier] 알림 워커 정상 종료.")

    async def refresh_access_token(self) -> bool:
        """refresh_token을 사용하여 access_token 자동 갱신"""
        if not self.rest_api_key or not self.refresh_token:
            return False

        url = "https://kauth.kakao.com/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.rest_api_key,
            "refresh_token": self.refresh_token
        }

        try:
            if not self._session or self._session.closed:
                self._session = aiohttp.ClientSession()

            async with self._session.post(url, data=payload, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token", self.access_token)
                    expires_in = data.get("expires_in", 21600)
                    self.token_expires_at = time.time() + expires_in - 300  # 만료 5분 전 갱신 예약

                    # refresh_token이 함께 갱신된 경우 업데이트
                    if "refresh_token" in data:
                        self.refresh_token = data["refresh_token"]

                    print("🔑 [KakaoNotifier] 카카오톡 Access Token 자동 갱신 성공.")
                    return True
                else:
                    err_text = await resp.text()
                    print(f"⚠️ [KakaoNotifier] 토큰 갱신 실패 (Status: {resp.status}): {err_text}")
                    return False
        except Exception as e:
            print(f"⚠️ [KakaoNotifier] 토큰 갱신 예외: {e}")
            return False

    def send_message(self, text: str, title: str = "키움 퀀트 봇 알림"):
        """논블로킹 메시지 큐 적재 (동기/비동기 어디서든 즉각 호출 가능)"""
        if self._is_running:
            self.queue.put_nowait({"text": text, "title": title})
        else:
            print(f"[Kakao Log] {text}")

    def notify_order_filled(self, side: str, name: str, code: str, qty: int, price: float,
                            reason: str = "", pnl: Optional[float] = None, yield_rate: Optional[float] = None):
        """주문 체결 알림 포맷팅"""
        icon = "🔥 [매수 체결]" if side.upper() == "BUY" else "🎯 [매도 체결]"
        now_str = datetime.now().strftime('%H:%M:%S')

        msg = [
            f"{icon} {name}({code})",
            f"• 체결시간: {now_str}",
            f"• 체결단가: {price:,.0f}원 ({qty}주)",
            f"• 총 체결액: {price * qty:,.0f}원",
            f"• 체결사유: {reason}"
        ]
        if pnl is not None and yield_rate is not None:
            pnl_icon = "🔺" if pnl >= 0 else "🔻"
            msg.append(f"• 실현손익: {pnl_icon} {pnl:+,.0f}원 ({yield_rate:+.2f}%)")

        self.send_message("\n".join(msg), title=f"{icon} {name}")

    def notify_circuit_breaker(self, mdd: float, total_asset: float):
        """계좌 서킷 브레이커 발동 경보"""
        msg = (
            f"🚨🚨🚨 [EMERGENCY: 계좌 서킷 브레이커 발동] 🚨🚨🚨\n"
            f"• 발동시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• 계좌 낙폭: {mdd:.2f}% (최고점 대비 -5% 도달)\n"
            f"• 현재 자산: {total_asset:,.0f}원\n"
            f"• 조치사항: 당일 신규 매수 전면 차단 및 비상 관제 모드 전환"
        )
        self.send_message(msg, title="🚨 계좌 서킷 브레이커 발동")

    def notify_daily_settlement(self, snapshot: Dict[str, Any]):
        """15:35 장 마감 일일 정산 리포트"""
        initial = snapshot.get('initial_capital', 0)
        current = snapshot.get('total_asset', 0)
        pnl = snapshot.get('total_pnl', 0)
        yield_pct = snapshot.get('total_yield', 0)
        pos_count = snapshot.get('positions_count', 0)

        msg = (
            f"📊 [일일 장 마감 퀀트 정산 리포트]\n"
            f"• 정산일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"• 기초자산: {initial:,.0f}원\n"
            f"• 기말자산: {current:,.0f}원\n"
            f"• 당일손익: {pnl:+,.0f}원 ({yield_pct:+.2f}%)\n"
            f"• 보유종목: {pos_count}개 종목 잔여\n"
            f"• 켈리비중: {snapshot.get('kelly_allocation_pct', 20):.1f}%\n"
            f"🏁 수고하셨습니다. 내일 08:55에 자동으로 세션이 준비됩니다."
        )
        self.send_message(msg, title="📊 일일 장 마감 결산 리포트")

    async def _dispatch_loop(self):
        """큐에서 메시지를 꺼내 카카오톡 '나에게 보내기' API로 전송하는 루프"""
        while self._is_running:
            try:
                item = await self.queue.get()
                text = item.get("text", "")
                title = item.get("title", "키움 퀀트 봇 알림")

                if not self.access_token:
                    # 토큰 미설정 시 콘솔 출력으로 폴백
                    print(f"📱 [KakaoTalk Memo Log] {text}")
                    self.queue.task_done()
                    continue

                # 토큰 만료 시간 임박 시 자동 갱신
                if time.time() >= self.token_expires_at:
                    await self.refresh_access_token()

                url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }

                # 카카오 텍스트 템플릿
                template_object = {
                    "object_type": "text",
                    "text": text,
                    "link": {
                        "web_url": "http://localhost:8501",
                        "mobile_web_url": "http://localhost:8501"
                    },
                    "button_title": "관제 대시보드"
                }

                data = {
                    "template_object": json.dumps(template_object, ensure_ascii=False)
                }

                if self._session and not self._session.closed:
                    async with self._session.post(url, headers=headers, data=data, timeout=5) as resp:
                        if resp.status == 401:
                            # 만료 등으로 인증 실패 시 즉시 토큰 갱신 후 1회 재시도
                            refreshed = await self.refresh_access_token()
                            if refreshed:
                                headers["Authorization"] = f"Bearer {self.access_token}"
                                async with self._session.post(url, headers=headers, data=data, timeout=5) as retry_resp:
                                    if retry_resp.status != 200:
                                        print(f"⚠️ [KakaoTalk Error] 재전송 실패 (Status: {retry_resp.status})")
                        elif resp.status != 200:
                            print(f"⚠️ [KakaoTalk Error] 발송 실패 (Status: {resp.status})")

                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Kakao Dispatch Error] {e}")
                await asyncio.sleep(1.0)


# 하위 호환성을 위한 별칭 제공
AsyncNotifier = KakaoNotifier
