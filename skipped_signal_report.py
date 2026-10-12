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
        for r in rows:
            res = score(cur, r["code"], r["date"], r["hhmmss"], r["price"])
            r["ret"], r["exit"] = res if res else (None, "분봉없음")
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["date", "code", "name", "group", "hhmmss", "price", "ret", "exit"])


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
    lines += [f"■ 최근 {days}일 누적", summarize(df),
              f"채점 보류(분봉 없음·미완료): 오늘 {int(td['ret'].isna().sum())}건 / 누적 {int(df['ret'].isna().sum())}건",
              f"그룹 표본 {MIN_SAMPLE}건 미만이면 그룹 간 차이로 결론 내지 않는다."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20, help="최근 며칠(달력 기준)")
    print(build_daily_report(ap.parse_args().days))


if __name__ == "__main__":
    main()
