"""거른 신호 채점 리포트 (조회 전용): 위험 관리로 거른 매수 신호(skipped_signals)와 실제 매수(order_history)를
같은 규칙으로 채점해 비교한다. 장 마감 후 분봉 수집이 끝난 뒤 실행 (봇이 매 거래일 수집 직후 자동 실행 → logs level=REPORT).

채점 규칙 (근사): 신호가(실제 매수는 접수가)에 진입 → 이후 정규장 분봉 저가가 진입가 × (1 + 하드스탑률) 이하이면 그 가격에 손절,
아니면 15:15 이전 마지막 분봉 종가에 청산. 왕복 비용은 백테스터 기본값(슬리피지·수수료·거래세). 트레일링 익절·ATR 손절은 미반영.
실행: python skipped_signal_report.py [--days 20]
"""
import argparse
import re
from datetime import timedelta

import pandas as pd

import limits
from backtest import HighFidelityBacktester
from daily_backtest import _connect
from database import get_kst_now

_bt = HighFidelityBacktester()
ROUND_TRIP_COST = 2 * _bt.slippage_rate + _bt.buy_fee_rate + _bt.sell_fee_rate + _bt.sell_tax_rate
HARD_STOP = limits.HARD_STOP_LOSS_RATE  # 음수 (예: −0.03)
EXIT_HHMMSS = "151500"
MIN_SAMPLE = 30  # 그룹 비교로 결론 내는 최소 표본


def score(cur, code: str, ymd: str, hhmmss: str, price: float):
    """진입 이후 당일 분봉으로 (수익률, 청산 사유) 계산. 분봉이 없으면 None"""
    # 분봉 datetime은 14자리와 'YYYY-MM-DD HH:MM:SS'가 섞여 있어 숫자만 남겨 비교
    dash = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    cur.execute("SELECT datetime, low, close FROM minute_ohlcv WHERE code=%s AND (datetime LIKE %s OR datetime LIKE %s)",
                (code, ymd + "%", dash + "%"))
    bars = sorted((re.sub(r"\D", "", str(dt))[8:14], float(lo), float(cl)) for dt, lo, cl in cur.fetchall())
    bars = [b for b in bars if hhmmss < b[0] <= EXIT_HHMMSS]  # 진입 분봉 다음부터
    if not bars:
        return None
    stop = price * (1 + HARD_STOP)
    for _, lo, cl in bars:
        if lo <= stop:
            return HARD_STOP - ROUND_TRIP_COST, "손절"
    if bars[-1][0] < "151000":  # 장중 실행·분봉 누락으로 청산 시각까지 데이터가 없으면 채점 보류
        return None
    return bars[-1][2] / price - 1 - ROUND_TRIP_COST, "15:15청산"


def _day_bars(cur, code: str, ymd: str):
    """당일 분봉 [(HHMMSS, 고가, 종가)] 시간순"""
    dash = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    cur.execute("SELECT datetime, high, close FROM minute_ohlcv WHERE code=%s AND (datetime LIKE %s OR datetime LIKE %s)",
                (code, ymd + "%", dash + "%"))
    return sorted((re.sub(r"\D", "", str(dt))[8:14], float(hi), float(cl)) for dt, hi, cl in cur.fetchall())


def entry_context(bars, hhmmss: str, price: float):
    """진입 직전 흐름 (10/26 관찰 entry_far_below_dayhigh·entry_after_runup 재료)
    → (당일 고점 대비 진입가 비율, 직전 30분 상승률). 진입 분봉 이전 정규장 분봉 기준, 계산 불가면 None"""
    pre = [b for b in bars if "090000" <= b[0] < hhmmss]
    if not pre:
        return None, None
    dd = price / max(b[1] for b in pre) - 1
    run = pre[-1][2] / pre[-31][2] - 1 if len(pre) > 30 else None
    return dd, run


def after_exit_max(bars, hhmmss: str, price: float):
    """청산 후 30분(분봉 30개) 최고가의 청산가 대비 상승률 (관찰 exit_breakeven_early 재료). 분봉이 없으면 None"""
    after = [b for b in bars if hhmmss < b[0] <= "153000"][:30]
    return max(b[1] for b in after) / price - 1 if after else None


_EXIT_MSG = re.compile(r"\]\s*(.+?)\((\d{6})\)\s*\d+주 @ ([\d,]+)원 \((.+)\)$")


