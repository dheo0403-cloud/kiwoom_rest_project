"""
Gate Info:
- Importers/callers: kiwoom_rest_project/main_rest_async.py, kiwoom_rest_project/api_server.py
- Affected API: MariaDB Async Connection Pool
- Data schemas: logs, orders, watchlist, portfolio, manual_orders
- User's verbatim instruction: "파일 전체를 읽지 말고 최근 에러 로그 50줄만 읽으면서 계속 진행해줘"
"""
import aiomysql
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=False)

class DatabaseManager:
    def __init__(self):
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = int(os.getenv("DB_PORT", "3306"))
        self.user = os.getenv("DB_USER", "azure")
        self.password = os.getenv("DB_PASSWORD", "")
        self.db_name = os.getenv("DB_NAME", "kiwoom_quant_db")
        self.pool = None

    async def init_pool(self):
        """aiomysql 커넥션 풀 초기화 (테이블 생성 로직 제거)"""
        try:
            self.pool = await aiomysql.create_pool(
                host=self.host, port=self.port,
                user=self.user, password=self.password,
                db=self.db_name, minsize=1, maxsize=10,
                cursorclass=aiomysql.DictCursor
            )
            print(f"✅ DB Pool connected to '{self.db_name}'.")
        except Exception as e:
            print(f"❌ Pool 생성 오류: {e}")
            return

    async def close_pool(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def log_message(self, level, message):
        if not self.pool: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('INSERT INTO logs (level, message) VALUES (%s, %s)', (level, message))
                await conn.commit()
        except Exception as e:
            print(f"DB Log Error: {e}")

    async def log_order(self, code, name, side, qty, price):
        if not self.pool: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('''
                        INSERT INTO order_history (code, name, side, qty, price)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (code, name, side, qty, price))
                await conn.commit()
            await self.log_message('TRADE', f"{side} {name}({code}): {price}원 {qty}주")
        except Exception as e:
            print(f"DB Order Error: {e}")

    async def update_balance(self, total_asset, deposit, profit_loss, yield_rate):
        if not self.pool: return
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('''
                        INSERT INTO balance (date, total_asset, deposit, profit_loss, yield)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                        total_asset=VALUES(total_asset), deposit=VALUES(deposit), 
                        profit_loss=VALUES(profit_loss), yield=VALUES(yield)
                    ''', (today, total_asset, deposit, profit_loss, yield_rate))
                await conn.commit()
        except Exception as e:
            print(f"DB Balance Error: {e}")

    async def upsert_daily_ohlcv(self, df):
        if not self.pool or df.empty: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = '''
                        INSERT INTO daily_ohlcv (code, date, open, high, low, close, volume, value)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high), low=VALUES(low), 
                        close=VALUES(close), volume=VALUES(volume), value=VALUES(value)
                    '''
                    data = [
                        (row.code, row.date, row.open, row.high, row.low, row.close, row.volume, row.value)
                        for row in df.itertuples(index=False)
                    ]
                    await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"DB Daily OHLCV Error: {e}")

    async def batch_upsert_minute_candles(self, candle_list):
        """튜플/딕셔너리 리스트 형태의 분봉 데이터 비동기 벌크 저장"""
        if not self.pool or not candle_list: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = '''
                        INSERT INTO minute_ohlcv (code, datetime, open, high, low, close, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high), low=VALUES(low),
                        close=VALUES(close), volume=VALUES(volume)
                    '''
                    data = []
                    for c in candle_list:
                        if isinstance(c, (list, tuple)):
                            data.append(c)
                        elif isinstance(c, dict):
                            data.append((
                                c.get('code'), c.get('datetime'),
                                c.get('open'), c.get('high'), c.get('low'), c.get('close'),
                                c.get('volume', 0)
                            ))
                    if data:
                        await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"DB Batch Minute OHLCV Error: {e}")

    async def upsert_minute_ohlcv(self, df):
        if not self.pool or df.empty: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = '''
                        INSERT INTO minute_ohlcv (code, datetime, open, high, low, close, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high), low=VALUES(low),
                        close=VALUES(close), volume=VALUES(volume)
                    '''
                    data = [
                        (row.code, row.datetime, row.open, row.high, row.low, row.close, row.volume)
                        for row in df.itertuples(index=False)
                    ]
                    await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"DB Minute OHLCV Error: {e}")

    async def upsert_technical_indicators(self, df):
        """기술적 지표 (RSI, MACD, BB, ATR, VWAP, ADX 등) 캐싱"""
        if not self.pool or df.empty: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = '''
                        INSERT INTO technical_indicators
                        (code, date, rsi14, macd, macd_signal, macd_hist, bb_upper, bb_middle, bb_lower, atr14, vwap, adx14)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        rsi14=VALUES(rsi14), macd=VALUES(macd), macd_signal=VALUES(macd_signal),
                        macd_hist=VALUES(macd_hist), bb_upper=VALUES(bb_upper), bb_middle=VALUES(bb_middle),
                        bb_lower=VALUES(bb_lower), atr14=VALUES(atr14), vwap=VALUES(vwap), adx14=VALUES(adx14)
                    '''
                    data = []
                    for row in df.itertuples(index=False):
                        r_dict = row._asdict() if hasattr(row, '_asdict') else dict(zip(df.columns, row))
                        data.append((
                            r_dict.get('code', ''),
                            str(r_dict.get('date', '')),
                            float(r_dict.get('rsi14', 50.0)),
                            float(r_dict.get('macd', 0.0)),
                            float(r_dict.get('macd_signal', 0.0)),
                            float(r_dict.get('macd_hist', 0.0)),
                            float(r_dict.get('bb_upper', 0.0)),
                            float(r_dict.get('bb_middle', 0.0)),
                            float(r_dict.get('bb_lower', 0.0)),
                            float(r_dict.get('atr14', 0.0)),
                            float(r_dict.get('vwap', 0.0)),
                            float(r_dict.get('adx14', 0.0))
                        ))
                    await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"DB Technical Indicators Error: {e}")

    async def get_latest_technical_indicators(self, code):
        """특정 종목의 최신 산출 기술적 지표 1건 조회"""
        if not self.pool: return None
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    query = "SELECT * FROM technical_indicators WHERE code = %s ORDER BY date DESC LIMIT 1"
                    await cursor.execute(query, (code,))
                    result = await cursor.fetchone()
                    return dict(result) if result else None
        except Exception as e:
            print(f"DB Get Latest Indicators Error: {e}")
            return None

    async def upsert_fundamental_metrics(self, data_list):
        """펀더멘털 재무지표 및 피오트로스키 F-Score 캐싱"""
        if not self.pool or not data_list: return
        try:
            if isinstance(data_list, dict):
                data_list = [data_list]
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = '''
                        INSERT INTO fundamental_metrics
                        (code, name, per, pbr, psr, roe, roa, debt_to_equity, piotroski_f_score, graham_number, margin_of_safety_pct, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON DUPLICATE KEY UPDATE
                        name=VALUES(name), per=VALUES(per), pbr=VALUES(pbr), psr=VALUES(psr),
                        roe=VALUES(roe), roa=VALUES(roa), debt_to_equity=VALUES(debt_to_equity),
                        piotroski_f_score=VALUES(piotroski_f_score), graham_number=VALUES(graham_number),
                        margin_of_safety_pct=VALUES(margin_of_safety_pct), updated_at=NOW()
                    '''
                    records = []
                    for item in data_list:
                        records.append((
                            item.get('code', ''),
                            item.get('name', ''),
                            float(item.get('per', 0.0)),
                            float(item.get('pbr', 0.0)),
                            float(item.get('psr', 0.0)),
                            float(item.get('roe', 0.0)),
                            float(item.get('roa', 0.0)),
                            float(item.get('debt_to_equity', 0.0)),
                            int(item.get('piotroski_f_score', item.get('f_score', 0))),
                            float(item.get('graham_number', 0.0)),
                            float(item.get('margin_of_safety_pct', 0.0))
                        ))
                    await cursor.executemany(sql, records)
                await conn.commit()
        except Exception as e:
            print(f"DB Fundamental Metrics Error: {e}")

    async def get_screened_universe(self, min_f_score=6, max_debt=150.0, min_roe=8.0):
        """재무 건전성 및 퀄리티 통과 유니버스 조회 (F-Score >= 6, 부채비율 <= 150%, ROE >= 8%)"""
        if not self.pool: return []
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    query = '''
                        SELECT * FROM fundamental_metrics
                        WHERE piotroski_f_score >= %s AND debt_to_equity <= %s AND roe >= %s
                        ORDER BY piotroski_f_score DESC, roe DESC
                    '''
                    await cursor.execute(query, (min_f_score, max_debt, min_roe))
                    result = await cursor.fetchall()
                    return [dict(r) for r in result] if result else []
        except Exception as e:
            print(f"DB Screened Universe Error: {e}")
            return []

    async def get_dataframe(self, query):
        """DB에서 쿼리 결과를 비동기로 가져와서 DataFrame으로 반환"""
        import pandas as pd
        if not self.pool: return pd.DataFrame()
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query)
                    result = await cursor.fetchall()
                    if not result:
                        return pd.DataFrame()
                    return pd.DataFrame(result)
        except Exception as e:
            print(f"DB Query Error: {e}")
            return pd.DataFrame()



    async def save_portfolio(self, positions_dict, current_prices=None):
        """현재 봇이 관리 중인 포트폴리오를 DB에 저장 (대시보드 표출용)"""
        if not self.pool: return
        try:
            current_prices = current_prices or {}
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('DELETE FROM portfolio')
                    if positions_dict:
                        sql = '''
                            INSERT INTO portfolio (code, name, qty, buy_price, current_price) 
                            VALUES (%s, %s, %s, %s, %s)
                        '''
                        data = []
                        for code, info in positions_dict.items():
                            c_price = current_prices.get(code) or info.get('current_price', 0)
                            data.append((code, info['name'], info['qty'], info['buy_price'], c_price))
                        if data:
                            await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"Portfolio 저장 에러: {e}")

    async def save_watchlist(self, codes, name_map=None):
        """감시 종목 목록 DB 저장 (종목명/현재가 포함 초기화, str/dict 리스트 모두 지원)"""
        if not self.pool: return
        name_map = name_map or {}
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('DELETE FROM watchlist')
                    if codes:
                        sql = 'INSERT INTO watchlist (code, name, status, current_price, avg_volume, updated_at) VALUES (%s, %s, %s, %s, %s, NOW())'
                        data = []
                        for item in codes:
                            if isinstance(item, dict):
                                c_code = item.get('code') or item.get('stk_cd', '')
                                c_name = item.get('name') or item.get('stk_nm') or name_map.get(c_code, c_code)
                                c_price = item.get('current_price', 0)
                                c_vol = item.get('volume', 0)
                                if c_code:
                                    data.append((c_code, c_name, 'WATCHING', c_price, c_vol))
                            elif isinstance(item, str):
                                data.append((item, name_map.get(item, item), 'WATCHING', 0, 0))
                        if data:
                            await cursor.executemany(sql, data)
                await conn.commit()
        except Exception as e:
            print(f"Watchlist 저장 에러: {e}")

    async def update_watchlist_price(self, code, name, price, volume=0):
        """감시 종목 현재가/이름 실시간 업데이트"""
        if not self.pool: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute('''
                        UPDATE watchlist SET name=COALESCE(NULLIF(%s, ''), name), current_price=%s, avg_volume=%s, updated_at=NOW()
                        WHERE code=%s
                    ''', (name, price, volume, code))
                await conn.commit()
        except Exception as e:
            pass

    # ================= 동기 버전 (Dashboard 용) =================
    def add_manual_order_sync(self, code, side, qty):
        """(동기) 대시보드에서 수동 주문 접수"""
        import pymysql
        try:
            conn = pymysql.connect(
                host=self.host, port=self.port, user=self.user,
                password=self.password, db=self.db_name
            )
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO manual_orders (code, side, qty, status)
                    VALUES (%s, %s, %s, 'PENDING')
                ''', (code, side, qty))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"수동 주문 추가 에러: {e}")
            return False

    async def get_pending_manual_orders(self):
        """대기 중인 수동 주문 조회"""
        if not self.pool: return []
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT * FROM manual_orders WHERE status = 'PENDING'")
                    return await cursor.fetchall()
        except Exception as e:
            print(f"수동 주문 조회 에러: {e}")
            return []

    async def complete_manual_order(self, order_id, status='COMPLETED'):
        """수동 주문 상태 업데이트"""
        if not self.pool: return
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("UPDATE manual_orders SET status = %s WHERE id = %s", (status, order_id))
                await conn.commit()
        except Exception as e:
            print(f"수동 주문 업데이트 에러: {e}")

    async def update_manual_order_status(self, order_id, status='COMPLETED'):
        """수동 주문 상태 업데이트 별칭"""
        await self.complete_manual_order(order_id, status)

# AsyncDatabase 별칭 지원 (main_rest_async.py 호환)
AsyncDatabase = DatabaseManager

