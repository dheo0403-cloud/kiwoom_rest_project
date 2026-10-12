"""주문 한도 — 실계좌 위험을 정하는 값과 넘으면 안 되는 상한을 한곳에 둔다.

상한(BOUNDS)은 사람이 이유를 적어 직접 커밋해서만 바꾼다. test_limits.py가 현재 값·상한을 고정하고,
봇은 기동할 때 validate()로 확인해 상한을 넘으면 기동을 거부한다(대시보드 API 변경도 같은 검사).
보호 영역: 이 파일·test_limits.py·.github/ 를 바꾼 커밋은 메시지에 [operator]가 있어야 배포된다(.github/scripts/boundary.py).
"""
import os

# 현재 운용 값 (2026-10-12 고정)
MAX_STOCKS = 5                 # 동시 보유 + 미체결 매수 종목 수
MAX_DAILY_ENTRIES = 10         # 하루 신규 진입 수 (환경변수 MAX_DAILY_ENTRIES로 더 낮출 수만 있음)
ONE_ENTRY_PER_DAY = True       # 같은 종목 하루 1회 진입
DAILY_LOSS_LIMIT_RATE = -0.025 # 하루 손실 한도 (서킷 브레이커)
HARD_STOP_LOSS_RATE = -0.03    # 하드 손절
KELLY_FRACTION = 0.4           # 프랙셔널 켈리 비중

# 허용 범위 (최소, 최대) — 손실 계열은 음수라 '최소'가 가장 느슨한 값
BOUNDS = {
    "max_stocks": (1, 5),
    "max_daily_entries": (1, 10),          # 0(무제한) 금지
    "daily_loss_limit_rate": (-0.025, -0.001),
    "hard_stop_loss_rate": (-0.03, -0.001),
    "kelly_fraction": (0.01, 0.4),
}


# 주간 검토로 고칠 수 있는 전략 파라미터(strategy_params.json)의 허용 범위 — 이 표에 없는 키는 파일에 넣을 수 없음.
# 손실 상한은 하드 손절(HARD_STOP_LOSS_RATE)이 따로 막으므로 ATR 배수를 키워도 1회 손실은 −3%를 넘지 않는다.
STRATEGY_PARAM_BOUNDS = {
    "k_breakout": (0.3, 1.0),
    "atr_hard_stop_mult": (0.5, 3.0),
    "atr_trailing_stop_mult": (1.0, 4.0),
    "breakeven_trigger_pct": (0.005, 0.03),
    "trailing_stop_drop_rate": (-0.05, -0.005),
    "trailing_activation_pct": (0.005, 0.05),
    "breakeven_buffer_pct": (0.0, 0.01),
}
STRATEGY_BOOL_PARAMS = {"use_trailing_stop_only"}
UNDISABLEABLE_FILTERS = {"time", "indicator_missing"}  # 장 시간·지표 누락 필터는 끌 수 없음


def strategy_param_violations(params: dict) -> list:
    """strategy_params.json 값 검사 (빈 목록 = 정상)"""
    out = []
    for key, val in params.items():
        if key == "disabled_filters":
            locked = set(val) & UNDISABLEABLE_FILTERS
            if locked:
                out.append(f"disabled_filters에 끌 수 없는 필터 {sorted(locked)}")
        elif key in STRATEGY_BOOL_PARAMS:
            if not isinstance(val, bool):
                out.append(f"{key}={val} (true/false만)")
        elif key in STRATEGY_PARAM_BOUNDS:
            lo, hi = STRATEGY_PARAM_BOUNDS[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= val <= hi:
                out.append(f"{key}={val} (허용 {lo}~{hi})")
        else:
            out.append(f"{key}: 수정 허용 목록에 없는 파라미터")
    return out


def env_max_daily_entries() -> int:
    return int(os.getenv("MAX_DAILY_ENTRIES", str(MAX_DAILY_ENTRIES)))


def env_one_entry_per_day() -> bool:
    return os.getenv("ONE_ENTRY_PER_STOCK_PER_DAY", "true").lower() in ("true", "1", "yes")


def violations(**values) -> list:
    """BOUNDS를 벗어난 값과 같은 종목 재진입 허용을 찾아 사유 목록으로 돌려준다 (빈 목록 = 정상)."""
    out = []
    for key, val in values.items():
        if key == "one_entry_per_day":
            if not val:
                out.append("one_entry_per_day=False (같은 종목 하루 1회 해제 금지)")
            continue
        lo, hi = BOUNDS[key]
        if not lo <= val <= hi:
            out.append(f"{key}={val} (허용 {lo}~{hi})")
    return out


def validate(**values) -> None:
    bad = violations(**values)
    if bad:
        raise ValueError("주문 한도 상한 초과: " + ", ".join(bad))
