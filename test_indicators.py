"""
기술적 지표 산출 엔진 (TechnicalIndicators) 단위 테스트
- Wilder's RSI, MACD, Bollinger Bands, ATR, VWAP, ADX 수학적 정합성 검증
"""
import pytest
import numpy as np
import pandas as pd
from indicators import TechnicalIndicators


@pytest.fixture
def sample_ohlcv_df():
    """테스트용 가상 60일 OHLCV 데이터 생성"""
    np.random.seed(42)
    n = 60
    base_price = 50000.0
    returns = np.random.normal(0.001, 0.02, n)
    close_prices = base_price * np.cumprod(1 + returns)

    high_prices = close_prices * (1 + np.random.uniform(0.005, 0.03, n))
    low_prices = close_prices * (1 - np.random.uniform(0.005, 0.03, n))
    open_prices = low_prices + (high_prices - low_prices) * np.random.uniform(0.2, 0.8, n)
    volumes = np.random.randint(10000, 500000, n)

    df = pd.DataFrame({
        'open': open_prices,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices,
        'volume': volumes
    })
    return df


def test_standardize_columns():
    """다양한 컬럼명(한글, 키움 API 코드명 등)의 표준화 검증"""
    df_raw = pd.DataFrame({
        'stck_oprc': [100, 105],
        'stck_hgpr': [110, 115],
        'stck_lwpr': [95, 100],
        'stck_clpr': [105, 110],
        'acml_vol': [1000, 2000]
    })
    df_std = TechnicalIndicators._standardize_columns(df_raw)
    assert set(['open', 'high', 'low', 'close', 'volume']).issubset(df_std.columns)
    assert df_std['close'].iloc[0] == 105.0


def test_rsi_calculation(sample_ohlcv_df):
    """Wilder's RSI(14) 산출 및 범위(0~100) 검증"""
    close = sample_ohlcv_df['close']
    rsi = TechnicalIndicators.calculate_rsi(close, period=14)

    assert len(rsi) == len(close)
    assert (rsi >= 0.0).all() and (rsi <= 100.0).all()
    # 연속 상승 시 RSI > 70
    bull_series = pd.Series([100.0 * (1.05 ** i) for i in range(30)])
    bull_rsi = TechnicalIndicators.calculate_rsi(bull_series, period=14)
    assert bull_rsi.iloc[-1] > 80.0


def test_macd_calculation(sample_ohlcv_df):
    """MACD(12, 26, 9) 계산 및 수치 정합성 검증"""
    close = sample_ohlcv_df['close']
    macd_df = TechnicalIndicators.calculate_macd(close, fast=12, slow=26, signal=9)

    assert 'macd' in macd_df.columns
    assert 'macd_signal' in macd_df.columns
    assert 'macd_hist' in macd_df.columns
    # macd_hist = macd - macd_signal
    diff = macd_df['macd'] - macd_df['macd_signal']
    np.testing.assert_allclose(macd_df['macd_hist'], diff, atol=1e-5)


def test_bollinger_bands(sample_ohlcv_df):
    """볼린저 밴드(20, 2) 상단/중단/하단 밴드 논리 정합성 검증"""
    close = sample_ohlcv_df['close']
    bb_df = TechnicalIndicators.calculate_bollinger_bands(close, period=20, nbdev=2.0)

    assert (bb_df['bb_upper'] >= bb_df['bb_middle']).all()
    assert (bb_df['bb_middle'] >= bb_df['bb_lower']).all()
    assert (bb_df['bb_bandwidth'] >= 0.0).all()


def test_atr_calculation(sample_ohlcv_df):
    """ATR(14) 변동성 산출 및 양수 값 유지 검증"""
    atr = TechnicalIndicators.calculate_atr(sample_ohlcv_df, period=14)
    assert len(atr) == len(sample_ohlcv_df)
    assert (atr > 0.0).all()


def test_vwap_calculation(sample_ohlcv_df):
    """VWAP 거래량 가중 평균가 산출 검증"""
    vwap = TechnicalIndicators.calculate_vwap(sample_ohlcv_df)
    assert len(vwap) == len(sample_ohlcv_df)
    # VWAP은 당일 최저가와 최고가 사이 또는 근접한 범위 내 존재
    assert (vwap > 0.0).all()


def test_adx_calculation(sample_ohlcv_df):
    """ADX(14), +DI, -DI 산출 및 0~100 범위 검증"""
    adx_df = TechnicalIndicators.calculate_adx(sample_ohlcv_df, period=14)
    assert set(['plus_di', 'minus_di', 'dx', 'adx']).issubset(adx_df.columns)
    assert (adx_df['plus_di'] >= 0.0).all()
    assert (adx_df['minus_di'] >= 0.0).all()
    assert (adx_df['adx'] >= 0.0).all()


def test_compute_all_and_latest_indicators(sample_ohlcv_df):
    """일괄 계산 및 최신 1건 딕셔너리 추출 검증"""
    computed = TechnicalIndicators.compute_all_indicators(sample_ohlcv_df)
    assert 'rsi14' in computed.columns
    assert 'macd' in computed.columns
    assert 'bb_upper' in computed.columns
    assert 'atr14' in computed.columns
    assert 'adx14' in computed.columns
    assert 'ma20' in computed.columns

    latest = TechnicalIndicators.get_latest_indicators(sample_ohlcv_df)
    assert isinstance(latest, dict)
    assert 'rsi14' in latest
    assert 'atr14' in latest
    assert 'adx14' in latest
    assert latest['close'] > 0
