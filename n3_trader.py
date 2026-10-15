"""
N3 애프터마켓 반등 매매 (research/afterhours_minute_plan.md 2차 재검증 합격 규칙, 2026-10-15 실거래 투입)

- 19:50 판단: 그날 KRX 거래대금 상위 300 보통주 중 NXT 종목, A' = NXT 현재가 / KRX 종가 − 1 ≤ −3% (15:40 이후 NXT 체결 있음)
- 19:51 매수: 거래대금 순으로 빈 슬롯만큼 NXT 지정가(매도1호가), 종목당 예산 = 주문가능금액 / 최대 종목 수
- 19:58 미체결 취소, 다음 거래일 08:55 KRX 장전 시장가 매도(09:00 시가 체결, 놓치면 09:30까지 재시도)
- 주문 기록 side: N3_BUY / N3_CANCEL(19:58 취소 수량) / N3_SELL — 일중 전략 채점과 분리, 재시작해도 DB로 상태 복원
"""
import asyncio
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List

import limits
from async_kiwoom_client import RequestPriority, is_order_ok, orderbook_price, parse_unexecuted_orders
from database import get_kst_now

LIVE_FROM = "20261016"     # 이 날짜 전에는 판단·로그만 (10/15 밤 ka10095 `_NX` 응답 확인용)
SIGNAL_DROP = -0.03        # A' ≤ −3%
MAX_DROP = -0.30           # ±30% 초과 제외 (설계 §정의)
AFTER_START = "154000"     # 15:40 이후 NXT 체결이 있어야 신호
MIN_UNIVERSE = 100         # 대상이 이보다 적으면 그날 일봉 수집이 덜 된 것으로 보고 진입 안 함 (10/14 186종목)
QUOTE_CHUNK = 50
# 분봉·일봉 수정주가 불일치로 검증에서 제외한 종목 (afterhours_minute_plan.md §2차 보완)
EXCLUDED = {"000500", "000880", "036800", "047920", "068270", "183300", "196170", "199800", "220100",
            "247540", "290650", "328130", "340570", "356860", "466100", "476830", "484870", "494120"}
T_JUDGE, T_BUY, T_CANCEL, T_NIGHT_END = dtime(19, 50), dtime(19, 51), dtime(19, 58), dtime(20, 0)
T_EXIT, T_EXIT_END = dtime(8, 55), dtime(9, 30)


def signal_drop(u: dict, q: dict):
    """신호면 A'(음수)를, 아니면 None (정합성 제외·당일 ±30% 급등락·15:40 이후 체결 없음·A' 범위 밖)"""
    close, prev = float(u.get("close") or 0), float(u.get("prev_close") or 0)
    if u["code"] in EXCLUDED or not q or close <= 0 or q["tm"] < AFTER_START:
        return None
    if prev > 0 and abs(close / prev - 1) > 0.30:
        return None
    a = q["cur"] / close - 1
    return a if MAX_DROP <= a <= SIGNAL_DROP else None


def parse_watch_quotes(data: Any) -> Dict[str, Dict[str, Any]]:
    """ka10095 응답 → {6자리 코드: {cur, ask, tm}} (부호 제거, 접미사 '_NX' 제거)"""
    rows = (data or {}).get("atn_stk_infr") or []
    out = {}
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get("stk_cd") or "").replace("A", "").split("_")[0].strip()
        cur = orderbook_price(r, "cur_prc")
        if code and cur > 0:
            out[code] = {"cur": cur, "ask": orderbook_price(r, "sel_bid", "sel_1th_bid"), "tm": str(r.get("cntr_tm") or "")}
    return out


