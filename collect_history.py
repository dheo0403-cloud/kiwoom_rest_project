"""과거 시세·종목 정보 수집 (키움 REST → MariaDB)

- 종목 정보(ka10099): 코스피·코스닥·ETF 전 종목 코드·이름·시장 → stock_master (ETF 여부 판별용)
- 일봉(ka10081, 수정주가): 이어받기(cont-yn/next-key)로 기준일부터 과거로 N년치 → daily_ohlcv
- 1분봉(ka10080, 수정주가): 이어받기로 최근 N거래일치, 시각은 14자리(YYYYMMDDHHMMSS) → minute_ohlcv
- 키움 REST는 등록된 IP에서만 토큰이 발급되므로 운영 Pod(등록 IP)에서 실행한다.

사용 예 (Pod 안에서):
  python collect_history.py --master
  python collect_history.py --daily 148070 132030 --years 3
  python collect_history.py --daily-watchlist --minute-watchlist --days 1
"""
import argparse
import asyncio
import os
from datetime import timedelta
from typing import Any, Dict, List, Optional

from database import get_kst_now

STOCK_LIST_MARKETS = (("0", "KOSPI"), ("10", "KOSDAQ"), ("8", "ETF"))  # ka10099 mrkt_tp (ETF를 마지막에 둬서 ETF 표시가 남게)
MAX_PAGES = 50  # 이어받기 안전 한도 (일봉 1페이지 ≈ 600일, 분봉 1페이지 ≈ 900봉)


def _num(v: Any) -> int:
    """키움 숫자 문자열(+/- 부호, 콤마) → 절댓값 정수"""
    try:
        return abs(int(float(str(v).replace(',', '').strip() or 0)))
    except ValueError:
        return 0


async def _paged(client, api_id: str, path: str, payload: Dict[str, Any], list_key: str,
                 stop=lambda rows: False) -> List[Dict[str, Any]]:
    """이어받기 조회: 응답 헤더 cont-yn=Y 이고 stop(이번 페이지)이 False인 동안 next-key로 다음 페이지 요청"""
    rows, next_key = [], None
    for _ in range(MAX_PAGES):
        headers = {"cont-yn": "Y", "next-key": next_key} if next_key else None
        data, resp_headers = await client.request(api_id, f"{client.base_url}{path}", payload, headers_override=headers)
        page = (data or {}).get(list_key) or []
        if not isinstance(page, list) or not page:
            if data and str(data.get('return_code', 0)) not in ('0', ''):
                print(f"⚠️ [{api_id}] 업무 오류: {data.get('return_msg')}")
            break
        rows.extend(page)
        if stop(page) or not resp_headers or str(resp_headers.get('cont-yn', 'N')).upper() != 'Y':
            break
        next_key = resp_headers.get('next-key')
        if not next_key:
            break
    return rows


def parse_daily(code: str, items: List[Dict[str, Any]]) -> List[tuple]:
    out = []
    for it in items:
        dt = str(it.get('dt') or '').strip()
        if len(dt) != 8:
            continue
        out.append((code, dt, _num(it.get('open_pric')), _num(it.get('high_pric')), _num(it.get('low_pric')),
                    _num(it.get('cur_prc')), _num(it.get('trde_qty')), _num(it.get('trde_prica'))))
    return out


# 저장할 장 시간 (HHMM, 포함). 키움 분봉에는 넥스트레이드 장전·장후(08~20시) 봉도 섞여 와서 정규장만 남긴다.
MINUTE_SESSION = tuple(os.getenv("MINUTE_SESSION", "0900-1530").split("-"))


def parse_minute(code: str, items: List[Dict[str, Any]]) -> List[tuple]:
    out = []
    for it in items:
        tm = str(it.get('cntr_tm') or '').strip()
        if len(tm) != 14 or not tm.isdigit() or not (MINUTE_SESSION[0] <= tm[8:12] <= MINUTE_SESSION[1]):
            continue
        out.append((code, tm, _num(it.get('open_pric')), _num(it.get('high_pric')), _num(it.get('low_pric')),
                    _num(it.get('cur_prc')), _num(it.get('trde_qty'))))
    return out


