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

        # 0 나누기 및 평탄 가격(변동 없음) 처리
        both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
        gain_only = (avg_loss == 0.0) & (avg_gain > 0.0)
        loss_only = (avg_gain == 0.0) & (avg_loss > 0.0)

        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi = rsi.where(~gain_only, 100.0)
        rsi = rsi.where(~loss_only, 0.0)
        rsi = rsi.where(~both_zero, 50.0)
        rsi = rsi.fillna(50.0)
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

    @staticmethod
    def calculate_chandelier_exit(df: pd.DataFrame, period: int = 14, multiplier: float = 2.5) -> pd.DataFrame:
        """
        Chandelier Exit (샹들리에 출구 - 변동성 기반 동적 트레일링 스탑)
        - Long Stop = Highest High(period) - (multiplier * ATR(period))
        - Short Stop = Lowest Low(period) + (multiplier * ATR(period))
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        high = df_std['high']
        low = df_std['low']
        atr = TechnicalIndicators.calculate_atr(df_std, period=period)

        highest_high = high.rolling(window=period, min_periods=1).max()
        lowest_low = low.rolling(window=period, min_periods=1).min()

        long_stop = highest_high - (multiplier * atr)
        short_stop = lowest_low + (multiplier * atr)

        return pd.DataFrame({
            'chandelier_long': long_stop,
            'chandelier_short': short_stop,
            'highest_high': highest_high,
            'lowest_low': lowest_low,
            'atr': atr
        }, index=df_std.index)

    @staticmethod
    def calculate_keltner_channels(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, multiplier: float = 1.5) -> pd.DataFrame:
        """
        Keltner Channels (켈트너 채널)
        - Upper = EMA(close, 20) + (multiplier * ATR(10))
        - Middle = EMA(close, 20)
        - Lower = EMA(close, 20) - (multiplier * ATR(10))
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        close = df_std['close']
        atr = TechnicalIndicators.calculate_atr(df_std, period=atr_period)

        middle = close.ewm(span=ema_period, adjust=False).mean()
        upper = middle + (multiplier * atr)
        lower = middle - (multiplier * atr)

        return pd.DataFrame({
            'kc_upper': upper,
            'kc_middle': middle,
            'kc_lower': lower
        }, index=df_std.index)

    @staticmethod
    def calculate_squeeze_momentum(df: pd.DataFrame, bb_period: int = 20, bb_mult: float = 2.0,
                                   kc_period: int = 20, kc_mult: float = 1.5) -> pd.DataFrame:
        """
        John Carter's Squeeze Momentum Indicator (볼린저 밴드 + 켈트너 채널 스퀴즈)
        - Squeeze On: BB가 KC 내부로 압축 수축된 상태 (폭발적 변동성 예고)
        - Squeeze Off: BB가 KC 밖으로 확장 돌파한 상태 (모멘텀 분출)
        """
        df_std = TechnicalIndicators._standardize_columns(df)
        close = df_std['close']
        high = df_std['high']
        low = df_std['low']

        bb = TechnicalIndicators.calculate_bollinger_bands(close, period=bb_period, nbdev=bb_mult)
        kc = TechnicalIndicators.calculate_keltner_channels(df_std, ema_period=kc_period, atr_period=kc_period, multiplier=kc_mult)

        # Squeeze 판정: BB 상단 < KC 상단 and BB 하단 > KC 하단
        squeeze_on = (bb['bb_upper'] < kc['kc_upper']) & (bb['bb_lower'] > kc['kc_lower'])

        # 모멘텀 값 산출: Linear Regression of (close - average(highest_high, lowest_low, sma))
        highest = high.rolling(window=kc_period, min_periods=1).max()
        lowest = low.rolling(window=kc_period, min_periods=1).min()
        sma = close.rolling(window=kc_period, min_periods=1).mean()
        hl_avg = (highest + lowest) / 2.0
        delta = close - ((hl_avg + sma) / 2.0)

        # 20 기간 선형회귀 근사 (간이 모멘텀)
        momentum = delta.rolling(window=kc_period, min_periods=1).mean()

        return pd.DataFrame({
            'squeeze_on': squeeze_on,
            'squeeze_off': ~squeeze_on,
            'momentum': momentum,
            'momentum_positive': momentum > 0,
            'momentum_increasing': momentum > momentum.shift(1).fillna(0)
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

        # 9. Chandelier Exit (14, 2.5)
        ch_df = cls.calculate_chandelier_exit(df_res, period=14, multiplier=2.5)
        df_res['chandelier_long'] = ch_df['chandelier_long']
        df_res['chandelier_short'] = ch_df['chandelier_short']

        # 10. Squeeze Momentum
        sq_df = cls.calculate_squeeze_momentum(df_res)
        df_res['squeeze_on'] = sq_df['squeeze_on']
        df_res['squeeze_off'] = sq_df['squeeze_off']
        df_res['squeeze_momentum'] = sq_df['momentum']

        return df_res

    @staticmethod
    def calculate_volume_profile(df: pd.DataFrame, num_bins: int = 10) -> Dict[str, Any]:
        """
        Volume Profile (매물대 분석) 및 Point of Control(POC) 산출
        - 최근 캔들들의 가격 범위를 num_bins개 구간으로 분할하여 각 구간별 누적 거래량 집계
        - POC (Point of Control): 가장 많은 거래량이 체결된 핵심 매물대 가격
        - VAH (Value Area High) / VAL (Value Area Low): 전체 거래량의 70%가 집중된 가치 영역 상단/하단
        """
        if df.empty or len(df) < 2:
            close_val = float(df['close'].iloc[-1]) if not df.empty and 'close' in df.columns else 0.0
            return {
                'poc_price': close_val,
                'vah': close_val,
                'val': close_val,
                'is_above_poc': True,
                'profile': []
            }

        df_std = TechnicalIndicators._standardize_columns(df)
        highs = df_std['high']
        lows = df_std['low']
        closes = df_std['close']
        volumes = df_std['volume']

        min_p = float(lows.min())
        max_p = float(highs.max())

        if max_p <= min_p or max_p <= 0:
            cur_p = float(closes.iloc[-1])
            return {
                'poc_price': cur_p,
                'vah': cur_p,
                'val': cur_p,
                'is_above_poc': True,
                'profile': []
            }

        bin_size = (max_p - min_p) / num_bins
        bin_volumes = [0.0] * num_bins

        typical_prices = (highs + lows + closes) / 3.0
        for tp, vol in zip(typical_prices, volumes):
            if vol <= 0:
                continue
            b_idx = int((tp - min_p) / bin_size)
            b_idx = min(num_bins - 1, max(0, b_idx))
            bin_volumes[b_idx] += vol

        max_vol_idx = int(np.argmax(bin_volumes))
        poc_price = min_p + (max_vol_idx + 0.5) * bin_size

        total_vol = sum(bin_volumes)
        target_vol = total_vol * 0.70
        cum_vol = 0.0
        val_idx = max_vol_idx
        vah_idx = max_vol_idx
        cum_vol += bin_volumes[max_vol_idx]

        while cum_vol < target_vol and (val_idx > 0 or vah_idx < num_bins - 1):
            next_below = bin_volumes[val_idx - 1] if val_idx > 0 else -1
            next_above = bin_volumes[vah_idx + 1] if vah_idx < num_bins - 1 else -1

            if next_above >= next_below and next_above >= 0:
                vah_idx += 1
                cum_vol += next_above
            elif next_below >= 0:
                val_idx -= 1
                cum_vol += next_below
            else:
                break

        vah = min_p + (vah_idx + 1) * bin_size
        val = min_p + val_idx * bin_size
        current_price = float(closes.iloc[-1])

        profile_data = [
            {'bin_low': round(min_p + i * bin_size, 1), 'bin_high': round(min_p + (i + 1) * bin_size, 1), 'volume': round(bin_volumes[i], 0)}
            for i in range(num_bins)
        ]

        return {
            'poc_price': round(poc_price, 1),
            'vah': round(vah, 1),
            'val': round(val, 1),
            'is_above_poc': current_price >= poc_price,
            'profile': profile_data
        }

    @classmethod
    def get_latest_indicators(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """최신 1건의 지표를 딕셔너리로 추출 (실시간 전략 평가용)"""
        if df.empty:
            return {}

        computed_df = cls.compute_all_indicators(df)
        latest = computed_df.iloc[-1]
        prev = computed_df.iloc[-2] if len(computed_df) > 1 else latest
        vp = cls.calculate_volume_profile(df, num_bins=10)

        return {
            'close': float(latest.get('close', 0)),
            'open': float(latest.get('open', 0)),
            'high': float(latest.get('high', 0)),
            'low': float(latest.get('low', 0)),
            'volume': float(latest.get('volume', 0)),
            'ma5': float(latest.get('ma5', 0)),
            'ma20': float(latest.get('ma20', 0)),
            'ma60': float(latest.get('ma60', 0)),
            'vol_ma5': float(latest.get('vol_ma5', 0)),
            'vol_ma20': float(latest.get('vol_ma20', 0)),
            'rsi14': float(latest.get('rsi14', 50)),
            'prev_rsi14': float(prev.get('rsi14', 50)),
            'macd': float(latest.get('macd', 0)),
            'macd_signal': float(latest.get('macd_signal', 0)),
            'macd_hist': float(latest.get('macd_hist', 0)),
            'bb_upper': float(latest.get('bb_upper', 0)),
            'bb_middle': float(latest.get('bb_middle', 0)),
            'bb_lower': float(latest.get('bb_lower', 0)),
            'bb_bandwidth': float(latest.get('bb_bandwidth', 0)),
            'bb_percent_b': float(latest.get('bb_percent_b', 0.5)),
            'atr14': float(latest.get('atr14', 0)),
            'vwap': float(latest.get('vwap', 0)),
            'poc_price': float(vp.get('poc_price', 0)),
            'vah': float(vp.get('vah', 0)),
            'val': float(vp.get('val', 0)),
            'is_above_poc': bool(vp.get('is_above_poc', True)),
            'adx': float(latest.get('adx14', 0)),
            'adx14': float(latest.get('adx14', 0)),
            'chandelier_long': float(latest.get('chandelier_long', 0)),
            'chandelier_short': float(latest.get('chandelier_short', 0)),
            'squeeze_on': bool(latest.get('squeeze_on', False)),
            'squeeze_off': bool(latest.get('squeeze_off', True)),
            'squeeze_momentum': float(latest.get('squeeze_momentum', 0)),
            'plus_di': float(latest.get('plus_di', 0)),
            'minus_di': float(latest.get('minus_di', 0))
        }

    @staticmethod
    def calculate_orderbook_imbalance(orderbook: Dict[str, Any]) -> Dict[str, float]:
        """
        호가창 불균형(Orderbook Imbalance) 및 스프레드 지표 산출
        - Imbalance Ratio = (Total Bid Qty - Total Ask Qty) / (Total Bid Qty + Total Ask Qty)
        - 양수(+): 매수 잔량 우세 (상승 지지 압력)
        - 음수(-): 매도 잔량 우세 (하락 매도 압력)
        """
        if not orderbook or not isinstance(orderbook, dict):
            return {'imbalance_ratio': 0.0, 'total_bid_qty': 0.0, 'total_ask_qty': 0.0, 'bid_ask_spread': 0.0}

        out = orderbook.get('output', orderbook)
        if isinstance(out, list) and len(out) > 0:
            out = out[0]
        elif not isinstance(out, dict):
            out = {}

        total_bid_qty = 0.0
        total_ask_qty = 0.0

        # 5단계 호가 잔량 집계
        for i in range(1, 6):
            b_q = out.get(f'buy_fpr_bid_qty{i}') or out.get(f'bid_qty{i}') or out.get(f'bid_rsqn{i}') or 0
            a_q = out.get(f'sel_fpr_bid_qty{i}') or out.get(f'ask_qty{i}') or out.get(f'ask_rsqn{i}') or 0
            try:
                total_bid_qty += abs(float(str(b_q).replace(',', '').strip() or 0))
                total_ask_qty += abs(float(str(a_q).replace(',', '').strip() or 0))
            except (ValueError, TypeError):
                pass

        # 총 매수/매도 잔량 필드가 직접 제공되는 경우 우선 반영
        tot_b = out.get('tot_buy_qty') or out.get('total_bid_qty') or out.get('tot_bid_rsqn')
        tot_a = out.get('tot_sel_qty') or out.get('total_ask_qty') or out.get('tot_ask_rsqn')
        if tot_b and tot_a:
            try:
                total_bid_qty = max(total_bid_qty, abs(float(str(tot_b).replace(',', '').strip() or 0)))
                total_ask_qty = max(total_ask_qty, abs(float(str(tot_a).replace(',', '').strip() or 0)))
            except (ValueError, TypeError):
                pass

        total_depth = total_bid_qty + total_ask_qty
        imbalance_ratio = ((total_bid_qty - total_ask_qty) / total_depth) if total_depth > 0 else 0.0

        ask1 = out.get('sel_fpr_bid') or out.get('ask_price1') or 0
        bid1 = out.get('buy_fpr_bid') or out.get('bid_price1') or 0
        try:
            spread = max(0.0, float(str(ask1).replace(',', '').strip() or 0) - float(str(bid1).replace(',', '').strip() or 0))
        except (ValueError, TypeError):
            spread = 0.0

        return {
            'imbalance_ratio': round(imbalance_ratio, 4),
            'total_bid_qty': total_bid_qty,
            'total_ask_qty': total_ask_qty,
            'bid_ask_spread': spread
        }

    @staticmethod
    def calculate_volume_power(accum_buy_vol: float, accum_sell_vol: float) -> float:
        """
        체결강도(Volume Power / Buying Strength) 산출 (%)
        - 체결강도 = (체결 매수량 / 체결 매도량) * 100.0
        - 100% 초과: 매수 체결 우세
        - 120% 이상: 강력한 수급 모멘텀
        """
        if accum_sell_vol <= 0:
            return 100.0 if accum_buy_vol <= 0 else 200.0
        power = (accum_buy_vol / accum_sell_vol) * 100.0
        return round(float(np.clip(power, 0.0, 500.0)), 2)