def load_exits(start: str) -> pd.DataFrame:
    """start(YYYYMMDD) 이후 봇 청산(logs '[긴급 매도 주문 접수]', KST)별 사유와 청산 후 30분 최고 상승률"""
    conn = _connect()
    rows, bars_cache = [], {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT timestamp, message FROM logs WHERE timestamp >= %s AND message LIKE %s ORDER BY timestamp",
                    (start, "%긴급 매도 주문 접수%"))
        for ts, msg in cur.fetchall():
            m = _EXIT_MSG.search(msg)
            if not m:
                continue
            name, code, price, reason = m.group(1), m.group(2), float(m.group(3).replace(",", "")), m.group(4)
            ymd, hhmmss = ts.strftime("%Y%m%d"), ts.strftime("%H%M%S")
            if (code, ymd) not in bars_cache:
                bars_cache[(code, ymd)] = _day_bars(cur, code, ymd)
            rows.append(dict(date=ymd, hhmmss=hhmmss, code=code, name=name, price=price,
                             reason=re.split(r"\(|_최고", reason)[0],  # 백테스트 청산 사유 분류와 같은 기준
                             after=after_exit_max(bars_cache[(code, ymd)], hhmmss, price)))
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["date", "hhmmss", "code", "name", "price", "reason", "after"])


def load_scored(start: str) -> pd.DataFrame:
    """start(YYYYMMDD) 이후 거른 신호·실제 매수를 모아 채점한 표 (ret None = 채점 보류)"""
    conn = _connect()
    try:
        cur = conn.cursor()
        rows = []
        cur.execute("SHOW TABLES LIKE 'skipped_signals'")  # 배포 전이면 테이블 없음 → 실제 매수만 채점
        if cur.fetchone():
            cur.execute("SELECT date, code, name, block_reason, signal_time, price FROM skipped_signals WHERE date >= %s", (start,))
            for ymd, code, name, block, ts, price in cur.fetchall():
                rows.append(dict(date=ymd, code=code, name=name, group=f"거름:{block}", hhmmss=ts.strftime("%H%M%S"), price=float(price)))
        # 실제 매수: order_history.timestamp는 UTC → KST(+9h), 같은 날 같은 종목은 첫 접수만
        cur.execute("SELECT code, name, price, timestamp + INTERVAL 9 HOUR FROM order_history "
                    "WHERE side='BUY' AND timestamp + INTERVAL 9 HOUR >= %s ORDER BY timestamp", (start,))
        seen = set()
        for code, name, price, ts in cur.fetchall():
            key = (ts.strftime("%Y%m%d"), code)
            if key in seen or not price:
                continue
            seen.add(key)
            rows.append(dict(date=key[0], code=code, name=name, group="실제매수", hhmmss=ts.strftime("%H%M%S"), price=float(price)))
        bars_cache = {}
        for r in rows:
            res = score(cur, r["code"], r["date"], r["hhmmss"], r["price"])
            r["ret"], r["exit"] = res if res else (None, "분봉없음")
            key = (r["code"], r["date"])
            if key not in bars_cache:
                bars_cache[key] = _day_bars(cur, *key)
            r["dd_high"], r["run30"] = entry_context(bars_cache[key], r["hhmmss"], r["price"])
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["date", "code", "name", "group", "hhmmss", "price", "ret", "exit", "dd_high", "run30"])


def _dd_bucket(x) -> str:
    return "미산출" if pd.isna(x) else ("고점-5%이하" if x <= -0.05 else "고점-5%초과")


def _run_bucket(x) -> str:
    return "미산출" if pd.isna(x) else ("직전30분+2%이상" if x >= 0.02 else "직전30분+1~2%" if x >= 0.01 else "직전30분+1%미만")


def context_sections(df: pd.DataFrame, exits: pd.DataFrame, label: str) -> list:
    """진입 직전 흐름별 채점 성적과 청산 사유별 청산 후 흐름 (10/26 관찰 3건의 누적 재료)"""
    d = df.copy()
    d["kind"] = d["group"].where(d["group"] == "실제매수", "거른신호")
    d["고점대비"] = d["kind"] + "|" + d["dd_high"].map(_dd_bucket)
    d["직전30분"] = d["kind"] + "|" + d["run30"].map(_run_bucket)
    lines = [_breakdown(d, "고점대비", f"■ {label} 진입가의 당일 고점 대비 (entry_far_below_dayhigh)"),
             _breakdown(d, "직전30분", f"■ {label} 진입 직전 30분 상승률 (entry_after_runup)")]
    e = exits.dropna(subset=["after"])
    if e.empty:
        lines.append(f"■ {label} 청산 후 30분 흐름 (exit_breakeven_early): (없음)")
    else:
        g = e.groupby("reason")["after"]
        out = pd.DataFrame({"건수": g.size(), "청산후최고평균%": g.mean() * 100,
                            "청산가+1%이상비율%": g.apply(lambda s: (s >= 0.01).mean() * 100)})
        lines.append(f"■ {label} 청산 사유별 청산 후 30분 최고가 (exit_breakeven_early)\n{out.round(2).to_string()}")
    return lines


