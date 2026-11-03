"""
TOM(월말-월초) 스윙 매매 — AICompany 연구 20261030_154931 합격 규칙, **모의계좌 전용** (2026-11-02)

- 월말 판단(매월 마지막 거래일 15:21): 그 달 대상(그 달 첫 거래일 **전** 20거래일 평균 거래대금 상위 100 보통주,
  ETF 제외·코드 끝자리 0·전날 종가 1,000원 이상) 중 현재가 ≥ 60일 평균(오늘 포함) 이고 현재가 ≥ 20거래일 전 종가
- 하락장 필터: 대상 종목 같은 비중 시장 지수의 **전날** 값이 전날까지 120일 평균보다 낮으면 그날 진입 안 함
- 매수: 종목코드 순으로 빈 자리만큼 (동시 10종목, 종목당 총자산의 1/10), 종가 단일가(15:20~15:30)에 지정가(현재가 +2%)
- 매도: 진입 뒤 15거래일째 15:21 시장가(종가 단일가 체결)
- 주문 기록 side: TOM_BUY / TOM_SELL (TOM_SELL 0주 = 미체결·잔고 없음으로 기록만 닫음) — 재시작해도 DB로 상태 복원
- 시세(일봉)는 MARKET_DB_NAME(운영 DB)에서 읽기만, 주문·잔고·로그는 이 봇의 DB_NAME(모의 DB)에 쓴다
- 백테스트(harness)와 다른 점: 월말 판정은 KRX 휴장일 달력 기준(백테스트는 평일 달력), 오늘 종가 대신 15:21 현재가
"""
import asyncio
import os
import re
from datetime import date, datetime, timedelta, time as dtime
from typing import Callable, Dict, List, Optional

import pandas as pd

from async_kiwoom_client import RequestPriority, floor_tick, is_order_ok
from database import get_kst_now
from n3_trader import OrderPacer, exit_qty, parse_watch_quotes

HOLD_DAYS = 15        # 진입 뒤 이 거래일째 종가 청산 (탐색 구간 1위 조합)
SLOTS = 10            # 동시 보유 종목 수 (harness SLOTS)
TOP_N = 100           # 그 달 대상 종목 수 (spec universe.top_value)
MA_DAYS = 60
RET_DAYS = 20
MARKET_MA_DAYS = 120  # harness MARKET_MA_DAYS
MIN_PRICE = 1000
LOOKBACK_DAYS = 280   # 시세 조회 기간(달력일): 대상 종목 계산 20일 + 시장 지수 120일 + 첫 달 버림 여유
BUY_LIMIT_UP = 1.02   # 종가 단일가 매수 지정가 = 현재가 × 이 값 (BUY 시장가는 send_order가 지정가로 바꿈)
QUOTE_CHUNK = 50
T_RUN, T_RUN_END = dtime(15, 21), dtime(15, 28)


def next_trading_day(day: date, is_holiday: Callable[[datetime], bool]) -> date:
    d = day + timedelta(days=1)
    while is_holiday(datetime(d.year, d.month, d.day)):
        d += timedelta(days=1)
    return d


def is_month_end(day: date, is_holiday: Callable[[datetime], bool]) -> bool:
    """오늘이 그 달 마지막 거래일인지 (다음 거래일이 다른 달)"""
    return next_trading_day(day, is_holiday).month != day.month


def trading_days_after(start: date, end: date, is_holiday: Callable[[datetime], bool]) -> int:
    """start 다음 날부터 end까지(end 포함) 거래일 수"""
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        if not is_holiday(datetime(d.year, d.month, d.day)):
            n += 1
        d += timedelta(days=1)
    return n


def add_trading_days(start: date, n: int, is_holiday: Callable[[datetime], bool]) -> date:
    """start 뒤 n번째 거래일 (예정 청산일 표시용)"""
    d = start
    for _ in range(n):
        d = next_trading_day(d, is_holiday)
    return d


def next_month_end(day: date, is_holiday: Callable[[datetime], bool]) -> date:
    """day(포함) 이후 첫 월말 거래일 (다음 판단일 표시용)"""
    d = day
    while is_holiday(datetime(d.year, d.month, d.day)) or not is_month_end(d, is_holiday):
        d += timedelta(days=1)
    return d


def _frames(rows):
    """[(code, 'YYYYMMDD', close, value)] → (거래대금, 종가) 날짜×종목 표"""
    df = pd.DataFrame(rows, columns=["code", "date", "close", "value"])
    value = df.pivot(index="date", columns="code", values="value").sort_index().astype(float)
    close = df.pivot(index="date", columns="code", values="close").sort_index().astype(float)
    return value, close


