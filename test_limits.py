"""주문 한도 고정 테스트 — 값이나 상한을 바꾸면 여기서 실패한다. 바꾸려면 사람이 이유를 적어 이 파일과 limits.py를 함께 커밋."""
import pytest

import limits
from async_portfolio import AsyncPortfolioManager
from main_rest_async import AsyncTradingBot
from strategy import AdaptiveVolatilityBreakoutStrategy
from test_async_trading_loop import MockKiwoomClient, MockDatabaseManager


def _bot():
    return AsyncTradingBot(is_demo=True, client=MockKiwoomClient(), db=MockDatabaseManager())


def test_limit_values_and_bounds_are_pinned():
    """2026-10-12 사용자 승인값: 현재 값 = 상한"""
    assert (limits.MAX_STOCKS, limits.MAX_DAILY_ENTRIES, limits.ONE_ENTRY_PER_DAY) == (5, 10, True)
    assert (limits.DAILY_LOSS_LIMIT_RATE, limits.HARD_STOP_LOSS_RATE, limits.KELLY_FRACTION) == (-0.025, -0.03, 0.4)
    assert limits.BOUNDS == {
        "max_stocks": (1, 5),
        "max_daily_entries": (1, 10),
        "daily_loss_limit_rate": (-0.025, -0.001),
        "hard_stop_loss_rate": (-0.03, -0.001),
        "kelly_fraction": (0.01, 0.4),
    }


def test_runtime_objects_use_limits(monkeypatch):
    """봇·포트폴리오·전략 기본값이 limits.py 값을 실제로 쓴다"""
    monkeypatch.delenv("MAX_DAILY_ENTRIES", raising=False)
    monkeypatch.delenv("ONE_ENTRY_PER_STOCK_PER_DAY", raising=False)
    bot = _bot()
    assert bot.portfolio.max_stocks == limits.MAX_STOCKS
    assert bot.portfolio.kelly_fraction == limits.KELLY_FRACTION
    assert bot.strategy.hard_stop_loss_rate == limits.HARD_STOP_LOSS_RATE
    assert bot.daily_loss_limit_rate == limits.DAILY_LOSS_LIMIT_RATE
    assert bot.max_daily_entries == limits.MAX_DAILY_ENTRIES
    assert bot.one_entry_per_day is True


def test_real_mode_is_default(monkeypatch):
    """환경변수가 없으면 실전(REAL) — 모드 기본값이 바뀌면 실패"""
    monkeypatch.delenv("IS_REAL", raising=False)
    monkeypatch.delenv("KIWOOM_MODE", raising=False)
    bot = AsyncTradingBot(client=MockKiwoomClient(), db=MockDatabaseManager())
    assert bot.is_demo is False


@pytest.mark.parametrize("env,val", [
    ("MAX_DAILY_ENTRIES", "11"),
    ("MAX_DAILY_ENTRIES", "0"),  # 0 = 무제한
    ("ONE_ENTRY_PER_STOCK_PER_DAY", "false"),
])
def test_bot_refuses_to_start_over_limit_env(monkeypatch, env, val):
    monkeypatch.setenv(env, val)
    with pytest.raises(ValueError, match="주문 한도"):
        _bot()


def test_bot_allows_stricter_env(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_ENTRIES", "3")
    assert _bot().max_daily_entries == 3


@pytest.mark.parametrize("kwargs", [
    {"portfolio": AsyncPortfolioManager(initial_capital=100_000, max_stocks=6)},
    {"portfolio": AsyncPortfolioManager(initial_capital=100_000, kelly_fraction=0.5)},
    {"strategy": AdaptiveVolatilityBreakoutStrategy(hard_stop_loss_rate=-0.05)},
])
def test_bot_refuses_to_start_with_loose_components(kwargs):
    with pytest.raises(ValueError, match="주문 한도"):
        AsyncTradingBot(is_demo=True, client=MockKiwoomClient(), db=MockDatabaseManager(), **kwargs)
