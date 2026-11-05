"""
N3 애프터마켓 반등 매매 (research/afterhours_minute_plan.md 2차 재검증 합격 규칙, 2026-10-15 실거래 투입)

- 19:50 판단: 그날 KRX 거래대금 상위 300 보통주 중 NXT 종목, A' = NXT 현재가 / KRX 종가(15:30 분봉) − 1 ≤ −3% (15:40 이후 NXT 체결 있음)
- 19:51 매수: 거래대금 순으로 빈 슬롯만큼 NXT 지정가(매도1호가), 종목당 예산 = 주문가능금액 / 최대 종목 수
- 19:58 미체결 취소, 다음 거래일 08:55 KRX 장전 시장가 매도(09:00 시가 체결, 놓치면 09:30까지 재시도)
- 주문 기록 side: N3_BUY / N3_CANCEL(19:58 취소 수량) / N3_SELL — 일중 전략 채점과 분리, 재시작해도 DB로 상태 복원
- N3_SELL 가격: 접수 때는 잔고 현재가(전날 종가)로 넣고, 09:01~09:40 ka10076으로 실제 체결가를 받아 고친다
"""
import asyncio
import time
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List

import limits
from async_kiwoom_client import RequestPriority, is_order_ok, orderbook_price, parse_executions, parse_unexecuted_orders
from collect_history import _num
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
T_FILL, T_FILL_END = dtime(9, 1), dtime(9, 40)  # 장전 매도 체결가 확인 구간 (09:30 지연 매도까지 포함, 끝 시각이 마지막 확인)
FILL_CHECK_SECONDS = 60.0  # 체결가 확인 간격
# 1회성(2026-11-05): 일봉 종가 원인 확인용 (research/daily_close_after_market_cause.md) — 조회·로그만, 확인이 끝나면 다음 배포에서 삭제
SOURCE_CHECK_CODES = ("005930", "009830", "028050", "069500")  # 대형주, NXT 일봉 없는 주식 2개, ETF
SOURCE_CHECK_DAYS = ("20260911", "20261015", "20261104")       # 애프터마켓 시작 전, ETF 불일치일, 최근 불일치일
SOURCE_CHECK_SESSIONS = (("장전", "0800", "0859"), ("정규", "0900", "1530"), ("사이", "1531", "1559"), ("애프터", "1600", "2000"))


def source_check_lines(code: str, day: str, chart: Any, price: Any, minute: Any) -> List[str]:
    """ka10081·ka10086·ka10080 원본에서 그날 값을 뽑아 비교용 로그 문장으로 만든다"""
    tag = f"[일봉 원인 확인] {day} {code}"
    d = next((r for r in (chart or {}).get("stk_dt_pole_chart_qry") or [] if r.get("dt") == day), None)
    p = next((r for r in (price or {}).get("daly_stkpc") or [] if r.get("date") == day), None)
    rows = (minute or {}).get("stk_min_pole_chart_qry") or []
    bars = sorted((r for r in rows if str(r.get("cntr_tm") or "").startswith(day)), key=lambda r: r["cntr_tm"])
    lines = [f"{tag} ka10081 시{d.get('open_pric')} 고{d.get('high_pric')} 저{d.get('low_pric')} 종{d.get('cur_prc')} "
             f"전일비{d.get('pred_pre')} 량{d.get('trde_qty')} 대금{d.get('trde_prica')}" if d
             else f"{tag} ka10081 그날 행 없음 {(chart or {}).get('return_msg') or ''}".strip(),
             f"{tag} ka10086 시{p.get('open_pric')} 고{p.get('high_pric')} 저{p.get('low_pric')} 종{p.get('close_pric')} "
             f"전일비{p.get('pred_rt')} 량{p.get('trde_qty')} 대금{p.get('amt_mn')}" if p
             else f"{tag} ka10086 그날 행 없음 {(price or {}).get('return_msg') or ''}".strip()]
    if not bars:
        return lines + [f"{tag} ka10080 그날 봉 없음 (받은 봉 {len(rows)}개)"]
    parts = []
    for label, start, end in SOURCE_CHECK_SESSIONS:
        s = [r for r in bars if start <= r["cntr_tm"][8:12] <= end]
        if s:
            parts.append(f"{label} {len(s)}봉 고{max(_num(r.get('high_pric')) for r in s)} 저{min(_num(r.get('low_pric')) for r in s)} "
                         f"량{sum(_num(r.get('trde_qty')) for r in s)} 끝{s[-1]['cntr_tm'][8:12]}={_num(s[-1].get('cur_prc'))}")
    first = next((r for r in bars if r["cntr_tm"][8:12] == "0900"), None)
    close = next((r for r in bars if r["cntr_tm"][8:12] == "1530"), None)
    lines.append(f"{tag} ka10080 09:00시가 {_num(first.get('open_pric')) if first else '없음'}, "
                 f"15:30종가 {_num(close.get('cur_prc')) if close else '없음'}, 첫봉 {bars[0]['cntr_tm'][8:12]} | " + " | ".join(parts))
    return lines


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


