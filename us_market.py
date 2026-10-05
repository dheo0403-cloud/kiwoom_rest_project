"""미국 시장 야간 데이터 (Yahoo 차트 API, 비공식) → us_daily 테이블

- 대상: US_MARKET_SYMBOLS 환경변수(쉼표 구분), 기본 S&P500·나스닥·필라델피아 반도체·VIX·원달러.
- 날짜는 각 거래소 현지 날짜(meta.exchangeTimezoneName). 아직 끝나지 않은 당일 봉은 저장하지 않는다.
- 실패하면 아무것도 저장하지 않고 None/빈 결과를 돌려준다 (가짜 값으로 채우지 않음).

사용 예:
  python us_market.py --range 5y      # 과거치 적재
  python us_market.py                 # 최근 1개월 갱신 + 최신 요약 출력
"""
import argparse
import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import aiohttp

DEFAULT_SYMBOLS = "^GSPC,^IXIC,^SOX,^VIX,KRW=X"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SETTLE_HOUR = 17  # 현지 17시 이후에만 당일 봉을 확정으로 간주 (미국 정규장 16시 마감 + 여유)


def symbols() -> List[str]:
    return [s.strip() for s in os.getenv("US_MARKET_SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]


def parse_chart(symbol: str, payload: Dict[str, Any], now_utc: Optional[datetime] = None) -> List[tuple]:
    """Yahoo chart JSON → [(symbol, YYYYMMDD, close)] (확정된 봉만)"""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return []
    res = result[0]
    tz = ZoneInfo((res.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York")
    closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    local_now = (now_utc or datetime.now(ZoneInfo("UTC"))).astimezone(tz)
    rows = {}
    for ts, close in zip(res.get("timestamp") or [], closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, tz)
        if d.date() == local_now.date() and local_now.hour < SETTLE_HOUR:
            continue  # 진행 중인 당일 봉
        rows[d.strftime("%Y%m%d")] = float(close)
    return [(symbol, d, c) for d, c in sorted(rows.items())]


async def fetch_symbol(session: aiohttp.ClientSession, symbol: str, rng: str = "1mo") -> List[tuple]:
    try:
        async with session.get(CHART_URL.format(symbol=symbol), params={"range": rng, "interval": "1d"},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print(f"⚠️ [미국시장] {symbol} HTTP {r.status}")
                return []
            return parse_chart(symbol, await r.json())
    except Exception as e:
        print(f"⚠️ [미국시장] {symbol} 조회 실패: {type(e).__name__} {e}")
        return []


async def refresh_us_daily(db, rng: str = "1mo") -> int:
    """모든 대상 심볼 일봉을 받아 us_daily에 upsert, 저장 행 수 반환"""
    await db.ensure_us_daily()
    total = 0
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        for sym in symbols():
            rows = await fetch_symbol(session, sym, rng)
            await db.upsert_us_daily(rows)
            total += len(rows)
    return total


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """us_daily 최근 행들(symbol, date, close) → 심볼별 최신 종가·전일 대비 등락률(%)"""
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: (r["symbol"], r["date"])):
        by_sym.setdefault(r["symbol"], []).append(r)
    out = {}
    for sym, rs in by_sym.items():
        last = rs[-1]
        prev = rs[-2] if len(rs) >= 2 else None
        out[sym] = {"date": last["date"], "close": float(last["close"]),
                    "change_pct": (float(last["close"]) / float(prev["close"]) - 1) * 100 if prev and float(prev["close"]) else None}
    return out


async def _main(args):
    from database import DatabaseManager
    db = DatabaseManager()
    await db.init_pool()
    try:
        print(f"✅ us_daily {await refresh_us_daily(db, args.range):,}행 저장")
        for sym, s in summarize(await db.get_recent_us_daily()).items():
            chg = f"{s['change_pct']:+.2f}%" if s['change_pct'] is not None else "-"
            print(f"  {sym:<7} {s['date']} {s['close']:,.2f} ({chg})")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="미국 시장 일봉(Yahoo) → us_daily")
    p.add_argument("--range", default="1mo", help="Yahoo range (1mo, 1y, 5y 등)")
    asyncio.run(_main(p.parse_args()))
