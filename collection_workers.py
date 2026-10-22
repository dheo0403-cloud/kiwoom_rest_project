"""장 시간 외 수집 작업 (AsyncTradingBot에 섞어 쓰는 믹스인)
- 장 마감 후 시세 수집·일일 채점, 분봉 과거 백필, 투자자 순매수·NXT 일봉 동기화, 수정주가 재수집, NXT 분봉 백필
- self.client·self.db·self.is_shutdown·self.is_korean_market_holiday 등은 AsyncTradingBot이 제공한다
"""
import asyncio
import os
from datetime import datetime, timedelta
from typing import Dict

from database import get_kst_now
from dart_disclosure import DartClient, fetch_day
from collect_history import (collect_after_close, backfill_minute_step, refresh_stock_master, collect_investor, collect_nxt_daily,
                             collect_daily, refetch_minute_adjusted, minute_daily_mismatch, collect_nxt_minute_day,
                             NXT_AFTER_SESSION, NXT_PRE_SESSION)


class CollectionWorkersMixin:
    async def collect_market_history(self):
        """장 마감 후 시세 수집: 종목 정보·일봉(MINUTE_UNIVERSE 전체+관심·보유+EXTRA_DAILY_CODES)·당일 1분봉(전체+관심·보유). 실패해도 데몬은 계속"""
        self.history_collected_on = get_kst_now().date()  # 실패해도 같은 날 반복 호출 방지
        watch = sorted(set(self.watchlist) | set(self.portfolio.positions))
        extra = [c.strip() for c in os.getenv("EXTRA_DAILY_CODES", "").split(",") if c.strip()]
        try:
            stats = await collect_after_close(self.client, self.db, watch, extra, self.minute_universe)
            msg = (f"장 마감 후 시세 수집 완료: 종목정보 {stats['master']:,}건, 일봉 {stats['daily']:,}행({stats['daily_codes']:,}종목), "
                   f"1분봉 {stats['minute']:,}행({stats['minute_codes']:,}종목)")
            print(f"🗄️ [수집] {msg}")
            await self.db.log_message("SYSTEM", msg)
        except Exception as e:
            print(f"❌ [수집 오류] {e}")
            await self.db.log_message("ERROR", f"장 마감 후 시세 수집 오류: {e}")
        await self.run_daily_scorecard()

    def _is_last_trading_day_of_week(self, now: datetime) -> bool:
        """다음 거래일이 다른 주(ISO 주차)면 오늘이 이번 주 마지막 거래일"""
        nxt = now + timedelta(days=1)
        while self.is_korean_market_holiday(nxt):
            nxt += timedelta(days=1)
        return nxt.isocalendar()[:2] != now.isocalendar()[:2]

    async def run_daily_scorecard(self):
        """장 마감 수집 직후 일일 채점 리포트(실제 매수 + 거른 신호)를 logs(level=REPORT)에 저장. 실패해도 데몬은 계속"""
        if not self.daily_report_enabled:
            return
        try:
            import skipped_signal_report  # 조회 전용 (pymysql 동기 → 스레드에서 실행)
            text = await asyncio.to_thread(skipped_signal_report.build_daily_report, self.daily_report_days)
            print(f"📊 {text}")
            await self.db.log_message("REPORT", text)
            if self._is_last_trading_day_of_week(get_kst_now()):  # 주의 마지막 거래일이면 주간 판정표도 저장
                weekly = await asyncio.to_thread(skipped_signal_report.build_weekly_report)
                print(f"📅 {weekly}")
                await self.db.log_message("WEEKLY", weekly)
        except Exception as e:
            print(f"⚠️ [일일 채점 오류] {e}")
            await self.db.log_message("WARNING", f"일일 채점 리포트 오류: {e}")

    def _backfill_allowed(self, now: datetime) -> bool:
        """백필 허용 시간: 휴장일 종일, 거래일은 MINUTE_BACKFILL_WINDOW(기본 16:10~익일 08:00)"""
        if self.is_korean_market_holiday(now):
            return True
        (sh, sm), (eh, em) = self.backfill_window
        t = (now.hour, now.minute)
        return t >= (sh, sm) or t < (eh, em)

    async def _minute_backfill_worker(self):
        """전 종목 분봉 과거 백필 (장 시간 외, 종목별 20페이지씩 돌아가며, 진행표로 재시작 후 이어받기)"""
        await self.db.ensure_minute_backfill()
        announced_done = False
        while not self.is_shutdown:
            try:
                if not self._backfill_allowed(get_kst_now()):
                    await asyncio.sleep(60.0)
                    continue
                universe = await self.db.get_stock_universe()
                if not universe:  # stock_master가 비어 있으면 직접 채움 (장 마감 후 수집을 기다리지 않음)
                    await self.db.ensure_stock_master()
                    n = await refresh_stock_master(self.client, self.db)
                    await self.db.log_message("SYSTEM", f"분봉 백필 준비: 종목 정보 {n:,}건 갱신")
                    universe = await self.db.get_stock_universe()
                    if not universe:
                        await asyncio.sleep(600.0)
                        continue
                states = await self.db.get_minute_backfill()
                pending = [c for c in universe if not int((states.get(c) or {}).get("done", 0))]
                if not pending:
                    if not announced_done:
                        await self.db.log_message("SYSTEM", f"분봉 과거 백필 완료: {len(universe):,}종목")
                        announced_done = True
                    await asyncio.sleep(3600.0)
                    continue
                announced_done = False
                rows_before, steps, errors = sum(int((states.get(c) or {}).get("rows_saved", 0)) for c in universe), 0, 0
                for code in pending:
                    if self.is_shutdown or not self._backfill_allowed(get_kst_now()):
                        break
                    before = states.get(code)
                    after = await backfill_minute_step(self.client, self.db, code, before, self.minute_backfill_days)
                    steps += 1
                    if not after["done"] and after["oldest_date"] == (before or {}).get("oldest_date"):
                        errors += 1
                        await asyncio.sleep(5.0)  # 오류·무진전: 잠시 쉬고 다음 종목
                    states[code] = after
                rows_after = sum(int((states.get(c) or {}).get("rows_saved", 0)) for c in universe)
                await self.db.log_message("SYSTEM", f"분봉 백필 라운드: {steps:,}종목 처리, 신규 {rows_after - rows_before:,}행, "
                                                    f"오류·무진전 {errors}, 남은 종목 {sum(1 for c in universe if not int((states.get(c) or {}).get('done', 0))):,}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [분봉 백필 오류] {e}")
                await self.db.log_message("ERROR", f"분봉 백필 오류: {e}")
                await asyncio.sleep(60.0)

    def _investor_sync_allowed(self, now: datetime) -> bool:
        """투자자 순매수 동기화 시간: 휴장일 종일, 거래일은 INVESTOR_SYNC_HOUR(기본 18시) 이후 ~ 08시 전"""
        return self.is_korean_market_holiday(now) or now.hour >= self.investor_sync_hour or now.hour < 8

    async def _investor_sync_worker(self):
        """보통주 전체 외국인·기관·개인 일별 순매수 동기화: 처음 보는 종목은 INVESTOR_BACKFILL_YEARS년 과거까지,
        이미 있는 종목은 최근 1페이지. 하루 1라운드 (라운드 도중 시간이 끝나면 다음 날 이어서)"""
        await self.db.ensure_investor_daily()
        synced_on = None
        while not self.is_shutdown:
            try:
                now = get_kst_now()
                round_day = (now - timedelta(hours=8)).date()  # 18시~익일 08시를 같은 라운드로 묶음
                if not self._investor_sync_allowed(now) or synced_on == round_day:
                    await asyncio.sleep(300.0)
                    continue
                universe = await self.db.get_stock_universe()
                if not universe:
                    await asyncio.sleep(600.0)
                    continue
                coverage = await self.db.get_investor_coverage()
                new_codes = sum(1 for c in universe if c not in coverage)
                rows, done, empty = 0, 0, 0
                for code in universe:
                    if self.is_shutdown or not self._investor_sync_allowed(get_kst_now()):
                        break
                    n = await collect_investor(self.client, self.db, code,
                                               self.investor_backfill_years if code not in coverage else 0.0)
                    rows += n
                    done += 1
                    empty += (n == 0)
                finished = done == len(universe)
                if finished:
                    synced_on = round_day
                oldest = min((await self.db.get_investor_coverage()).values(), default=None)
                await self.db.log_message("SYSTEM", f"투자자 순매수 동기화 {'완료' if finished else '중단(시간 종료)'}: "
                                                    f"{done:,}/{len(universe):,}종목(신규 {new_codes:,}), {rows:,}행, "
                                                    f"빈 응답 {empty:,}, 가장 오래된 날짜 {oldest}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [투자자 순매수 동기화 오류] {e}")
                await self.db.log_message("ERROR", f"투자자 순매수 동기화 오류: {e}")
                await asyncio.sleep(600.0)

    def _nxt_sync_allowed(self, now: datetime) -> bool:
        """NXT 일봉 동기화 시간: 휴장일 종일, 거래일은 NXT_SYNC_HOUR(기본 20시, 애프터마켓 종료) 이후 ~ 08시 전"""
        return self.is_korean_market_holiday(now) or now.hour >= self.nxt_sync_hour or now.hour < 8

    async def _nxt_sync_worker(self):
        """넥스트레이드 일봉 동기화 (하루 1라운드). NXT 거래가 확인된 종목은 매일, 아직 확인 안 된 종목은
        첫 라운드와 토요일 라운드에만 조회 (NXT 비거래 종목 약 1,700개를 매일 헛조회하지 않도록)"""
        await self.db.ensure_nxt_daily()
        synced_on = None
        while not self.is_shutdown:
            try:
                now = get_kst_now()
                round_day = (now - timedelta(hours=8)).date()  # 20시~익일 08시를 같은 라운드로 묶음
                if not self._nxt_sync_allowed(now) or synced_on == round_day:
                    await asyncio.sleep(300.0)
                    continue
                universe = await self.db.get_stock_universe()
                if not universe:
                    await asyncio.sleep(600.0)
                    continue
                known = await self.db.get_nxt_codes()
                full = not known or round_day.weekday() == 5
                targets = universe if full else [c for c in universe if c in known]
                # 라운드 날짜가 거래일이면 그날 NXT 분봉(장전 + 애프터)도 함께 저장 (N3 전진 검증·확장용)
                minute_day = None if self.is_korean_market_holiday(now - timedelta(hours=8)) else round_day.strftime('%Y%m%d')
                rows, done, empty, minute_rows = 0, 0, 0, 0
                for code in targets:
                    if self.is_shutdown or not self._nxt_sync_allowed(get_kst_now()):
                        break
                    n = await collect_nxt_daily(self.client, self.db, code)
                    rows += n
                    done += 1
                    empty += (n == 0)
                    if minute_day and n > 0:
                        minute_rows += await collect_nxt_minute_day(self.client, self.db, code, minute_day)
                finished = done == len(targets)
                if finished:
                    synced_on = round_day
                await self.db.log_message("SYSTEM", f"NXT 일봉 동기화 {'완료' if finished else '중단(시간 종료)'}"
                                                    f"({'전체' if full else 'NXT 종목'}): {done:,}/{len(targets):,}종목, "
                                                    f"{rows:,}행, 빈 응답 {empty:,}, {minute_day or '휴장일'} 분봉 {minute_rows:,}행")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [NXT 일봉 동기화 오류] {e}")
                await self.db.log_message("ERROR", f"NXT 일봉 동기화 오류: {e}")
                await asyncio.sleep(600.0)

    async def _minute_refetch_worker(self):
        """수정주가 재수집: 기업 이벤트로 과거 가격이 바뀐 종목의 분봉(KRX·NXT)과 600일 넘는 일봉을 오늘 기준일로 다시 받는다.
        대기열은 최초 1회 전 종목 표본 점검 + 매일 일봉 수집 때 과거 종가 변경 감지(collect_history._detect_adjustment)로 채움"""
        await self.db.ensure_minute_refetch()
        failures: Dict[str, int] = {}  # 대기열 키별 연속 실패 (3회면 포기 표시, 다음 이벤트 감지 때 다시 등록됨)
        while not self.is_shutdown:
            try:
                if not self._backfill_allowed(get_kst_now()):
                    await asyncio.sleep(300.0)
                    continue
                queue = await self.db.get_minute_refetch()
                nxt_codes = await self.db.get_nxt_codes()
                if "__seeded__" not in queue:
                    universe = await self.db.get_stock_universe()
                    if not universe:
                        await asyncio.sleep(600.0)
                        continue
                    found, finished = [], True
                    for code in universe:
                        if self.is_shutdown or not self._backfill_allowed(get_kst_now()):
                            finished = False  # 허용 시간이 끝나면 다음 시간대에 처음부터 다시 점검
                            break
                        d = await minute_daily_mismatch(self.db, code, self.minute_backfill_days)
                        if d:
                            found.append((code, f"최초 점검: {d} KRX 분봉이 일봉 고가~저가 밖"))
                        if code in nxt_codes:
                            dn = await minute_daily_mismatch(self.db, code, self.minute_backfill_days, nx=True)
                            if dn:
                                found.append((f"{code}_NX", f"최초 점검: {dn} NXT 분봉이 일봉 고가~저가 밖"))
                    if not finished:
                        continue
                    await self.db.queue_minute_refetch(found + [("__seeded__", f"최초 점검 {len(universe):,}종목")])
                    await self.db.mark_minute_refetch_done("__seeded__", len(found))
                    await self.db.log_message("SYSTEM", f"수정주가 최초 점검 완료: {len(universe):,}종목 중 재수집 대상 "
                                                        f"{len(found):,}건(KRX·NXT 합)")
                    continue
                pending = sorted(c for c, v in queue.items() if c != "__seeded__" and not v.get("done_at"))
                if not pending:
                    await asyncio.sleep(1800.0)
                    continue
                done, rows, errors = 0, 0, 0
                for key in pending:
                    if self.is_shutdown or not self._backfill_allowed(get_kst_now()):
                        break
                    nx = key.endswith("_NX")
                    code = key[:-3] if nx else key
                    n = await refetch_minute_adjusted(self.client, self.db, code, self.minute_backfill_days, nx=nx)
                    if n < 0:
                        errors += 1
                        failures[key] = failures.get(key, 0) + 1
                        if failures[key] >= 3:
                            await self.db.mark_minute_refetch_done(key, -1)  # -1 = 3회 실패로 포기
                            await self.db.log_message("WARNING", f"수정주가 재수집 3회 실패로 포기: {key}")
                        await asyncio.sleep(5.0)
                        continue
                    if not nx:
                        await collect_daily(self.client, self.db, code, years=3)  # 첫 페이지(약 600일)보다 오래된 일봉도 수정주가로
                        nx_state = queue.get(f"{code}_NX")
                        if code in nxt_codes and not (nx_state and not nx_state.get("done_at")):  # 대기 중이 아니면 NXT 분봉도
                            await self.db.queue_minute_refetch([(f"{code}_NX", f"{code} 재수집에 따라 NXT 분봉도")])
                    await self.db.mark_minute_refetch_done(key, n)
                    failures.pop(key, None)
                    done, rows = done + 1, rows + n
                await self.db.log_message("SYSTEM", f"수정주가 분봉 재수집 라운드: {done:,}건 완료, {rows:,}행 덮어씀, "
                                                    f"오류 {errors}, 남은 {len(pending) - done:,}건")
                if errors and not done:
                    await asyncio.sleep(600.0)  # 전부 실패한 라운드는 쉬었다가 (호출 한도·장애 대비)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [수정주가 재수집 오류] {e}")
                await self.db.log_message("ERROR", f"수정주가 분봉 재수집 오류: {e}")
                await asyncio.sleep(60.0)

    async def _nxt_minute_backfill_worker(self):
        """NXT 분봉 과거 백필: nxt_daily에 있는 종목만, 20시~08시·휴장일에 종목별 20페이지씩 돌아가며
        (진행표 minute_backfill 키로 재시작 후 이어받기). 애프터 봉('코드_NX')을 끝낸 뒤 장전 봉('코드_NXP', 10/22 추가). 연구용"""
        await self.db.ensure_minute_backfill()
        await self.db.ensure_nxt_minute()
        phases = {"_NX": NXT_AFTER_SESSION, "_NXP": NXT_PRE_SESSION}
        announced_done = False
        while not self.is_shutdown:
            try:
                if not self._nxt_sync_allowed(get_kst_now()):
                    await asyncio.sleep(300.0)
                    continue
                codes = sorted(await self.db.get_nxt_codes())
                if not codes:
                    await asyncio.sleep(600.0)
                    continue
                states = await self.db.get_minute_backfill()
                pending = [(c, s) for s in phases for c in codes if not int((states.get(c + s) or {}).get("done", 0))]
                if not pending:
                    if not announced_done:
                        await self.db.log_message("SYSTEM", f"NXT 분봉 과거 백필 완료(애프터·장전): {len(codes):,}종목")
                        announced_done = True
                    await asyncio.sleep(3600.0)
                    continue
                announced_done = False
                steps, rows, errors = 0, 0, 0
                for code, suffix in pending:
                    if self.is_shutdown or not self._nxt_sync_allowed(get_kst_now()):
                        break
                    key = code + suffix
                    before = states.get(key)
                    after = await backfill_minute_step(self.client, self.db, code, before, self.minute_backfill_days, nx=True,
                                                       session=phases[suffix], key=key)
                    steps += 1
                    rows += after["rows_saved"] - int((before or {}).get("rows_saved", 0))
                    if not after["done"] and after["oldest_date"] == (before or {}).get("oldest_date"):
                        errors += 1
                        await asyncio.sleep(5.0)  # 오류·무진전: 잠시 쉬고 다음 종목
                    states[key] = after
                left = sum(1 for s in phases for c in codes if not int((states.get(c + s) or {}).get("done", 0)))
                await self.db.log_message("SYSTEM", f"NXT 분봉 백필 라운드: {steps:,}종목 처리, 신규 {rows:,}행, "
                                                    f"오류·무진전 {errors}, 남은 종목 {left:,}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [NXT 분봉 백필 오류] {e}")
                await self.db.log_message("ERROR", f"NXT 분봉 백필 오류: {e}")
                await asyncio.sleep(60.0)

    def _dart_live_allowed(self, now: datetime) -> bool:
        """공시 장중 폴링 시간: 거래일 06~21시"""
        return not self.is_korean_market_holiday(now) and 6 <= now.hour < 21

    async def _dart_worker(self):
        """DART 공시 수집 (공시 이벤트 연구용, 매매에는 쓰지 않음, 10/22 추가)
        - 거래일 06~21시: DART_POLL_SEC(기본 60초)마다 오늘 공시를 최신순으로 읽어 새 공시만 저장, 처음 본 시각 = first_seen_at
        - 그 밖(21시~06시·휴장일): 어제부터 과거 DART_BACKFILL_DAYS(기본 1095일)를 하루씩 백필 (시각 NULL, 진행표 dart_days)"""
        key = os.getenv("DART_API_KEY", "")
        if not key:
            await self.db.log_message("WARNING", "DART_API_KEY 없음 → 공시 수집 안 함 (키를 넣고 재시작하면 시작)")
            return
        await self.db.ensure_dart()
        dart = DartClient(key, int(os.getenv("DART_DAILY_LIMIT", "18000")))
        poll_sec = float(os.getenv("DART_POLL_SEC", "60"))
        backfill_days = int(os.getenv("DART_BACKFILL_DAYS", "1095"))
        known, known_day, timed_day, announced_done = set(), None, None, False
        try:
            while not self.is_shutdown:
                try:
                    now = get_kst_now()
                    today = now.strftime('%Y%m%d')
                    if dart.budget_left(today) <= 0:
                        await asyncio.sleep(600.0)
                        continue
                    if self._dart_live_allowed(now):
                        if known_day != today:
                            known, known_day = await self.db.get_dart_rcept_nos(today), today
                        # 그날 첫 조회는 그 전에 올라온 공시가 섞여 시각을 모르므로 NULL, 다음 조회부터 처음 본 시각
                        seen_at = now.replace(tzinfo=None, microsecond=0) if timed_day == today else None
                        rows = await fetch_day(dart, today, today, known=known, first_seen_at=seen_at)
                        if rows is not None:
                            new = [r for r in rows if r[0] not in known]  # 이미 본 공시는 시각을 다시 쓰지 않음
                            if await self.db.upsert_dart_rows(new):
                                known.update(r[0] for r in new)
                                timed_day = today
                                if new:
                                    print(f"📰 [DART] 새 공시 {len(new)}건 (오늘 누적 {len(known):,}건)")
                        await asyncio.sleep(poll_sec)
                        continue
                    if not self._nxt_sync_allowed(now):
                        await asyncio.sleep(300.0)
                        continue
                    done_days = await self.db.get_dart_days()
                    pending = [d for d in ((now - timedelta(days=i)).strftime('%Y%m%d') for i in range(1, backfill_days + 1))
                               if d not in done_days]
                    if not pending:
                        if not announced_done:
                            await self.db.log_message("SYSTEM", f"DART 공시 과거 백필 완료: {backfill_days:,}일")
                            announced_done = True
                        await asyncio.sleep(3600.0)
                        continue
                    announced_done = False
                    days, saved, failed = 0, 0, False
                    for d in pending:
                        t = get_kst_now()
                        if self.is_shutdown or self._dart_live_allowed(t) or not self._nxt_sync_allowed(t):
                            break
                        rows = await fetch_day(dart, d, t.strftime('%Y%m%d'))
                        if rows is None or not await self.db.upsert_dart_rows(rows):
                            failed = True  # 오류·한도: 그날은 완료 처리하지 않고 다음 라운드에 다시
                            break
                        await self.db.mark_dart_day(d, len(rows))
                        days, saved = days + 1, saved + len(rows)
                    await self.db.log_message("SYSTEM", f"DART 공시 백필 라운드: {days:,}일, {saved:,}건, "
                                                        f"{'오류로 중단, ' if failed else ''}남은 {len(pending) - days:,}일, 오늘 호출 {dart.calls:,}회")
                    if failed:
                        await asyncio.sleep(600.0)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"❌ [DART 공시 수집 오류] {e}")
                    await self.db.log_message("ERROR", f"DART 공시 수집 오류: {e}")
                    await asyncio.sleep(60.0)
        finally:
            await dart.close()