ONE_SHARE_CAP = 0.7  # 종목당 예산으로 1주도 못 살 때 1주는 허용하되, 한 종목에 주문가능금액의 이 비율까지 (10/15 50% → 10/20 사용자 결정 70%)


def pick_n3_orders(universe: List[dict], quotes: Dict[str, dict], slots: int, budget: float, cash: float = None) -> List[dict]:
    """신호 종목을 거래대금 순으로 골라 주문 목록 생성.
    수량 = 종목당 예산 // 가격. 0주면 가격이 주문가능금액 × ONE_SHARE_CAP 이하일 때만 1주. 합계는 주문가능금액(cash) 안"""
    picks = []
    if slots <= 0:
        return picks
    cash = budget * limits.MAX_STOCKS if cash is None else cash
    left = cash
    for u in universe:  # get_n3_universe가 거래대금 내림차순
        q = quotes.get(u["code"])
        a = signal_drop(u, q)
        if a is None:
            continue
        price = q["ask"] or q["cur"]
        qty = int(min(budget, left) // price)
        if qty < 1 and price <= min(cash * ONE_SHARE_CAP, left):
            qty = 1
        if qty < 1:
            continue
        left -= qty * price
        picks.append({"code": u["code"], "name": u.get("name") or u["code"], "qty": qty, "price": int(price), "a": a})
        if len(picks) >= slots:
            break
    return picks


def exit_qty(held_qty: int, n3_qty: int) -> int:
    """장전 매도 수량: 잔고가 보이면 min(잔고, N3 순매수), 안 보이면(NXT 잔고 미반영 대비) N3 순매수"""
    return min(held_qty, n3_qty) if held_qty > 0 else n3_qty


# 주문 사이 최소 간격(초) — 11/3 TOM 모의 리허설에서 주문 10건을 연달아 보내자 서버가 429로 한 건 걸러 한 건씩 거부
# (주문은 중복 접수 위험 때문에 재시도 1회뿐이라 그대로 '응답 없음'), 3초 쉰 다음 주문은 모두 성공 → 여러 건 주문 루프는 간격을 둔다
ORDER_GAP_SECONDS = 1.0


class OrderPacer:
    """여러 건 주문을 보낼 때 주문 사이 간격을 지켜 client.send_order 호출 (429 호출 제한 방지)"""
    def __init__(self, client, gap: float = ORDER_GAP_SECONDS):
        self.client, self.gap, self.last = client, gap, float("-inf")

    async def send_order(self, *args, **kwargs):
        wait = self.gap - (time.monotonic() - self.last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await self.client.send_order(*args, **kwargs)
        finally:
            self.last = time.monotonic()


class N3Trader:
    def __init__(self, bot):
        self.bot, self.db, self.client = bot, bot.db, bot.client
        self.orders = OrderPacer(bot.client)
        self.entered_on = self.cancelled_on = self.exited_on = None
        self.exit_codes: Dict[str, datetime] = {}  # 장전 매도 중인 종목 → 일중 엔진이 건드리지 않을 시각
        self.pending_fills: Dict[str, dict] = {}   # 주문번호(앞자리 0 제거) → 체결가 확인 대기 중인 매도 {code, name, qty}
        self.next_fill_check = 0.0

    def protects(self, code: str) -> bool:
        """일중 매매 루프가 이 종목 청산·미체결 정리를 건너뛰어야 하는지 (장전 매도 체결 대기)"""
        until = self.exit_codes.get(code)
        return bool(until and get_kst_now() < until)

    async def log(self, level: str, msg: str, notify: bool = False):
        print(msg)
        await self.db.log_message(level, msg)
        if notify:
            self.bot.notifier.send_message(msg)

    async def log_daily_source_check(self):
        """1회성: 일봉 종가 원인 확인용 키움 원본 값을 로그로 남긴다. 주문·DB 시세 변경 없음. 실패해도 N3 매매에 영향 없음"""
        try:
            url = self.client.base_url
            for day in SOURCE_CHECK_DAYS:
                for code in SOURCE_CHECK_CODES:
                    chart, _ = await self.client.request("ka10081", f"{url}/api/dostk/chart", {"stk_cd": code, "base_dt": day, "upd_stkpc_tp": "1"})
                    price, _ = await self.client.request("ka10086", f"{url}/api/dostk/mrkcond", {"stk_cd": code, "qry_dt": day, "indc_tp": "0"})
                    minute, _ = await self.client.request("ka10080", f"{url}/api/dostk/chart",
                                                          {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day})
                    for line in source_check_lines(code, day, chart, price, minute):
                        await self.log("SYSTEM", line)
        except Exception as e:
            await self.log("WARNING", f"[일봉 원인 확인] 예외: {e}")

    async def run(self):
        self.source_check_task = asyncio.create_task(self.log_daily_source_check())  # 조회가 늦어도 N3 판단을 막지 않게 따로 실행
        while not self.bot.is_shutdown:
            try:
                now = get_kst_now()
                today, t = now.strftime("%Y%m%d"), now.time()
                if not self.bot.is_korean_market_holiday(now):
                    if T_EXIT <= t < T_EXIT_END and self.exited_on != today:
                        if await self.exit_positions():
                            self.exited_on = today
                    if self.pending_fills and t >= T_FILL and time.monotonic() >= self.next_fill_check:
                        self.next_fill_check = time.monotonic() + FILL_CHECK_SECONDS
                        await self.record_fills(final=t >= T_FILL_END)
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
            await self.log("WARNING", f"[N3] {today} 대상 {len(universe)}종목(< {MIN_UNIVERSE}, 일봉·15:30 분봉 수집 전?) → 진입 안 함", notify=live)
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
        picks = pick_n3_orders(universe, quotes, slots, budget, min(cash, float(self.bot.portfolio.total_asset or cash)))
        sigs = [(u, quotes[u["code"]], a) for u in universe
                if (a := signal_drop(u, quotes.get(u["code"]))) is not None]
        await self.log("SYSTEM", f"[N3/{tag}] 19:50 판단: 대상 {len(universe)}종목, 시세 {len(quotes)}종목, 신호 {len(sigs)}건"
                                 + (" (" + ", ".join(f"{u['name']}({u['code']}) {a * 100:+.2f}% @{q['ask'] or q['cur']:,}"
                                                     for u, q, a in sigs) + ")" if sigs else "")
                                 + f", 빈 슬롯 {slots}, 종목당 예산 {int(budget):,}원 → 주문 {len(picks)}건: "
                                 + ", ".join(f"{p['name']}({p['code']}) {p['qty']}주@{p['price']:,}" for p in picks),
                       notify=live and bool(sigs))
        if not live:
            return
        while get_kst_now().time() < T_BUY:  # 판단(19:50) 뒤 19:51부터 체결 (설계 M1)
            await asyncio.sleep(1.0)
        for p in picks:
            res = await self.orders.send_order(p["code"], p["qty"], p["price"], order_type="00", side="BUY",
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
            res = await self.orders.send_order(code, qty, 0, order_type="03", side="SELL",
                                               priority=RequestPriority.CRITICAL, exchange="KRX")
            msg = str((res or {}).get("return_msg") or (res or {}).get("msg1") or "응답 없음")
            if is_order_ok(res):
                # 접수 시점 기록가는 임시값(잔고 현재가) → record_fills가 실제 체결가로 고친다
                await self.db.log_order(code, name, "N3_SELL", qty, int(pos.get("current_price") or pos.get("buy_price") or 0))
                ord_no = str(res.get("ord_no") or "").strip().lstrip("0")
                if ord_no:
                    self.pending_fills[ord_no] = {"code": code, "name": name, "qty": qty}
                else:
                    await self.log("WARNING", f"[N3] {name}({code}) 매도 응답에 주문번호 없음 → 체결가 확인 못 함 (기록가는 전날 종가 근처)")
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

    async def record_fills(self, final: bool = False):
        """장전 매도의 실제 체결가·수량으로 N3_SELL 기록을 고친다. 전량 체결된 주문만 처리하고 나머지는 다음 확인으로 넘긴다.
        final=True(09:40 마지막 확인): 일부 체결이면 체결분만 기록하고 잔량을 알리며, 끝내 확인 못 한 주문은 경고 후 대기에서 뺀다"""
        data = await self.client.get_executions(priority=RequestPriority.HIGH)
        fills = parse_executions(data)
        for ord_no, p in list(self.pending_fills.items()):
            f = fills.get(ord_no)
            if f and (f["qty"] >= p["qty"] or final):
                price, qty = int(round(f["price"])), int(f["qty"])
                if await self.db.update_n3_sell_fill(p["code"], price, qty):
                    self.pending_fills.pop(ord_no)
                    partial = qty < p["qty"]
                    await self.log("WARNING" if partial else "SYSTEM",
                                   f"[N3] 매도 체결 확인: {p['name']}({p['code']}) {qty}/{p['qty']}주 평균 {price:,}원"
                                   + (f" → 잔량 {p['qty'] - qty}주 미체결, 확인 필요" if partial else ""), notify=partial)
                    continue
            if final:
                self.pending_fills.pop(ord_no)
                await self.log("WARNING", f"[N3] {p['name']}({p['code']}) 체결가 확인 실패(주문번호 {ord_no}, 조회 {'성공' if data else '실패'}) "
                                          f"→ N3_SELL 기록가는 실제 체결가가 아님", notify=True)
