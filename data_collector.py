import asyncio
import pandas as pd
from datetime import datetime, timedelta

class DataCollector:
    def __init__(self, kiwoom_api, db_manager):
        self.kiwoom = kiwoom_api
        self.db = db_manager
        self.stock_names = {}  # code -> name

    async def fetch_top_liquidity_stocks(self):
        """1차 필터: 거래대금 상위 종목 조회 및 필터링 (비동기)"""
        print("📊 거래대금 상위 종목 조회 중...")
        data = await self.kiwoom.get_top_trading_value()

        if not data:
            if getattr(self.kiwoom, 'mode', None) == 'MOCK':
                print("⚠️ [MOCK] 모의투자 환경 거래대금 상위 조회 불가. 테스트용 우량주(삼성전자 등 5종목)를 강제 세팅합니다.")
                return ["005930", "000660", "035420", "035720", "005380"]
            print("⚠️ API 응답 없음 (None)")
            return []

        # 실전/모의 환경 모두 대응: 여러 가능한 응답 키 탐색
        output = data.get('trde_prica_upper') or data.get('output') or data.get('Output') or None
        if not output:
            available_keys = list(data.keys())[:10]
            print(f"⚠️ 응답에 종목 리스트 키 없음. 응답 키 목록: {available_keys}")
            if getattr(self.kiwoom, 'mode', None) == 'MOCK':
                return ["005930", "000660", "035420", "035720", "005380"]
            return []

        if not isinstance(output, list):
            output = [output]

        print(f"  📋 API 응답 종목 수: {len(output)}개")

        candidates = []
        for item in output:
            try:
                raw_code = item.get('stk_cd') or item.get('code') or item.get('mksc_shrn_iscd')
                if not raw_code: continue

                # 종목코드 정제: _AL, _NX 등 거래소 접미사 제거 → 6자리 숫자만 추출
                code = raw_code.split('_')[0].strip()
                if len(code) != 6 or not code.isdigit():
                    continue

                # 등락률: flu_rt(실전) 또는 prdy_ctrt(모의) 키 대응
                flu_rt_raw = item.get('flu_rt') or item.get('prdy_ctrt') or '0'
                flu_rt = float(str(flu_rt_raw).replace('+', '').replace('%', ''))
                name = item.get('stk_nm') or item.get('name') or code

                # 중복 제거 (KRX/NXT 통합 조회 시 동일 종목 중복 가능)
                if code in candidates: continue

                self.stock_names[code] = name
                if flu_rt >= 3.0:
                    candidates.append(code)

                if len(candidates) >= 50:
                    break
            except Exception:
                pass

        print(f"✅ 1차 유동성 필터 통과 종목: {len(candidates)}개 (등락률 3%+)")
        return candidates

    async def _fetch_daily_single(self, code, today):
        """단일 종목 일봉 데이터 수집"""
        try:
            data = await self.kiwoom.get_daily_chart(code, today)
            if not data: return

            # 실전/모의 응답 키 대응
            output = data.get('stk_dt_pole_chart_qry') or data.get('output') or None
            if not output: return
            if not isinstance(output, list):
                output = [output]
                
            records = []
            for item in output:
                # 날짜: dt(실전) / stck_bsop_date / date
                dt = item.get('dt') or item.get('stck_bsop_date') or item.get('date')
                if not dt: continue
                
                records.append({
                    'code': code,
                    'date': dt,
                    'open': abs(int(item.get('open_pric', 0) or item.get('stck_oprc', 0) or item.get('open', 0))),
                    'high': abs(int(item.get('high_pric', 0) or item.get('stck_hgpr', 0) or item.get('high', 0))),
                    'low': abs(int(item.get('low_pric', 0) or item.get('stck_lwpr', 0) or item.get('low', 0))),
                    'close': abs(int(item.get('cur_prc', 0) or item.get('stck_clpr', 0) or item.get('close', 0))),
                    'volume': abs(int(item.get('trde_qty', 0) or item.get('acml_vol', 0) or item.get('volume', 0))),
                    'value': abs(int(item.get('trde_prica', 0) or item.get('acml_tr_pbmn', 0) or item.get('value', 0)))
                })
            
            df = pd.DataFrame(records)
            await self.db.upsert_daily_ohlcv(df)
        except Exception as e:
            print(f"❌ 일봉 수집 에러 ({code}): {e}")

    async def _get_daily_data_count(self, code):
        """특정 종목의 DB 일봉 데이터 건수 조회"""
        try:
            query = f"SELECT COUNT(*) as cnt FROM daily_ohlcv WHERE code='{code}'"
            df = await self.db.get_dataframe(query)
            if not df.empty:
                return int(df.iloc[0]['cnt'])
        except Exception:
            pass
        return 0

    async def update_daily_data(self, codes):
        """대상 종목 일봉 데이터 순차 수집 — 부족 시 과거 데이터 추가 수집"""
        today = datetime.now().strftime('%Y%m%d')
        sufficient_count = 0

        for i, code in enumerate(codes):
            # 1차: 오늘 기준 일봉 수집
            await self._fetch_daily_single(code, today)

            # DB 데이터 충분성 확인
            count = await self._get_daily_data_count(code)
            if count < 20:
                # 2차: 과거 90일 전 기준으로 추가 수집 시도
                earlier_dt = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
                await self._fetch_daily_single(code, earlier_dt)
                count = await self._get_daily_data_count(code)

            if count >= 20:
                sufficient_count += 1
            elif count > 0:
                print(f"  ⚠️ {code}: 일봉 {count}일치만 확보 (최소 20일 권장)")

            if (i + 1) % 10 == 0:
                print(f"  📦 일봉 수집 진행: {i+1}/{len(codes)}개 (충분: {sufficient_count}개)")

        print(f"  ✅ 일봉 데이터 수집 완료: {sufficient_count}/{len(codes)}개 종목 데이터 충분 (20일+)")

    async def _fetch_minute_single(self, code, today):
        """단일 종목 1분봉 데이터 수집"""
        try:
            data, _ = await self.kiwoom.get_minute_chart(code, today)
            if not data: return

            # 실전/모의 응답 키 대응
            output = data.get('stk_min_pole_chart_qry') or data.get('output') or None
            if not output: return
            if not isinstance(output, list):
                output = [output]
                
            records = []
            for item in output:
                # 시간: cntr_tm(실전) / stck_cntg_hour / datetime
                dt = item.get('cntr_tm') or item.get('stck_cntg_hour') or item.get('datetime')
                if not dt: continue
                
                records.append({
                    'code': code,
                    'datetime': today + dt if len(dt) <= 6 else dt,
                    'open': abs(int(item.get('open_pric', 0) or item.get('stck_oprc', 0) or item.get('open', 0))),
                    'high': abs(int(item.get('high_pric', 0) or item.get('stck_hgpr', 0) or item.get('high', 0))),
                    'low': abs(int(item.get('low_pric', 0) or item.get('stck_lwpr', 0) or item.get('low', 0))),
                    'close': abs(int(item.get('cur_prc', 0) or item.get('stck_prpr', 0) or item.get('close', 0))),
                    'volume': abs(int(item.get('trde_qty', 0) or item.get('cntg_vol', 0) or item.get('volume', 0)))
                })
            
            df = pd.DataFrame(records)
            await self.db.upsert_minute_ohlcv(df)
        except Exception as e:
            print(f"❌ 분봉 수집 에러 ({code}): {e}")

    async def update_minute_data(self, codes):
        """대상 종목 1분봉 데이터 순차 수집 (Rate Limit 안전)"""
        today = datetime.now().strftime('%Y%m%d')
        for i, code in enumerate(codes):
            await self._fetch_minute_single(code, today)
            if (i + 1) % 10 == 0:
                print(f"  📦 분봉 수집 진행: {i+1}/{len(codes)}개")

    async def get_calculated_indicators(self, code):
        """DB 기반 비동기 지표 계산 — 데이터가 1일 이상이면 가용 범위로 계산"""
        try:
            query = f"SELECT * FROM daily_ohlcv WHERE code='{code}' ORDER BY date DESC LIMIT 40"
            df = await self.db.get_dataframe(query)

            if getattr(self.kiwoom, 'mode', None) == 'MOCK':
                # 모의투자 환경에서는 과거 차트 데이터 부족으로 필터링이 막히는 것을 방지
                return {
                    'ma5': 50000,
                    'ma20': 40000,
                    'high10': 60000,
                    'avg_vol': 1000000,
                    'data_days': 0
                }

            if df.empty:
                return None
            
            df = df.sort_values('date').reset_index(drop=True)
            n = len(df)
            
            # 가용 데이터 범위 내에서 지표 계산
            ma5 = df['close'].tail(min(5, n)).mean()
            ma20 = df['close'].tail(min(20, n)).mean()
            high10 = df['high'].tail(min(10, n)).max()
            high3 = df['high'].tail(min(3, n)).max()   # ★ 단기 3일 고가 추가
            avg_vol = df['volume'].mean()

            return {
                'ma5': ma5,
                'ma20': ma20,
                'high10': high10,
                'high3': high3,
                'avg_vol': avg_vol,
                'data_days': n
            }
        except Exception as e:
            print(f"❌ 지표 연산 에러 ({code}): {e}")
            return None

    async def filter_2nd_breakout(self, candidates):
        final_list = []
        skipped = 0
        for code in candidates:
            ind = await self.get_calculated_indicators(code)
            if not ind:
                skipped += 1
                continue
            final_list.append(code)

        if skipped > 0:
            print(f"  ⚠️ 2차 필터에서 {skipped}개 종목 제외 (일봉 데이터 없음)")
        return final_list
