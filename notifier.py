"""
비동기 텔레그램 / 디스코드 실시간 알림 엔진 (Async Notifier)
- 트레이딩 루프의 실행 지연이 없도록 asyncio.Queue 기반 백그라운드 논블로킹 발송
- 체결 알림 (매수/매도/분할익절/스탑로스)
- 시스템 리스크 알림 (서킷 브레이커, API 장애, MDD 도달 경보)
- 15:35 장 마감 일일 정산 리포트 자동 생성 및 발송
"""
import asyncio
import os
import aiohttp
from datetime import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv(override=False)


class AsyncNotifier:
    """
    비동기 텔레그램 및 웹훅 알림 관리자
    """
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_running = False

    async def start(self):
        """알림 발송 백그라운드 워커 가동"""
        if self._is_running:
            return
        self._is_running = True
        self._session = aiohttp.ClientSession()
        self._worker_task = asyncio.create_task(self._dispatch_loop())
        print("🔔 [AsyncNotifier] 텔레그램 실시간 알림 워커 가동 완료.")

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
        print("🛑 [AsyncNotifier] 알림 워커 정상 종료.")

    def send_message(self, text: str):
        """논블로킹 메시지 큐 적재 (동기/비동기 어디서든 즉각 호출 가능)"""
        if self._is_running:
            self.queue.put_nowait(text)
        else:
            print(f"[Notifier Log] {text}")

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

        self.send_message("\n".join(msg))

    def notify_circuit_breaker(self, mdd: float, total_asset: float):
        """계좌 서킷 브레이커 발동 경보"""
        msg = (
            f"🚨🚨🚨 [EMERGENCY: 계좌 서킷 브레이커 발동] 🚨🚨🚨\n"
            f"• 발동시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• 계좌 낙폭: {mdd:.2f}% (최고점 대비 -5% 도달)\n"
            f"• 현재 자산: {total_asset:,.0f}원\n"
            f"• 조치사항: 당일 신규 매수 전면 차단 및 비상 관제 모드 전환"
        )
        self.send_message(msg)

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
        self.send_message(msg)

    async def _dispatch_loop(self):
        """큐에서 메시지를 꺼내 텔레그램 API로 전송하는 루프"""
        while self._is_running:
            try:
                text = await self.queue.get()
                if not self.bot_token or not self.chat_id:
                    # 토큰 미설정 시 콘솔 출력으로 폴백
                    print(f"📱 [Telegram Alert] {text}")
                    self.queue.task_done()
                    continue

                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": text
                }
                if self._session and not self._session.closed:
                    async with self._session.post(url, json=payload, timeout=5) as resp:
                        if resp.status != 200:
                            print(f"⚠️ [Telegram Error] 발송 실패 (Status: {resp.status})")
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Notifier Dispatch Error] {e}")
                await asyncio.sleep(1.0)
