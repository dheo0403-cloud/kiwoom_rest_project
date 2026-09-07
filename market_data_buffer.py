"""
인메모리 링버퍼(Ring-Buffer) 및 실시간 시세 캐싱 계층 (Market Data Buffer)
- 종목별 최근 60개 1분봉 및 틱 데이터를 메모리에 상주시켜 DB I/O 없이 0.1ms 내에 지표 연산 처리
- 비동기 큐(asyncio.Queue) 기반 백그라운드 배치 DB 영속화 (5초 주기)
- 시스템 종료 시 잔여 데이터 무손실 Flush 지원 (Graceful Shutdown)
"""
import asyncio
import collections
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import numpy as np


class CircularCandleBuffer:
    """
    단일 종목 전용 인메모리 링버퍼 (Ring-Buffer)
    - 최근 maxlen개 (기본 60개) 1분봉 데이터 보관
    - 틱 데이터로부터 1분봉 실시간 롤링 조립
    """
    def __init__(self, code: str, maxlen: int = 60):
        self.code = code
        self.maxlen = maxlen
        # 각 항목은 {'datetime': str, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}
        self.candles: collections.deque = collections.deque(maxlen=maxlen)
        self.current_candle: Optional[Dict[str, Any]] = None
        self.latest_tick_price: float = 0.0
        self.latest_tick_volume: float = 0.0
        self._lock = asyncio.Lock()

    def update_tick(self, price: float, volume: float, dt_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        실시간 틱 유입 시 현재 분봉 갱신 또는 분봉 완성 시 링버퍼에 push
        dt_str: 'YYYY-MM-DD HH:MM:SS' 또는 None(현재시간)
        반환: 완성되어 링버퍼에 추가된 직전 분봉 딕셔너리 (새 분봉 시작 시) 또는 None
        """
        now = datetime.now()
        dt_minute_str = dt_str or now.strftime('%Y-%m-%d %H:%M:00')
        self.latest_tick_price = price
        self.latest_tick_volume = volume

        completed_candle = None

        if self.current_candle is None:
            # 최초 분봉 생성
            self.current_candle = {
                'code': self.code,
                'datetime': dt_minute_str,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume
            }
        elif self.current_candle['datetime'] == dt_minute_str:
            # 동일 분봉 내 업데이트
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] = max(self.current_candle['volume'], volume)
        else:
            # 새로운 분봉 시작 -> 이전 분봉 링버퍼에 커밋
            completed_candle = dict(self.current_candle)
            self.candles.append(completed_candle)
            self.current_candle = {
                'code': self.code,
                'datetime': dt_minute_str,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume
            }

        return completed_candle

    def append_candle(self, open_p: float, high_p: float, low_p: float, close_p: float,
                      volume: float, dt_str: str):
        """과거 분봉 또는 완성된 분봉을 직접 버퍼에 삽입"""
        candle = {
            'code': self.code,
            'datetime': dt_str,
            'open': float(open_p),
            'high': float(high_p),
            'low': float(low_p),
            'close': float(close_p),
            'volume': float(volume)
        }
        self.candles.append(candle)
        self.latest_tick_price = float(close_p)
        self.latest_tick_volume = float(volume)

    def load_initial_candles(self, candle_list: List[Dict[str, Any]]):
        """DB 또는 REST API로부터 수신한 초기 분봉 리스트 적재 (시간 오름차순 정렬)"""
        self.candles.clear()
        for c in candle_list:
            self.candles.append({
                'code': self.code,
                'datetime': str(c.get('datetime', '')),
                'open': float(c.get('open', 0)),
                'high': float(c.get('high', 0)),
                'low': float(c.get('low', 0)),
                'close': float(c.get('close', 0)),
                'volume': float(c.get('volume', 0))
            })
        if self.candles:
            self.latest_tick_price = self.candles[-1]['close']
            self.latest_tick_volume = self.candles[-1]['volume']

    def get_dataframe(self, limit: Optional[int] = None) -> pd.DataFrame:
        """
        메모리 상에서 즉시 Pandas DataFrame으로 변환 (SQL 쿼리 불필요, 0.1ms)
        진행 중인 current_candle도 포함하여 최신 실시간 반영
        """
        data = list(self.candles)
        if self.current_candle:
            data.append(self.current_candle)

        if not data:
            return pd.DataFrame(columns=['code', 'datetime', 'open', 'high', 'low', 'close', 'volume'])

        df = pd.DataFrame(data)
        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    def get_latest_price(self) -> float:
        """최신 가격 반환"""
        if self.current_candle:
            return self.current_candle['close']
        if self.candles:
            return self.candles[-1]['close']
        return self.latest_tick_price

    def get_highest_high(self, window: int = 10) -> float:
        """최근 N개 분봉 중 최고가 산출"""
        data = list(self.candles)
        if self.current_candle:
            data.append(self.current_candle)
        if not data:
            return self.latest_tick_price
        target = data[-window:]
        return max(c['high'] for c in target)

    def get_lowest_low(self, window: int = 10) -> float:
        """최근 N개 분봉 중 최저가 산출"""
        data = list(self.candles)
        if self.current_candle:
            data.append(self.current_candle)
        if not data:
            return self.latest_tick_price
        target = data[-window:]
        return min(c['low'] for c in target)


class MarketDataBuffer:
    """
    전체 감시 종목의 링버퍼 통합 관리 및 비동기 배치 DB 영속화 관리자
    """
    def __init__(self, db_manager=None, flush_interval: float = 5.0, buffer_maxlen: int = 60):
        self.db = db_manager
        self.flush_interval = flush_interval
        self.buffer_maxlen = buffer_maxlen
        self.buffers: Dict[str, CircularCandleBuffer] = {}
        self.db_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False

    def get_or_create(self, code: str) -> CircularCandleBuffer:
        """특정 종목의 링버퍼 조회 또는 신규 생성"""
        if code not in self.buffers:
            self.buffers[code] = CircularCandleBuffer(code=code, maxlen=self.buffer_maxlen)
        return self.buffers[code]

    def update_tick(self, code: str, price: float, volume: float, dt_str: Optional[str] = None):
        """실시간 틱 수신 시 해당 종목 버퍼 갱신 및 완성된 분봉 DB 저장 큐에 인큐"""
        buf = self.get_or_create(code)
        completed_candle = buf.update_tick(price, volume, dt_str)
        if completed_candle and self._is_running:
            self.db_queue.put_nowait(completed_candle)

    def load_initial_candles(self, code: str, candle_list: List[Dict[str, Any]]):
        """특정 종목의 초기 분봉 리스트 적재"""
        buf = self.get_or_create(code)
        buf.load_initial_candles(candle_list)

    def get_dataframe(self, code: str, limit: Optional[int] = 10) -> pd.DataFrame:
        """특정 종목의 최근 분봉 DataFrame 메모리 조회"""
        buf = self.get_or_create(code)
        return buf.get_dataframe(limit=limit)

    async def start(self):
        """비동기 백그라운드 DB 배치 저장 워커 시작"""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._db_flush_worker())
        print("⚡ [MarketDataBuffer] 인메모리 링버퍼 및 비동기 DB 배치 워커 가동 완료.")

    async def stop(self):
        """백그라운드 워커 정지 및 잔여 데이터 완전 Flush (Graceful Shutdown)"""
        if not self._is_running:
            return
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        # 잔여 큐 및 현재 진행 중인 캔들 전량 Flush
        await self.flush_all()
        print("🛑 [MarketDataBuffer] 링버퍼 잔여 데이터 DB Flush 및 워커 정상 종료.")

    async def flush_all(self):
        """현재 큐 및 모든 종목의 마지막 활성 캔들 DB 즉시 저장"""
        if not self.db:
            return

        items_to_save = []
        # 1. 큐에 쌓인 완성 분봉 추출
        while not self.db_queue.empty():
            try:
                items_to_save.append(self.db_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # 2. 각 종목의 현재 미완성 캔들도 스냅샷 저장
        for code, buf in self.buffers.items():
            if buf.current_candle:
                items_to_save.append(dict(buf.current_candle))

        if items_to_save:
            await self.db.batch_upsert_minute_candles(items_to_save)
            print(f"💾 [MarketDataBuffer] 총 {len(items_to_save)}건의 분봉 데이터 DB 영속화 완료.")

    async def _db_flush_worker(self):
        """5초 주기 배치 저장 루프"""
        while self._is_running:
            try:
                await asyncio.sleep(self.flush_interval)
                batch = []
                # 최대 100개까지 한 번에 배치 수집
                while not self.db_queue.empty() and len(batch) < 100:
                    try:
                        batch.append(self.db_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                if batch and self.db:
                    await self.db.batch_upsert_minute_candles(batch)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [MarketDataBuffer] DB 배치 저장 워커 오류: {e}")