def pick_n3_orders(universe: List[dict], quotes: Dict[str, dict], slots: int, budget: float) -> List[dict]:
    """신호 종목을 거래대금 순으로 골라 주문 목록 생성. 예산으로 1주도 못 사는 종목은 건너뛴다"""
    picks = []
    if slots <= 0:
        return picks
    for u in universe:  # get_n3_universe가 거래대금 내림차순
        q = quotes.get(u["code"])
        a = signal_drop(u, q)
        if a is None:
            continue
        price = q["ask"] or q["cur"]
        qty = int(budget // price)
        if qty < 1:
            continue
        picks.append({"code": u["code"], "name": u.get("name") or u["code"], "qty": qty, "price": int(price), "a": a})
        if len(picks) >= slots:
            break
    return picks


def exit_qty(held_qty: int, n3_qty: int) -> int:
    """장전 매도 수량: 잔고가 보이면 min(잔고, N3 순매수), 안 보이면(NXT 잔고 미반영 대비) N3 순매수"""
    return min(held_qty, n3_qty) if held_qty > 0 else n3_qty


class N3Trader:
    def __init__(self, bot):
        self.bot, self.db, self.client = bot, bot.db, bot.client
        self.entered_on = self.cancelled_on = self.exited_on = None
        self.exit_codes: Dict[str, datetime] = {}  # 장전 매도 중인 종목 → 일중 엔진이 건드리지 않을 시각

    def protects(self, code: str) -> bool:
        """일중 매매 루프가 이 종목 청산·미체결 정리를 건너뛰어야 하는지 (장전 매도 체결 대기)"""
        until = self.exit_codes.get(code)
        return bool(until and get_kst_now() < until)

    async def log(self, level: str, msg: str, notify: bool = False):
        print(msg)
        await self.db.log_message(level, msg)
        if notify:
            self.bot.notifier.send_message(msg)

    async def run(self):
        while not self.bot.is_shutdown:
            try:
                now = get_kst_now()
                today, t = now.strftime("%Y%m%d"), now.time()
                if not self.bot.is_korean_market_holiday(now):
                    if T_EXIT <= t < T_EXIT_END and self.exited_on != today:
                        if await self.exit_positions():
                            self.exited_on = today
                    if T_JUDGE <= t < T_CANCEL and self.entered_on != today:
                        self.entered_on = today
                        await self.enter(today)
                    if T_CANCEL <= t < T_NIGHT_END and self.cancelled_on != today:
                        self.cancelled_on = today
                        await self.cancel_unfilled()
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.log("ERROR", f"[N3] 작업 예외: {e}", notify=True)
                await asyncio.sleep(30.0)

    async def _paused(self) -> bool:
        return self.bot.is_paused or (await self.bot._remote_paused()) is True

    async def enter(self, today: str):
        live = today >= LIVE_FROM
        tag = "실거래" if live else "판단만"
        if live and await self._paused():
            await self.log("SYSTEM", "[N3] 봇 일시정지(대시보드 STOP·킬스위치) 상태 → 오늘 진입 안 함")
            return
        # 재시작 뒤 같은 날 두 번 사지 않도록 DB로 확인 (order_history.timestamp는 UTC)
        kst0 = get_kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
        bought = await self.db.count_n3_buys_since((kst0 - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"))
        if live and bought != 0:
            await self.log("WARNING", f"[N3] 오늘 N3 매수 기록 {bought}건(-1=DB 오류) → 다시 진입하지 않음")
            return
        universe = await self.db.get_n3_universe(today)
        if len(universe) < MIN_UNIVERSE:
            await self.log("WARNING", f"[N3] {today} 대상 {len(universe)}종목(< {MIN_UNIVERSE}, 일봉 수집 전?) → 진입 안 함", notify=live)
            return
        quotes: Dict[str, dict] = {}
        codes = [u["code"] for u in universe]
        for i in range(0, len(codes), QUOTE_CHUNK):
            data = await self.client.get_watch_quotes([f"{c}_NX" for c in codes[i:i + QUOTE_CHUNK]], priority=RequestPriority.HIGH)
            if data is None:
                await self.log("WARNING", f"[N3] ka10095 `_NX` 시세 조회 실패 ({i}~{i + QUOTE_CHUNK}번째)")
            quotes.update(parse_watch_quotes(data))
        await self._sync()
        snap = await self.bot.portfolio.get_snapshot()
        slots = max(0, limits.MAX_STOCKS - len(snap["positions"]))
        cash = float(self.bot.portfolio.current_capital or 0)
        budget = min(cash, float(self.bot.portfolio.total_asset or cash)) / limits.MAX_STOCKS
        picks = pick_n3_orders(universe, quotes, slots, budget)
        n_sig = sum(1 for u in universe if signal_drop(u, quotes.get(u["code"])) is not None)
        await self.log("SYSTEM", f"[N3/{tag}] 19:50 판단: 대상 {len(universe)}종목, 시세 {len(quotes)}종목, 신호 {n_sig}건, "
                                 f"빈 슬롯 {slots}, 종목당 예산 {int(budget):,}원 → 주문 {len(picks)}건: "
                                 + ", ".join(f"{p['name']}({p['code']}) {p['a'] * 100:+.2f}% {p['qty']}주@{p['price']:,}" for p in picks),
                       notify=live and bool(picks))
        if not live:
            return
        while get_kst_now().time() < T_BUY:  # 판단(19:50) 뒤 19:51부터 체결 (설계 M1)
            await asyncio.sleep(1.0)
        for p in picks:
            res = await self.client.send_order(p["code"], p["qty"], p["price"], order_type="00", side="BUY",
                                               priority=RequestPriority.HIGH, exchange="NXT")
            if is_order_ok(res) or res is None:
                # 응답 없음은 접수됐을 수도 있어 기록해 둔다 (미체결이면 19:58 취소, 다음 날 매도가능 0주로 닫힘)
                await self.db.log_order(p["code"], p["name"], "N3_BUY", p["qty"], p["price"])
                await self.log("TRADE" if res else "WARNING",
                               f"🌙 [N3 매수 {'접수' if res else '응답 없음(접수 여부 미상)'}] {p['name']}({p['code']}) "
                               f"{p['qty']}주 @ {p['price']:,}원 (NXT, A' {p['a'] * 100:+.2f}%)", notify=True)
            else:
                msg = (res or {}).get("return_msg") or (res or {}).get("msg1") or ""
                await self.log("ERROR", f"[N3] NXT 매수 거절: {p['name']}({p['code']}) {p['qty']}주@{p['price']:,} - {msg}", notify=True)

    async def cancel_unfilled(self):
        """19:58 N3 미체결 매수 취소 → 취소 수량을 N3_CANCEL로 기록 (다음 날 매도 수량 = 매수 − 취소)"""
        opened = await self.db.get_n3_open()
        if not opened:
            return
        names = {o["code"]: o["name"] for o in opened}
        for o in parse_unexecuted_orders(await self.client.get_unexecuted_orders(priority=RequestPriority.HIGH)):
            if o["code"] in names and o["side"] in ("BUY", None) and o["qty"] > 0:
                res = await self.client.cancel_order(o["ord_no"], o["code"], o["qty"], priority=RequestPriority.HIGH, exchange="NXT")
                if is_order_ok(res):
                    await self.db.log_order(o["code"], names[o["code"]], "N3_CANCEL", o["qty"], 0)
                await self.log("SYSTEM", f"[N3] 19:58 미체결 매수 취소: {names[o['code']]}({o['code']}) {o['qty']}주 "
                                         f"(주문번호 {o['ord_no']}) {'성공' if is_order_ok(res) else '실패: ' + str((res or {}).get('return_msg'))}",
                               notify=not is_order_ok(res))

    async def _sync(self):
        if hasattr(self.bot, "_sync_account_balance"):
            await self.bot._sync_account_balance()

    async def exit_positions(self) -> bool:
        """장전(08:55) KRX 시장가 매도 → 09:00 시가 체결. 처리를 끝냈으면 True (DB 오류면 False → 09:30까지 재시도)"""
        opened = await self.db.get_n3_open()
        if opened is None:
            return False
        if not opened:
            return True
        await self._sync()
        held = {p["code"]: p for p in (await self.bot.portfolio.get_snapshot())["positions"]}
        now = get_kst_now()
        until = max(now + timedelta(minutes=5), now.replace(hour=9, minute=5, second=0, microsecond=0))
        late = now.time() >= dtime(9, 0)
        for o in opened:
            code, name = o["code"], o["name"]
            pos = held.get(code) or {}
            qty = exit_qty(int(pos.get("qty") or 0), int(o["qty"] or 0))
            self.exit_codes[code] = until
            res = await self.client.send_order(code, qty, 0, order_type="03", side="SELL",
                                               priority=RequestPriority.CRITICAL, exchange="KRX")
            msg = str((res or {}).get("return_msg") or (res or {}).get("msg1") or "응답 없음")
            if is_order_ok(res):
                await self.db.log_order(code, name, "N3_SELL", qty, int(pos.get("current_price") or pos.get("buy_price") or 0))
                await self.log("TRADE", f"🌅 [N3 매도 접수] {name}({code}) {qty}주 KRX 시장가 "
                                        f"({'장중 지연 매도' if late else '09:00 시가 체결'})", notify=True)
            elif "800033" in msg and not pos:
                self.exit_codes.pop(code, None)
                await self.db.log_order(code, name, "N3_SELL", 0, 0)  # 잔고도 없고 매도가능 0주 = 어제 미체결 → 기록만 닫음
                await self.log("SYSTEM", f"[N3] {name}({code}) 매도가능 0주·잔고 없음(어제 미체결) → 매도 없이 종료")
            else:
                self.exit_codes.pop(code, None)  # 실패하면 일중 엔진 청산 규칙(손절·15:15)에 맡기고 내일 다시 시도
                await self.log("ERROR", f"[N3] 장전 매도 거절: {name}({code}) {qty}주 - {msg} → 일중 엔진 청산 규칙에 맡김", notify=True)
        return True