def summarize(df: pd.DataFrame) -> str:
    """그룹별 건수·평균·승률·최저 (%) 표 문자열"""
    scored = df.dropna(subset=["ret"])
    if scored.empty:
        return "(채점된 거래 없음)"
    g = scored.groupby("group")["ret"]
    out = pd.DataFrame({"건수": g.size(), "평균%": g.mean() * 100, "승률%": g.apply(lambda s: (s > 0).mean() * 100),
                        "최저%": g.min() * 100})
    return out.round(2).to_string()


def build_daily_report(days: int = 20) -> str:
    """오늘 채점 + 최근 days일 누적 비교 (봇 장 마감 후 자동 실행용)"""
    now = get_kst_now()
    today = now.strftime("%Y%m%d")
    df = load_scored((now - timedelta(days=days)).strftime("%Y%m%d"))
    td = df[df["date"] == today]
    lines = [f"[일일 채점 {today}] 왕복 비용 {ROUND_TRIP_COST:.2%}, 손절 {HARD_STOP:.1%}, 15:15 청산 근사",
             "■ 오늘", summarize(td)]
    for r in td[td["group"] != "실제매수"].sort_values("hhmmss").itertuples():
        ret = f"{r.ret:+.2%}" if pd.notna(r.ret) else "-"
        lines.append(f"  {r.hhmmss[:4]} {r.name}({r.code}) {int(r.price):,}원 {r.group} → {ret} {r.exit}")
    pct = lambda x: f"{x:+.2%}" if pd.notna(x) else "-"
    lines.append("■ 오늘 실제 매수 진입 직전 흐름 (당일 고점 대비 / 직전 30분)")
    for r in td[td["group"] == "실제매수"].sort_values("hhmmss").itertuples():
        lines.append(f"  {r.hhmmss[:4]} {r.name}({r.code}) {int(r.price):,}원 고점대비 {pct(r.dd_high)} / 직전30분 {pct(r.run30)}")
    exits = load_exits((now - timedelta(days=days)).strftime("%Y%m%d"))
    lines.append("■ 오늘 청산 후 30분 최고가 (청산가 대비)")
    for r in exits[exits["date"] == today].itertuples():
        lines.append(f"  {r.hhmmss[:4]} {r.name}({r.code}) {int(r.price):,}원 {r.reason} → {pct(r.after)}")
    lines += context_sections(df, exits, f"최근 {days}일")
    lines += [f"■ 최근 {days}일 누적", summarize(df),
              f"채점 보류(분봉 없음·미완료): 오늘 {int(td['ret'].isna().sum())}건 / 누적 {int(df['ret'].isna().sum())}건",
              f"그룹 표본 {MIN_SAMPLE}건 미만이면 그룹 간 차이로 결론 내지 않는다."]
    return "\n".join(lines)


def _breakdown(df: pd.DataFrame, col: str, title: str) -> str:
    s = df.dropna(subset=["ret"])
    if s.empty:
        return f"{title}: (없음)"
    g = s.groupby(col)["ret"]
    out = pd.DataFrame({"건수": g.size(), "평균%": g.mean() * 100, "승률%": g.apply(lambda x: (x > 0).mean() * 100)})
    return f"{title}\n{out.round(2).to_string()}"


def build_weekly_report(weeks: int = 4) -> str:
    """주간 판정표 재료: 이번 주(월~오늘) 그룹·날짜·진입 시간대·청산 사유별 성적 + 최근 N주 누적 (기준 대조는 weekly_review_rules.md)"""
    now = get_kst_now()
    monday = (now - timedelta(days=now.weekday())).strftime("%Y%m%d")
    df = load_scored((now - timedelta(weeks=weeks)).strftime("%Y%m%d"))
    df["hour"] = df["hhmmss"].str[:2] + "시"
    wk = df[df["date"] >= monday]
    buys = wk[wk["group"] == "실제매수"]
    lines = [f"[주간 판정표 {monday}~{now:%Y%m%d}] 기준: research/weekly_review_rules.md",
             "■ 이번 주 그룹별", summarize(wk),
             _breakdown(buys, "date", "■ 이번 주 실제 매수 — 날짜별"),
             _breakdown(buys, "hour", "■ 이번 주 실제 매수 — 진입 시간대별"),
             _breakdown(buys, "exit", "■ 이번 주 실제 매수 — 청산 사유별"),
             f"■ 최근 {weeks}주 누적", summarize(df),
             _breakdown(df[df["group"] == "실제매수"], "hour", f"■ 최근 {weeks}주 실제 매수 — 진입 시간대별"),
             *context_sections(df, load_exits((now - timedelta(weeks=weeks)).strftime("%Y%m%d")), f"최근 {weeks}주"),
             f"채점 보류: 이번 주 {int(wk['ret'].isna().sum())}건 / 누적 {int(df['ret'].isna().sum())}건"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20, help="최근 며칠(달력 기준)")
    ap.add_argument("--weekly", action="store_true", help="주간 판정표 출력")
    args = ap.parse_args()
    print(build_weekly_report() if args.weekly else build_daily_report(args.days))


if __name__ == "__main__":
    main()
