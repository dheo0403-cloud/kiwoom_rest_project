"""
Alpha Vantage 호환 기술적 지표 산출 엔진 (Technical Indicators Engine)
- 외부 유료 API 결제 없이 순수 파이썬/NumPy/Pandas로 100% 자체 산출
- 지원 지표: Wilder's RSI(14), MACD(12,26,9), 볼린저 밴드(20,2), ATR(14), VWAP, ADX(14), 이동평균선(MA)
"""
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


class TechnicalIndicators:
    """Alpha Vantage 표준 수식 기반 고성능 기술적 지표 벡터 연산 클래스"""

    @staticmethod
    def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """컬럼명을 표준 소문자(open, high, low, close, volume)로 정규화"""
        df_copy = df.copy()
        col_map = {}
        for col in df_copy.columns:
            c_lower = str(col).lower()
            if c_lower in ['open', 'opn_prc', 'stck_oprc', '시가']:
                col_map[col] = 'open'
            elif c_lower in ['high', 'hg_prc', 'stck_hgpr', '고가']:
                col_map[col] = 'high'
            elif c_lower in ['low', 'lw_prc', 'stck_lwpr', '저가']:
                col_map[col] = 'low'
            elif c_lower in ['close', 'cl_prc', 'stck_clpr', '현재가', '종가']:
                col_map[col] = 'close'
            elif c_lower in ['volume', 'vol', 'acml_vol', '거래량']:
                col_map[col] = 'volume'

        df_copy.rename(columns=col_map, inplace=True)
        # 필수 컬럼 검증 및 numeric 변환
        for req in ['open', 'high', 'low', 'close']:
            if req in df_copy.columns:
                df_copy[req] = pd.to_numeric(df_copy[req], errors='coerce').fillna(0.0)
        if 'volume' in df_copy.columns:
            df_copy['volume'] = pd.to_numeric(df_copy['volume'], errors='coerce').fillna(0.0)

        return df_copy

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        Wilder's Smoothing RSI (Relative Strength Index)
        - Alpha Vantage 및 Welles Wilder 표준 지수평활(alpha = 1/period) 적용
        """
        if len(series) < 2:
            return pd.Series(50.0, index=series.index)

        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        # Wilder's Smoothing: ewm(alpha = 1/period, adjust=False)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        # 0 나누기 방지
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # avg_loss가 0이고 gain이 있는 경우 RSI는 100
        rsi = rsi.fillna(100.0)
        # 초기 NaN 구간(데이터 부족) 기본값 채움
        rsi.iloc[:period] = rsi.iloc[:period].fillna(50.0)
        return rsi

    @staticmethod
    def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """
        MACD (Moving Average Convergence Divergence)
        - fast EMA(12), slow EMA(26), signal EMA(9), histogram
        """
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False).mean()
        macd_hist = macd - macd_signal

        return pd.DataFrame({
            'macd': macd,
            'macd_signal': macd_signal,
            'macd_hist': macd_hist
        }, index=series.index)

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, period: int = 20, nbdev: float = 2.0) -> pd.DataFrame:
        """
        Bollinger Bands (볼린저 밴드)
        - 상단선, 중심선(SMA 20), 하단선, Bandwidth, %B 산출
        """
        middle = series.rolling(window=period, min_periods=1).mean()
        std = series.rolling(window=period, min_periods=1).std().fillna(0.0)
        upper = middle + (nbdev * std)
        lower = middle - (nbdev * std)

        bandwidth = ((upper - lower) / middle.replace(0.0, np.nan)) * 100.0
        bandwidth = bandwidth.fillna(0.0)

        band_diff = (upper - lower).replace(0.0, np.nan)
        percent_b = (series - lower) / band_diff
        percent_b = percent_b.fillna(0.5)

        return pd.DataFrame({
            'bb_upper': upper,
            'bb_middle': middle,
            'bb_lower': lower,
            'bb_bandwidth': bandwidth,
            'bb_percent_b': percent_b
        }, index=series.index)

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        ATR (Average True Range)
        - Wilder's Smoothing ATR 산출 (Gap Risk 반영한 변동성 측정)
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        high = df_std['high']
        low = df_std['low']
        close = df_std['close']
        close_prev = close.shift(1).fillna(close)

        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        atr = atr.fillna(tr.rolling(window=period, min_periods=1).mean())
        return atr

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> pd.Series:
        """
        VWAP (Volume Weighted Average Price - 거래량 가중 평균가)
        - Typical Price = (High + Low + Close) / 3
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        typical_price = (df_std['high'] + df_std['low'] + df_std['close']) / 3.0
        volume = df_std['volume']

        cum_tp_vol = (typical_price * volume).cumsum()
        cum_vol = volume.cumsum()

        vwap = cum_tp_vol / cum_vol.replace(0.0, np.nan)
        return vwap.fillna(typical_price)

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        ADX (Average Directional Movement Index - 추세 강도 지표)
        - +DI, -DI, ADX 산출 (Wilder's Smoothing)
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        high = df_std['high']
        low = df_std['low']

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        plus_dm_series = pd.Series(plus_dm, index=df_std.index)
        minus_dm_series = pd.Series(minus_dm, index=df_std.index)

        plus_dm_smoothed = plus_dm_series.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        minus_dm_smoothed = minus_dm_series.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        atr = TechnicalIndicators.calculate_atr(df_std, period=period).replace(0.0, np.nan)

        plus_di = (plus_dm_smoothed / atr) * 100.0
        minus_di = (minus_dm_smoothed / atr) * 100.0

        plus_di = plus_di.fillna(0.0)
        minus_di = minus_di.fillna(0.0)

        di_sum = (plus_di + minus_di).replace(0.0, np.nan)
        dx = (abs(plus_di - minus_di) / di_sum) * 100.0
        dx = dx.fillna(0.0)

        adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        adx = adx.fillna(0.0)

        return pd.DataFrame({
            'plus_di': plus_di,
            'minus_di': minus_di,
            'dx': dx,
            'adx': adx
        }, index=df_std.index)

    @classmethod
    def compute_all_indicators(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        모든 기술적 지표를 일괄 산출하여 원본 DataFrame에 병합
        """
        if df.empty:
            return df

        df_res = cls._standardize_columns(df)
        close = df_res['close']

        # 1. 이동평균선 (MA5, MA20, MA60, MA120)
        df_res['ma5'] = close.rolling(window=5, min_periods=1).mean()
        df_res['ma20'] = close.rolling(window=20, min_periods=1).mean()
        df_res['ma60'] = close.rolling(window=60, min_periods=1).mean()
        df_res['ma120'] = close.rolling(window=120, min_periods=1).mean()

        # 2. 거래량 이동평균
        if 'volume' in df_res.columns:
            df_res['vol_ma5'] = df_res['volume'].rolling(window=5, min_periods=1).mean()
            df_res['vol_ma20'] = df_res['volume'].rolling(window=20, min_periods=1).mean()

        # 3. RSI(14)
        df_res['rsi14'] = cls.calculate_rsi(close, period=14)

        # 4. MACD(12, 26, 9)
        macd_df = cls.calculate_macd(close, fast=12, slow=26, signal=9)
        df_res['macd'] = macd_df['macd']
        df_res['macd_signal'] = macd_df['macd_signal']
        df_res['macd_hist'] = macd_df['macd_hist']

        # 5. Bollinger Bands(20, 2)
        bb_df = cls.calculate_bollinger_bands(close, period=20, nbdev=2.0)
        df_res['bb_upper'] = bb_df['bb_upper']
        df_res['bb_middle'] = bb_df['bb_middle']
        df_res['bb_lower'] = bb_df['bb_lower']
        df_res['bb_bandwidth'] = bb_df['bb_bandwidth']
        df_res['bb_percent_b'] = bb_df['bb_percent_b']

        # 6. ATR(14)
        df_res['atr14'] = cls.calculate_atr(df_res, period=14)

        # 7. VWAP
        df_res['vwap'] = cls.calculate_vwap(df_res)

        # 8. ADX(14)
        adx_df = cls.calculate_adx(df_res, period=14)
        df_res['plus_di'] = adx_df['plus_di']
        df_res['minus_di'] = adx_df['minus_di']
        df_res['adx14'] = adx_df['adx']

        return df_res

    @classmethod
    def get_latest_indicators(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """최신 1건의 지표를 딕셔너리로 추출 (실시간 전략 평가용)"""
        if df.empty:
            return {}

        computed_df = cls.compute_all_indicators(df)
        latest = computed_df.iloc[-1]
        prev = computed_df.iloc[-2] if len(computed_df) > 1 else latest

        return {
            'close': float(latest.get('close', 0)),
            'ma5': float(latest.get('ma5', 0)),
            'ma20': float(latest.get('ma20', 0)),
            'ma60': float(latest.get('ma60', 0)),
            'rsi14': float(latest.get('rsi14', 50)),
            'prev_rsi14': float(prev.get('rsi14', 50)),
            'macd': float(latest.get('macd', 0)),
            'macd_signal': float(latest.get('macd_signal', 0)),
            'macd_hist': float(latest.get('macd_hist', 0)),
            'prev_macd_hist': float(prev.get('macd_hist', 0)),
            'bb_upper': float(latest.get('bb_upper', 0)),
            'bb_lower': float(latest.get('bb_lower', 0)),
            'bb_bandwidth': float(latest.get('bb_bandwidth', 0)),
            'bb_percent_b': float(latest.get('bb_percent_b', 0.5)),
            'atr14': float(latest.get('atr14', 0)),
            'vwap': float(latest.get('vwap', 0)),
            'adx14': float(latest.get('adx14', 0)),
            'plus_di': float(latest.get('plus_di', 0)),
            'minus_di': float(latest.get('minus_di', 0)),
            'volume': float(latest.get('volume', 0)),
            'vol_ma20': float(latest.get('vol_ma20', 0)),
        }
