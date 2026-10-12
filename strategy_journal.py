"""전략 저널 (phil journal/retros·proposals 이식): 일일 관찰·가설, 주간 제안·결정·기각 사유를 DB strategy_journal에 남긴다.

쓰기는 strategy_journal 테이블 INSERT(+최초 CREATE)만 한다. 다른 테이블은 조회만.
실행 ($env:PYTHONUTF8=1; python -X utf8 strategy_journal.py ...):
  unreviewed                         아직 검토하지 않은 일일(REPORT)·주간(WEEKLY) 리포트 출력
  add <kind> <key> "<내용>" [--evidence "<근거 수치>"] [--ref <logs.id>]
  list [--days 14] [--key 가설키] [--kind observation]
  hypotheses [--days 28]             가설키별 관찰 일수·마지막 기록 (주간 계획표 재료)
kind: observation(관찰·가설) | proposal(수정안) | decision(승인·반영) | rejected(기각·사유) | followup(반영 후 확인) | review(리포트 검토 완료)
"""
import argparse
from datetime import timedelta

from daily_backtest import _connect
from database import get_kst_now

KINDS = ("observation", "proposal", "decision", "rejected", "followup", "review")
DDL = """CREATE TABLE IF NOT EXISTS strategy_journal (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    kind VARCHAR(20) NOT NULL,
    hkey VARCHAR(60) NOT NULL,
    content TEXT NOT NULL,
    evidence TEXT,
    ref_log_id INT,
    INDEX idx_hkey (hkey), INDEX idx_created (created_at)
)"""


def _run(sql: str, params=(), write: bool = False):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute(sql, params)
        rows = cur.fetchall()
        if write:
            conn.commit()
        return rows
    finally:
        conn.close()


def add(kind: str, key: str, content: str, evidence: str = None, ref: int = None):
    if kind not in KINDS:
        raise SystemExit(f"kind는 {KINDS} 중 하나")
    _run("INSERT INTO strategy_journal (created_at, kind, hkey, content, evidence, ref_log_id) VALUES (%s,%s,%s,%s,%s,%s)",
         (get_kst_now().replace(tzinfo=None), kind, key[:60], content, evidence, ref), write=True)
    print(f"기록: [{kind}] {key} — {content}")


def unreviewed():
    """마지막 review 기록 이후의 REPORT·WEEKLY 로그 (처음이면 최근 14일)"""
    last = _run("SELECT COALESCE(MAX(ref_log_id), 0) FROM strategy_journal WHERE kind='review'")[0][0]
    since = (get_kst_now() - timedelta(days=14)).replace(tzinfo=None)
    rows = _run("SELECT id, timestamp, level, message FROM logs WHERE level IN ('REPORT','WEEKLY') AND id > %s "
                "AND timestamp >= %s ORDER BY id", (last, since))
    if not rows:
        print("미검토 리포트 없음")
    for lid, ts, level, msg in rows:
        print(f"===== logs.id={lid} {ts} {level} =====\n{msg}\n")
    if rows:
        print(f"검토를 마치면: add review report \"검토 완료\" --ref {rows[-1][0]}")


def list_entries(days: int, key: str = None, kind: str = None):
    sql = "SELECT id, created_at, kind, hkey, content, evidence FROM strategy_journal WHERE created_at >= %s"
    params = [(get_kst_now() - timedelta(days=days)).replace(tzinfo=None)]
    if key:
        sql, params = sql + " AND hkey=%s", params + [key]
    if kind:
        sql, params = sql + " AND kind=%s", params + [kind]
    for jid, ts, k, h, c, e in _run(sql + " ORDER BY id", params):
        print(f"#{jid} {ts:%m-%d %H:%M} [{k}] {h}: {c}" + (f"  (근거: {e})" if e else ""))


def hypotheses(days: int):
    rows = _run("SELECT hkey, COUNT(DISTINCT DATE(created_at)), COUNT(*), MAX(created_at), "
                "SUM(kind='proposal'), SUM(kind IN ('decision','rejected')) FROM strategy_journal "
                "WHERE kind <> 'review' AND created_at >= %s GROUP BY hkey ORDER BY 2 DESC",
                ((get_kst_now() - timedelta(days=days)).replace(tzinfo=None),))
    print(f"{'가설키':<30} {'관찰일':>4} {'기록':>4} {'제안':>4} {'결정':>4}  마지막")
    for h, d, n, last, p, dec in rows:
        print(f"{h:<30} {d:>4} {n:>4} {int(p or 0):>4} {int(dec or 0):>4}  {last:%m-%d}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("unreviewed")
    a = sub.add_parser("add")
    a.add_argument("kind")
    a.add_argument("key")
    a.add_argument("content")
    a.add_argument("--evidence")
    a.add_argument("--ref", type=int)
    li = sub.add_parser("list")
    li.add_argument("--days", type=int, default=14)
    li.add_argument("--key")
    li.add_argument("--kind")
    hy = sub.add_parser("hypotheses")
    hy.add_argument("--days", type=int, default=28)
    args = ap.parse_args()
    if args.cmd == "add":
        add(args.kind, args.key, args.content, args.evidence, args.ref)
    elif args.cmd == "unreviewed":
        unreviewed()
    elif args.cmd == "list":
        list_entries(args.days, args.key, args.kind)
    else:
        hypotheses(args.days)


if __name__ == "__main__":
    main()