def month_universes(value: pd.DataFrame, close: pd.DataFrame, top_n: int = TOP_N) -> Dict[str, List[str]]:
    """달별 대상 종목 (harness.universe_ranks와 같은 계산): 그 달 첫 거래일 기준 그 전 20거래일 평균 거래대금 상위.
    조회 기간의 첫 달은 첫 거래일이 잘려 있을 수 있어 뺀다"""
    avg = value.rolling(20, min_periods=20).mean().shift(1)
    prev_close = close.ffill().shift(1)
    months: Dict[str, List[str]] = {}
    for d in value.index:
        m = f"{d[:4]}-{d[4:6]}"
        if m not in months:
            a = avg.loc[d][prev_close.loc[d] >= MIN_PRICE].dropna().sort_values(ascending=False)
            months[m] = list(a.index[:top_n])
    first = min(months) if months else None
    return {m: v for m, v in months.items() if m != first}


def market_risk_off(close: pd.DataFrame, universes: Dict[str, List[str]], ma_days: int = MARKET_MA_DAYS) -> Optional[bool]:
    """오늘 하락장인지 (harness.market_returns·risk_off_days와 같은 계산): close는 어제까지의 종가.
    그날 대상 종목 같은 비중 하루 수익률을 누적한 지수의 어제 값 < 어제까지 ma_days일 평균이면 True. 날짜가 모자라면 None"""
    rets = close.pct_change()
    days = [d for d in rets.index if f"{d[:4]}-{d[4:6]}" in universes]
    out = []
    for d in days:
        members = [c for c in universes[f"{d[:4]}-{d[4:6]}"] if c in rets.columns]
        row = rets.loc[d, members].dropna()
        out.append(float(row.mean()) if len(row) else 0.0)
    if len(out) < ma_days:
        return None
    idx = pd.Series(out).add(1).cumprod()
    return bool(idx.iloc[-1] < idx.iloc[-ma_days:].mean())


def entry_ok(prior_closes: List[float], price: float) -> bool:
    """오늘 현재가로 진입 조건: 현재가 ≥ 60일 평균(어제까지 59일 + 오늘) and 현재가 ≥ 20거래일 전 종가"""
    if price <= 0 or len(prior_closes) < MA_DAYS - 1:
        return False
    ma = (sum(prior_closes[-(MA_DAYS - 1):]) + price) / MA_DAYS
    return price >= ma and price >= prior_closes[-RET_DAYS]


def pick_tom_orders(universe: List[str], prior: Dict[str, List[float]], prices: Dict[str, float],
                    held: set, slots: int, budget: float, cash: float) -> List[dict]:
    """종목코드 순(백테스트 포트폴리오와 같은 순서)으로 조건을 만족하는 종목을 빈 자리만큼.
    지정가 = 호가단위 내림(현재가 × BUY_LIMIT_UP), 수량 = min(예산, 남은 주문가능금액) // 지정가 (0주면 건너뜀).
    같은 날 종가 매도 대금은 주문 시점에 아직 못 쓰므로 합계는 주문가능금액(cash) 안"""
    picks, left = [], float(cash)
    for code in sorted(universe):
        if len(picks) >= slots:
            break
        p = float(prices.get(code) or 0)
        if code in held or not entry_ok(prior.get(code, []), p):
            continue
        limit = floor_tick(int(p * BUY_LIMIT_UP))
        qty = int(min(budget, left) // limit) if limit > 0 else 0
        if qty >= 1:
            left -= qty * limit
            picks.append({"code": code, "qty": qty, "price": limit, "cur": int(p)})
    return picks


def _market_db() -> str:
    """시세를 읽을 DB 이름 (식별자만 허용 — SQL에 그대로 들어감)"""
    name = os.getenv("MARKET_DB_NAME") or os.getenv("DB_NAME", "kiwoom_quant_db")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"MARKET_DB_NAME 형식 오류: {name!r}")
    return name