async def collect_daily(client, db, code: str, years: float = 0.0, base_dt: Optional[str] = None) -> int:
    """일봉 수집 (years=0이면 첫 페이지만 = 최근 약 600거래일)"""
    base_dt = base_dt or get_kst_now().strftime('%Y%m%d')
    cutoff = (get_kst_now() - timedelta(days=365 * years)).strftime('%Y%m%d') if years > 0 else None
    stop = (lambda page: True) if not cutoff else (lambda page: min(str(p.get('dt', '99999999')) for p in page) <= cutoff)
    items = await _paged(client, "ka10081", "/api/dostk/chart",
                         {"stk_cd": code, "base_dt": base_dt, "upd_stkpc_tp": "1"}, "stk_dt_pole_chart_qry", stop)
    rows = [r for r in parse_daily(code, items) if not cutoff or r[1] >= cutoff]
    await db.upsert_daily_rows(rows)
    return len(rows)


async def collect_minute(client, db, code: str, days: int = 1, base_dt: Optional[str] = None) -> int:
    """1분봉 수집: 최근 days거래일치 (휴장일 포함 달력일 기준 여유 있게 잘라냄)"""
    base_dt = base_dt or get_kst_now().strftime('%Y%m%d')
    cutoff = (get_kst_now() - timedelta(days=max(1, days) * 2)).strftime('%Y%m%d')
    stop = lambda page: min(str(p.get('cntr_tm', '9' * 14)) for p in page)[:8] < cutoff
    items = await _paged(client, "ka10080", "/api/dostk/chart",
                         {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": base_dt},
                         "stk_min_pole_chart_qry", stop)
    rows = parse_minute(code, items)
    trade_days = sorted({r[1][:8] for r in rows}, reverse=True)[:max(1, days)]
    rows = [r for r in rows if r[1][:8] in trade_days]
    await db.upsert_minute_rows(rows)
    return len(rows)


async def backfill_minute_step(client, db, code: str, state: Optional[Dict[str, Any]], max_days: int,
                               pages: int = 20) -> Dict[str, Any]:
    """분봉 과거 백필 1단계: 진행표의 가장 오래된 날짜(없으면 오늘)부터 과거로 최대 pages페이지 받아 저장.
    더 받을 게 없거나(키움 보관 한도) 목표 기간에 닿으면 완료 처리. 반환: 갱신된 진행 상태"""
    state = state or {"oldest_date": None, "rows_saved": 0, "done": 0}
    cutoff = (get_kst_now() - timedelta(days=max_days)).strftime('%Y%m%d')
    base_dt = state["oldest_date"] or get_kst_now().strftime('%Y%m%d')
    rows, next_key, reason = [], None, None
    for _ in range(pages):
        headers = {"cont-yn": "Y", "next-key": next_key} if next_key else None
        data, resp_headers = await client.request("ka10080", f"{client.base_url}/api/dostk/chart",
                                                  {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": base_dt},
                                                  headers_override=headers)
        raw = (data or {}).get("stk_min_pole_chart_qry") or []
        page = parse_minute(code, raw)  # 정규장 봉만 (완료 판단은 원본 응답 기준)
        raw_dates = [str(it.get("cntr_tm", ""))[:8] for it in raw if len(str(it.get("cntr_tm", ""))) == 14]
        if data is None or str(data.get("return_code", 0)) not in ("0", ""):
            reason = "error"  # 네트워크·업무 오류(요청 한도 등): 완료 처리하지 않고 다음에 재시도
            if data:
                print(f"⚠️ [분봉 백필] {code} 업무 오류: {data.get('return_msg')}")
            break
        if not raw_dates:
            reason = "no_more"
            break
        rows.extend(page)
        if min(raw_dates) <= cutoff:
            reason = "reached_target"
            break
        if not resp_headers or str(resp_headers.get('cont-yn', 'N')).upper() != 'Y' or not resp_headers.get('next-key'):
            reason = "no_more"
            break
        next_key = resp_headers.get('next-key')
    rows = [r for r in rows if r[1][:8] >= cutoff]
    await db.upsert_minute_rows(rows)
    oldest = min([r[1][:8] for r in rows] + ([state["oldest_date"]] if state["oldest_date"] else []), default=None)
    done = reason in ("no_more", "reached_target")
    if reason is None and oldest == state["oldest_date"]:
        done, reason = True, "no_progress"  # 같은 날짜만 반복되면 무한 반복 방지
    new_state = {"oldest_date": oldest, "rows_saved": int(state["rows_saved"]) + len(rows), "done": int(done)}
    await db.upsert_minute_backfill(code, oldest, new_state["rows_saved"], done, reason if done else None)
    return new_state


async def refresh_stock_master(client, db) -> int:
    """ka10099 전 종목 목록 → stock_master (코스피·코스닥·ETF)"""
    rows = []
    for mrkt_tp, market in STOCK_LIST_MARKETS:
        items = await _paged(client, "ka10099", "/api/dostk/stkinfo", {"mrkt_tp": mrkt_tp}, "list")
        for it in items:
            code = str(it.get('code') or '').strip()
            if len(code) == 6:
                rows.append((code, str(it.get('name') or '').strip(), market, 1 if market == "ETF" else 0))
        print(f"  📋 [종목정보] {market}: {len(items)}건")
    await db.upsert_stock_master(rows)
    return len(rows)


async def collect_after_close(client, db, watch_codes: List[str], extra_codes: List[str],
                              minute_universe: str = "watchlist") -> Dict[str, int]:
    """장 마감 후 일일 수집: 종목 정보 갱신 + (관심·보유·추가 종목) 일봉 최신 페이지
    + 당일 1분봉 (minute_universe='all'이면 stock_master 보통주 전체, 아니면 관심·보유)"""
    stats = {"master": 0, "daily": 0, "minute": 0, "minute_codes": 0}
    await db.ensure_stock_master()
    stats["master"] = await refresh_stock_master(client, db)
    for code in sorted(set(watch_codes) | set(extra_codes)):
        stats["daily"] += await collect_daily(client, db, code)
    minute_codes = set(watch_codes)
    if minute_universe == "all":
        minute_codes |= set(await db.get_stock_universe())
    stats["minute_codes"] = len(minute_codes)
    for code in sorted(minute_codes):
        stats["minute"] += await collect_minute(client, db, code, days=1)
    return stats


async def _main(args):
    from async_kiwoom_client import AsyncKiwoomClient
    from database import DatabaseManager
    client, db = AsyncKiwoomClient(), DatabaseManager()
    await db.init_pool()
    await client.start()
    try:
        if not await client.get_access_token():
            raise SystemExit("❌ 토큰 발급 실패 (키움 등록 IP·앱키 확인)")
        await db.ensure_stock_master()
        watch = []
        if args.daily_watchlist or args.minute_watchlist:
            watch = sorted({str(r['code']) for r in (await db.get_watchlist_items() or [])} |
                           {str(r['code']) for r in (await db.get_portfolio_positions() or [])})
        if args.master:
            print(f"✅ 종목 정보 {await refresh_stock_master(client, db):,}건")
        for code in (args.daily or []) + (watch if args.daily_watchlist else []):
            print(f"✅ 일봉 {code}: {await collect_daily(client, db, code, years=args.years):,}행")
        for code in (args.minute or []) + (watch if args.minute_watchlist else []):
            print(f"✅ 분봉 {code}: {await collect_minute(client, db, code, days=args.days):,}행")
    finally:
        await client.stop()
        await db.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="키움 과거 시세·종목 정보 수집 (DB upsert)")
    p.add_argument("--master", action="store_true", help="종목 정보(ka10099) 갱신")
    p.add_argument("--daily", nargs="*", help="일봉 수집 종목")
    p.add_argument("--daily-watchlist", action="store_true", help="관심·보유 종목 일봉")
    p.add_argument("--years", type=float, default=0.0, help="일봉 과거 연수 (0 = 최근 1페이지)")
    p.add_argument("--minute", nargs="*", help="1분봉 수집 종목")
    p.add_argument("--minute-watchlist", action="store_true", help="관심·보유 종목 1분봉")
    p.add_argument("--days", type=int, default=1, help="1분봉 최근 거래일 수")
    asyncio.run(_main(p.parse_args()))
