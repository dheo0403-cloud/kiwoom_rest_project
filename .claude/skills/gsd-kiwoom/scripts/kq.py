# 조회 전용: 인수로 받은 SQL(SELECT/SHOW만)을 차례로 실행해 출력 (운영 MariaDB kiwoom_quant_db)
# 실행: $env:PYTHONUTF8=1; python -X utf8 kq.py "SELECT ..." "SELECT ..."
import os, sys, pymysql
from dotenv import load_dotenv

load_dotenv(r"C:/Users/MZC01-MICHAEL/Desktop/허동진/claude_project/kiwoom_rest_project/.env")
c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=300)
cur = c.cursor()
for q in sys.argv[1:]:
    if not q.lstrip().upper().startswith(("SELECT", "SHOW")):
        sys.exit("조회 전용: SELECT/SHOW만 허용")
    print("##", q)
    cur.execute(q)
    if cur.description:
        print([d[0] for d in cur.description])
    for r in cur.fetchall():
        print(r)
