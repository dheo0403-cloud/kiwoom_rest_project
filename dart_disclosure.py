"""DART 공시 수집 (금융감독원 OpenDART 공시검색 list.json) → dart_disclosure (공시 이벤트 연구용, 매매에는 쓰지 않음)

- 응답에는 접수 날짜(rcept_dt)만 있고 시각이 없다 → 장중 폴링으로 처음 본 시각(first_seen_at, KST)을 기록.
  과거 백필분·봇 시작 직후 첫 조회분은 시각을 알 수 없어 NULL.
- 유가(Y)·코스닥(K) 법인만. corp_cls는 한 번에 하나만 되므로 두 번 조회.
- corp_code 없이 조회하면 검색 기간이 3개월로 제한 → 백필은 하루씩 조회.
- 키(DART_API_KEY)가 없거나 오류 응답이면 저장하지 않고 None (가짜 값으로 채우지 않음). 하루 호출 한도 약 2만 건.

사용 예:
  python dart_disclosure.py --day 20261022     # 하루치 조회·저장 (DART_API_KEY 필요)
"""
import argparse
import asyncio
import os
from typing import Any, Dict, List, Optional

import aiohttp

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
CORP_CLASSES = ("Y", "K")  # 유가증권, 코스닥
PAGE_COUNT = 100           # 페이지당 최대 건수
MAX_PAGES = 50             # 하루·법인구분당 안전 한도


def parse_list(payload: Dict[str, Any], first_seen_at=None) -> List[tuple]:
    """list.json 응답 → dart_disclosure 행 튜플 (접수번호 14자리가 아닌 항목은 버림)"""
    out = []
    for it in (payload or {}).get("list") or []:
        rcept_no = str(it.get("rcept_no") or "").strip()
        rcept_dt = str(it.get("rcept_dt") or "").strip()
        if len(rcept_no) != 14 or len(rcept_dt) != 8:
            continue
        out.append((rcept_no, rcept_dt, str(it.get("corp_code") or "").strip(), str(it.get("corp_name") or "").strip()[:100],
                    str(it.get("stock_code") or "").strip()[:6], str(it.get("corp_cls") or "").strip()[:1],
                    str(it.get("report_nm") or "").strip()[:300], str(it.get("flr_nm") or "").strip()[:100],
                    str(it.get("rm") or "").strip()[:20], first_seen_at))
    return out


class DartClient:
    """list.json 호출 + 하루 호출 수 집계 (KST 날짜가 바뀌면 0부터)"""

    def __init__(self, api_key: str, daily_limit: int = 18000):
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.calls, self.calls_day = 0, None
        self.session: Optional[aiohttp.ClientSession] = None

    def budget_left(self, today: str) -> int:
        if self.calls_day != today:
            self.calls, self.calls_day = 0, today
        return self.daily_limit - self.calls

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_page(self, day: str, corp_cls: str, page_no: int, today: str) -> Optional[Dict[str, Any]]:
        """한 페이지. 정상 → 응답 dict(데이터 없음 013은 빈 list), 오류·한도 초과 → None"""
        if self.budget_left(today) <= 0:
            return None
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.calls += 1
        params = {"crtfc_key": self.api_key, "bgn_de": day, "end_de": day, "corp_cls": corp_cls,
                  "sort": "date", "sort_mth": "desc", "page_no": str(page_no), "page_count": str(PAGE_COUNT)}
        try:
            async with self.session.get(LIST_URL, params=params) as r:
                if r.status != 200:
                    print(f"⚠️ [DART] HTTP {r.status} ({day} {corp_cls} p{page_no})")
                    return None
                data = await r.json(content_type=None)
        except Exception as e:
            print(f"⚠️ [DART] 조회 실패: {type(e).__name__} {e}")
            return None
        status = str(data.get("status", ""))
        if status == "013":
            return {"list": [], "total_page": 0}
        if status != "000":
            print(f"⚠️ [DART] status {status}: {data.get('message')}")  # 010 미등록 키, 020 한도 초과 등
            return None
        return data


async def fetch_day(dart: DartClient, day: str, today: str, known: Optional[set] = None,
                    first_seen_at=None) -> Optional[List[tuple]]:
    """하루치 공시(유가·코스닥). known이 주어지면 최신순으로 읽다가 이미 아는 접수번호가 나온 페이지에서 멈춤(장중 폴링).
    중간에 오류가 나면 None (부분 결과로 백필 완료 처리하지 않게)"""
    rows = []
    for cls in CORP_CLASSES:
        page_no = 1
        while page_no <= MAX_PAGES:
            data = await dart.fetch_page(day, cls, page_no, today)
            if data is None:
                return None
            page = parse_list(data, first_seen_at)
            rows.extend(page)
            if known is not None and any(r[0] in known for r in page):
                break
            if page_no >= int(data.get("total_page") or 0):
                break
            page_no += 1
            await asyncio.sleep(0.2)
    return rows


async def _main(args):
    from database import DatabaseManager, get_kst_now
    key = os.getenv("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY가 없습니다.")
        return
    db, dart = DatabaseManager(), DartClient(key)
    await db.init_pool()
    try:
        await db.ensure_dart()
        rows = await fetch_day(dart, args.day, get_kst_now().strftime('%Y%m%d'))
        if rows is None:
            print("❌ 조회 실패 (저장 안 함)")
            return
        print(f"✅ {args.day} 공시 {len(rows):,}건, 저장 {'성공' if await db.upsert_dart_rows(rows) else '실패'}, 호출 {dart.calls}회")
    finally:
        await dart.close()
        await db.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="DART 공시 하루치 → dart_disclosure")
    p.add_argument("--day", required=True, help="YYYYMMDD")
    asyncio.run(_main(p.parse_args()))