def _rehearsal_day() -> Optional[date]:
    """TOM_REHEARSAL_DATE(YYYY-MM-DD): 그날 1회만 월말처럼 진입(하락장 필터 무시) — 주문 경로 사전 검증용(11/2 대표 결정).
    매도는 평소대로 15거래일째. 리허설 거래는 전략 성과 평가에서 뺀다(로그 '리허설' 표시, 진입일이 월말이 아님)"""
    raw = (os.getenv("TOM_REHEARSAL_DATE") or "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _rehearsal_budget() -> int:
    """TOM_REHEARSAL_BUDGET(원): 리허설 때 종목당 예산 = 실계좌 금액(11/2 대표: 91,000원).
    10자리 제한 없이 이 돈으로 1주 이상 살 수 있는 조건 통과 종목을 전부 산다 → 종목마다 '실계좌 전액을 이 종목에' 결과. 0이면 평소 규칙"""
    try:
        return max(0, int(os.getenv("TOM_REHEARSAL_BUDGET") or 0))
    except ValueError:
        return 0


def rehearsal_compare(message, positions: List[dict], orders: List[dict]) -> Optional[dict]:
    """포탈 '리허설 비교' 카드: 리허설 로그('그룹 B(...): 코드 N주, ... / 그룹 A(...): ...')의 그룹별 수량 +
    매수가(잔고 평균단가, 없으면 마지막 TOM_BUY 지정가) + 현재가(잔고, 팔았으면 TOM_SELL 가격)로 그룹별 수익률. 로그가 없으면 None.
    message: 로그 1건 또는 시간순 목록 — 뒤 리허설에서 다시 주문한 종목은 그 수량으로 바꾼다(11/3 미체결 → 11/4 재주문).
    매수가 체결되지 않아 기록만 닫힌 종목(잔고 없음 + TOM_SELL 0원)은 빼고 계산.
    orders: [{code, name, side, price}] 시간순(첫 리허설 진입 이후)"""
    msgs = [m for m in ([message] if isinstance(message, str) else (message or [])) if m and " / 그룹 A" in m]
    if not msgs:
        return None
    qty = lambda part: {c: int(q) for c, q in re.findall(r"(\d{6}) (\d+)주", part)}
    groups, m = {"B": {}, "A": {}}, None
    for msg in msgs:
        b_part, a_part = msg.split(" / 그룹 A", 1)
        b, a = qty(b_part.split(":", 1)[-1]), qty(a_part.split(":", 1)[-1])
        for c in set(a) | set(b):  # 다시 주문한 종목은 앞 리허설 수량을 지우고 새 수량으로
            groups["A"].pop(c, None)
            groups["B"].pop(c, None)
        groups["B"].update(b)
        groups["A"].update(a)
        m = re.search(r"종목당 ([\d,]+)원", a_part) or m
    pos = {p["code"]: p for p in positions or []}
    names, buy, sell, unfilled = {}, {}, {}, set()
    for o in orders or []:
        names.setdefault(o["code"], o.get("name"))
        price = float(o.get("price") or 0)
        if o["side"] == "TOM_BUY":
            buy[o["code"]] = price
            unfilled.discard(o["code"])
        elif o["side"] == "TOM_SELL" and price > 0:
            sell[o["code"]] = price
        elif o["side"] == "TOM_SELL":
            unfilled.add(o["code"])  # 0원 = 매수 미체결로 기록만 닫음
    rows = []
    for code in sorted(set(groups["A"]) | set(groups["B"])):
        p = pos.get(code)
        if not p and code in unfilled:
            continue
        entry = float(p["buy_price"]) if p and float(p.get("buy_price") or 0) > 0 else buy.get(code)
        now = float(p["current_price"]) if p and float(p.get("current_price") or 0) > 0 else sell.get(code)
        rows.append({"code": code, "name": names.get(code) or (p or {}).get("name") or code,
                     "qty_a": groups["A"].get(code, 0), "qty_b": groups["B"].get(code, 0),
                     "entry": entry, "now": now, "sold": code in sell and not p,
                     "ret": (now / entry - 1) * 100 if entry and now else None})
    summary = {}
    for g, key in (("A", "qty_a"), ("B", "qty_b")):
        ok = [r for r in rows if r[key] and r["entry"] and r["now"]]
        invest = sum(r[key] * r["entry"] for r in ok)
        value = sum(r[key] * r["now"] for r in ok)
        summary[g] = {"stocks": sum(1 for r in rows if r[key]), "invest": invest, "value": value,
                      "ret": (value / invest - 1) * 100 if invest else None}
    return {"budget": int(m.group(1).replace(",", "")) if m else None, "groups": summary, "rows": rows}


class TomTrader:
    def __init__(self, bot):
        self.bot, self.db, self.client = bot, bot.db, bot.client
        self.orders = OrderPacer(bot.client)  # 주문 사이 1초 (11/3 리허설 429로 10건 중 5건 실패)
        self.done_on = None

    async def log(self, level: str, msg: str):
        print(msg)
        await self.db.log_message(level, msg)

    async def run(self):
        while not self.bot.is_shutdown:
            try:
                now = get_kst_now()
                today, t = now.strftime("%Y%m%d"), now.time()
                if not self.bot.is_korean_market_holiday(now) and T_RUN <= t < T_RUN_END and self.done_on != today:
                    self.done_on = today
                    await self.daily(now.date())
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.log("ERROR", f"[TOM/모의] 작업 예외: {e}")
                await asyncio.sleep(30.0)

    async def _paused(self) -> bool:
        return self.bot.is_paused or (await self.bot._remote_paused()) is True

    async def daily(self, today: date):
        if await self._paused():
            await self.log("SYSTEM", "[TOM/모의] 봇 일시정지 상태 → 오늘 매매 안 함")
            return
        opened = await self.db.get_tom_open()
        if opened is None:
            await self.log("WARNING", "[TOM/모의] 보유 기록 조회 실패(DB) → 오늘 매매 안 함")
            return
        await self.bot._sync_account_balance()
        held = {p["code"]: p for p in (await self.bot.portfolio.get_snapshot())["positions"]}
        is_holiday = self.bot.is_korean_market_holiday
        keep, exited = set(), set()
        for o in opened:
            code, name = o["code"], o["name"]
            entry = (o["entry_ts"] + timedelta(hours=9)).date()  # order_history.timestamp는 UTC
            pos = held.get(code)
            days = trading_days_after(entry, today, is_holiday)
            if not pos:
                # 어제까지 산 기록인데 잔고에 없음 = 종가 단일가 미체결 → 기록만 닫는다.
                # 단, 잔고 전체가 비어 있으면 잔고 TR 실패와 구분할 수 없어(닫으면 보유분이 영영 안 팔림) 보유 기간이 찰 때까지 미룬다
                if entry < today and (held or days >= HOLD_DAYS):
                    await self.db.log_order(code, name, "TOM_SELL", 0, 0)
                    await self.log("SYSTEM", f"[TOM/모의] {name}({code}) 잔고 없음(매수 미체결) → 기록만 닫음")
                elif entry < today:
                    keep.add(code)
                    await self.log("WARNING", f"[TOM/모의] {name}({code}) 잔고 목록이 비어 있음(TR 실패 가능) → 기록 유지, 다음 거래일 다시 확인")
                continue
            if days < HOLD_DAYS:
                keep.add(code)
                continue
            qty = exit_qty(int(pos.get("qty") or 0), int(o["qty"] or 0))
            res = await self.orders.send_order(code, qty, 0, order_type="03", side="SELL", priority=RequestPriority.HIGH)
            if is_order_ok(res):
                exited.add(code)
                await self.db.log_order(code, name, "TOM_SELL", qty, int(pos.get("current_price") or 0))
                await self.log("TRADE", f"[TOM/모의 매도 접수] {name}({code}) {qty}주 시장가 (진입 {entry:%m/%d}, {HOLD_DAYS}거래일 보유)")
            else:
                keep.add(code)  # 실패하면 다음 거래일 15:21에 다시 시도
                msg = (res or {}).get("return_msg") or (res or {}).get("msg1") or "응답 없음"
                await self.log("ERROR", f"[TOM/모의] 매도 거절: {name}({code}) {qty}주 - {msg} → 다음 거래일 재시도")
        rehearsal = today == _rehearsal_day() and not is_month_end(today, is_holiday)
        if is_month_end(today, is_holiday) or rehearsal:
            await self.enter(today, keep | exited, rehearsal)

    async def enter(self, today: date, held: set, rehearsal: bool = False):
        kst0 = datetime(today.year, today.month, today.day)
        bought = await self.db.count_side_since("TOM_BUY", (kst0 - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"))
        if bought != 0:
            await self.log("WARNING", f"[TOM/모의] 오늘 TOM 매수 기록 {bought}건(-1=DB 오류) → 다시 진입하지 않음")
            return
        start = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
        rows = await self.db.get_tom_market_rows(_market_db(), start, today.strftime("%Y%m%d"))
        if not rows:
            await self.log("WARNING", "[TOM/모의] 시세 조회 실패(0행) → 진입 안 함")
            return
        last, prev_day = max(r[1] for r in rows), today - timedelta(days=1)
        while self.bot.is_korean_market_holiday(datetime(prev_day.year, prev_day.month, prev_day.day)):
            prev_day -= timedelta(days=1)
        if last != prev_day.strftime("%Y%m%d"):
            await self.log("WARNING", f"[TOM/모의] 시세 마지막 날짜 {last} ≠ 전 거래일 {prev_day:%Y%m%d} (일봉 수집 누락?) → 진입 안 함")
            return
        value, close = _frames(rows)
        del rows
        universes = month_universes(value, close)
        universe = universes.get(f"{today:%Y-%m}", [])
        risk_off = market_risk_off(close, universes)
        if not universe or risk_off is None:
            await self.log("WARNING", f"[TOM/모의] 대상 {len(universe)}종목·하락장 판단 {risk_off} (데이터 부족) → 진입 안 함")
            return
        if risk_off and not rehearsal:
            await self.log("SYSTEM", f"[TOM/모의] {today:%m/%d} 월말이지만 하락장 필터(시장 지수 < {MARKET_MA_DAYS}일 평균) → 진입 안 함")
            return
        if rehearsal:
            await self.log("SYSTEM", f"[TOM/모의 리허설] {today:%m/%d} 주문 경로 검증용 1회 진입 (월말 아님, 하락장 필터 {'켜짐 → 무시' if risk_off else '꺼짐'}) — 전략 성과 평가 제외")
        prior = {c: close[c].dropna().tolist() for c in universe if c in close.columns}
        prices: Dict[str, float] = {}
        for i in range(0, len(universe), QUOTE_CHUNK):
            data = await self.client.get_watch_quotes(universe[i:i + QUOTE_CHUNK], priority=RequestPriority.HIGH)
            if data is None:
                await self.log("WARNING", f"[TOM/모의] ka10095 시세 조회 실패 ({i}~{i + QUOTE_CHUNK}번째)")
            prices.update({c: q["cur"] for c, q in parse_watch_quotes(data).items()})
        cash = float(self.bot.portfolio.current_capital or 0)
        total = float(self.bot.portfolio.total_asset or cash)
        budget = total / SLOTS
        slots = max(0, SLOTS - len(held))
        picks = pick_tom_orders(universe, prior, prices, held, slots, budget, cash)
        if rehearsal and _rehearsal_budget():
            # 리허설 비교(11/2 대표): B=원래 TOM 10자리 + A=실계좌 금액으로 살 수 있는 종목 전부, 남은 주문가능금액 안에서.
            # 같은 종목은 한 주문으로 합치고(같은 지정가·같은 매도일) 그룹별 수량은 로그로 남겨 11/24 뒤 따로 계산
            small = pick_tom_orders(universe, prior, prices, held, len(universe), float(_rehearsal_budget()),
                                    cash - sum(p["qty"] * p["price"] for p in picks))
            await self.log("SYSTEM", "[TOM/모의 리허설] 그룹 B(원래 10자리): " + ", ".join(f"{p['code']} {p['qty']}주" for p in picks)
                           + f" / 그룹 A(종목당 {_rehearsal_budget():,}원): " + ", ".join(f"{p['code']} {p['qty']}주" for p in small))
            merged = {p["code"]: dict(p) for p in picks}
            for p in small:
                if p["code"] in merged:
                    merged[p["code"]]["qty"] += p["qty"]
                else:
                    merged[p["code"]] = dict(p)
            picks = [merged[c] for c in sorted(merged)]
        names = await self.db.get_stock_names(_market_db(), [p["code"] for p in picks])
        await self.log("SYSTEM", f"[TOM/모의{' 리허설' if rehearsal else ''}] {today:%m/%d} {'리허설' if rehearsal else '월말'} 판단: 대상 {len(universe)}종목, 시세 {len(prices)}종목, "
                                 f"보유 {len(held)}, 빈 자리 {slots}, 종목당 예산 {int(budget):,}원(주문가능 {int(cash):,}원) → 주문 {len(picks)}건: "
                                 + ", ".join(f"{names.get(p['code'], p['code'])}({p['code']}) {p['qty']}주@{p['price']:,}" for p in picks))
        for p in picks:
            name = names.get(p["code"], p["code"])
            res = await self.orders.send_order(p["code"], p["qty"], p["price"], order_type="00", side="BUY",
                                               priority=RequestPriority.HIGH)
            if is_order_ok(res) or res is None:
                # 응답 없음은 접수됐을 수도 있어 기록 (미체결이면 다음 거래일에 잔고 없음으로 닫힘)
                await self.db.log_order(p["code"], name, "TOM_BUY", p["qty"], p["price"])
                await self.log("TRADE" if res else "WARNING",
                               f"[TOM/모의 매수 {'접수' if res else '응답 없음(접수 여부 미상)'}] {name}({p['code']}) "
                               f"{p['qty']}주 지정가 {p['price']:,}원 (현재가 {p['cur']:,}원, 종가 단일가)")
            else:
                msg = (res or {}).get("return_msg") or (res or {}).get("msg1") or ""
                await self.log("ERROR", f"[TOM/모의] 매수 거절: {name}({p['code']}) {p['qty']}주@{p['price']:,} - {msg}")
