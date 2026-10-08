---
name: gsd-kiwoom
description: kiwoom_rest_project(키움 REST 실전 자동매매 봇) 전용 GSD 실행 스킬. 운영 봇 상태 점검("확인해야 할 사항 확인해줘"), 버그 수정, 수집·매매 로직 변경, 배포, 연구(백테스트·탐색) 요청을 [점검/조회 → 표 비교 → 사전 승인 → 슬림 수정 → pytest·운영 로그 실측 → 코드 리뷰 → WORK_HISTORY → 장 마감 후 푸시·배포 확인] 순서로 처리한다. 키움 봇·kiwoom DB·kiwoom-bot Pod 관련 작업이면 gsd-prompt 대신 사용.
---

# gsd-kiwoom — 키움 실전 봇 전용 실행 절차

요청: `$ARGUMENTS`

이 봇은 **실계좌(REAL)로 주문을 낸다.** 조회는 바로 하고, 운영을 바꾸는 일은 모두 사전 승인을 받는다.

## 0. 도구 현황 (2026-10-08 실측, `claude mcp list`·디렉터리 확인)

| 구분 | 이름 | 상태/위치 | 키움에서 쓰는 곳 | 실패 시 대체 |
|---|---|---|---|---|
| MCP | **kiwoom-spec** | √ 연결 (kiwoom_rest_project 로컬 등록, uv 실행) | TR 스펙·필드 의미: `spec_search` → `spec_show` → `get_example`. 키움 API는 호출하지 않음 | `C:\Users\MZC01-MICHAEL\tools\Kiwoom-REST-API` 문서 Grep |
| MCP | **context7** | √ 연결 (user scope, 모든 프로젝트) | aiohttp·aiomysql·pandas·Streamlit·FastAPI 문서 | WebFetch |
| MCP | **repomix** | √ 연결 (user scope) | 저장소 요약 (보통 Grep/Glob로 충분) | Grep/Glob |
| MCP | bigquery | √ 연결 (claude_project `.mcp.json`) | 키움에서는 안 씀 (DB는 MariaDB) | — |
| MCP | claude.ai Claude Docs / Mem0 | √ 연결 | 공유 문서를 요청받았을 때만 / 장기 기억은 파일 메모리 우선 | `MEMORY.md` |
| 플러그인 | 없음 | 설치된 플러그인 없음 (marketplace 캐시만 있음) | — | — |
| 프로젝트 스킬 | **kiwoom-backtest** | `.claude/skills/kiwoom-backtest` | 전략 변경 전후 분봉 WFO·일봉 백테스트 | — |
| 사용자 스킬 | ponytail · karpathy-skills · agent-skills · markitdown · task-observer · github-deploy | `~/.claude/skills` 등 | 슬림 코드, 엣지케이스 테스트, 문서 변환, 관찰 로그, 푸시 | — |
| 내장 스킬 | code-review · simplify · security-review · loop · schedule · dataviz | 내장 | 변경 리뷰, 주기 점검, 차트 | — |
| 에이전트 | Explore · Plan · general-purpose | 내장 (사용자 정의 에이전트 없음) | 넓은 탐색·설계. 사용자가 원할 때만 | 직접 Grep/Read |
| CLI | `gh`, `az`, `python`(3.11, pytest 9.1.1), `node` | PATH | Actions 확인, Azure | — |
| CLI | kubectl | `~/.azure-kubectl/kubectl.exe` (PATH 아님) | Pod 조회·로그 | — |

- 세션 시작 시 MCP 실패 목록이 있으면 `claude mcp list`로 다시 점검하고 `/mcp` Reconnect를 요청한다(Claude는 직접 재연결 불가).
- **Git Bash에는 `sed`·`grep`·`ls`·`claude`가 없다.** 셸 작업은 PowerShell, 검색은 Grep/Glob 도구로 한다.
- PowerShell에서 `claude mcp add`에 `--`를 넘길 땐 `'--'`처럼 따옴표로 감싼다.

## 1. 운영 정보 (조회는 바로, 변경은 승인)

- 클러스터 `portal-aks-prod`, 네임스페이스 `mzc-apps`, 배포 `kiwoom-bot`(라벨 `app=kiwoom-bot`).
- kubectl은 **한 명령씩 단순 형태**로 실행한다(변수·파이프로 묶으면 자동 모드에서 거부됨):
  `~/.azure-kubectl/kubectl.exe get pods -n mzc-apps` / `... logs <pod> -n mzc-apps --since=8h --tail=20000 > <scratchpad>\bot.log` 후 Grep.
- **승인 필요:** `rollout restart`, `set env`, `scale`, 배포 푸시, DB 쓰기, 키움 주문·토큰 발급.
- **로컬에서 키움 토큰 발급 금지.** 봇은 시작할 때만 토큰을 받고 `8005`(토큰 무효)여도 재발급하지 않는다(`async_kiwoom_client.py` `start`·`_execute_request`). 토큰이 무효면 Pod 재시작(승인 후)으로 복구한다.
- 모드: 코드는 `IS_REAL`/`KIWOOM_MODE`만 본다(`TRADING_MODE`는 무시). 현재 REAL.

### 운영 DB 조회 (MariaDB `kiwoom_quant_db`, SELECT만)

scratchpad에 `kq.py`를 만들어 쓴다 (SELECT/SHOW 외 거부):

```python
# 조회 전용: 인수로 받은 SQL을 차례로 실행해 출력
import os, sys, pymysql
from dotenv import load_dotenv
load_dotenv(r"C:/Users/MZC01-MICHAEL/Desktop/허동진/claude_project/kiwoom_rest_project/.env")
c = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15, read_timeout=300)
cur = c.cursor()
for q in sys.argv[1:]:
    if not q.lstrip().upper().startswith(("SELECT", "SHOW")):
        sys.exit("조회 전용: SELECT/SHOW만 허용")
    print("##", q); cur.execute(q)
    if cur.description: print([d[0] for d in cur.description])
    for r in cur.fetchall(): print(r)
```

실행: `$env:PYTHONUTF8=1; python -X utf8 <scratchpad>\kq.py "SELECT ..."` (UTF-8을 안 주면 cp949 오류).

함정:
- `logs.timestamp`는 KST, `order_history.timestamp`·`NOW()`는 **UTC**. `order_history`는 체결이 아니라 **주문 접수** 기록.
- `minute_ohlcv`(8GB+)는 code 조건 없이 집계하지 않는다. 크기는 `information_schema.tables`.
- 분봉 datetime은 14자리와 19자(9/8~10/7 실시간분)가 섞여 있다.
- `balance`는 날짜별 1행 upsert. 잔고 TR이 빈 응답이어도 덮어쓰는 버그가 있었다(10/8 17,205원 사례).

## 2. "확인해야 할 사항 확인해줘" — 점검 순서

모두 조회만. 결과는 표(항목·결과·근거)로 보고하고, ✅는 실측 근거가 있을 때만 붙인다.

1. **Git·배포:** `git status --short`, `git log --oneline origin/<branch>..HEAD`(미푸시), `gh run list -L 4`.
2. **Pod:** `get pods`(재시작 횟수·AGE), 필요 시 `get pod <p> -o jsonpath="{.status.startTime}"`.
3. **수집:**
   - 장 마감 수집 로그: `SELECT timestamp, message FROM logs WHERE message LIKE '장 마감 후 시세 수집 완료%' ORDER BY id DESC LIMIT 3` → 일봉·분봉이 0행이면 이상.
   - `SELECT date, COUNT(*) FROM daily_ohlcv WHERE date >= '<최근>' GROUP BY date` (거래일마다 약 2,589).
   - 투자자 동기화: `logs`에서 `투자자 순매수 동기화 완료%`, `investor_daily`의 종목 수·MAX(date).
4. **오류 묶음:** `SELECT level, LEFT(message,90) m, COUNT(*), MIN(timestamp), MAX(timestamp) FROM logs WHERE timestamp >= '<오늘>' AND level IN ('ERROR','CRITICAL','WARNING') GROUP BY level, m ORDER BY COUNT(*) DESC LIMIT 20`
   - 알려진 패턴: `8005` 토큰 무효, `509247` 파생상품 ETF 미신청 매수 반복, 같은 주문번호 취소 반복, `800033` 매도가능 0주 긴급 매도.
5. **토큰:** Pod 로그에서 `8005` 건수와 첫 발생 위치, `키움 수신 필드: {}`(빈 잔고 TR).
6. **매매·잔고:** `order_history` 오늘 매수/매도 건수(UTC 주의), `balance` 최근 3행, 로그의 `[예수금 정산]` 값과 비교.
7. **대기 중인 결정·인계:** 메모리의 키움 항목(모의 전환 결정, KODEX200 추세안, 외국인 결합 검증 단계)과 연결해 다음 할 일을 제안한다.

## 3. 변경 작업 절차

1. **조회·탐색:** 관련 코드는 Grep/Glob로 좁히고, TR 필드는 kiwoom-spec으로 확인한다. 추측한 필드명을 쓰지 않는다.
2. **표 보고:** 현재 동작(Before)과 바꿀 동작(After), 원인 근거(로그·DB 값), 수정할 파일과 코드 조각을 보여 준다.
3. **승인:** `AskUserQuestion`으로 범위를 받는다. 승인 전에는 수정하지 않는다.
4. **구현 (Ponytail·Karpathy):** 기존 함수 재사용, 표준 라이브러리 우선, 새 의존성 금지. 주석은 한국어.
   - 수집 실패·빈 응답일 때 0이나 가짜 값을 저장하지 않는다. 건너뛰고 로그만 남긴다.
   - 연구 설계(`research/*_plan.md`)는 결과를 보기 전에 고정한다. 결과를 보고 기준을 바꾸려면 날짜와 이유를 적고 "2차"로 분리한다.
   - 전략을 바꾸면 `kiwoom-backtest`로 변경 전후를 같은 조건에서 비교한다.
5. **검증:** `python -m pytest -q <관련 test_*.py>` (전체는 `python -m pytest -q`). 명령·종료 코드·출력 발췌를 적는다. 실행하지 못한 검증은 `미실행 (이유)`.
6. **리뷰:** `[🔍 작업 완료 자동 코드 리뷰]` — 핵심 변경, 방어 로직, 엣지 케이스, 남은 리스크를 최소 1개 검토한다. 주문·잔고·토큰 경로를 바꿨으면 `code-review`를 추가로 실행한다.
7. **기록:** `claude_project/WORK_HISTORY.md` 최상단에 `### [날짜] [kiwoom_rest_project] 제목` 형식으로 쓴다(일시, 목적·내용, 파일, 리뷰 요약, 검증 결과, 후속 할 일). `kiwoom_rest_project/WORK_HISTORY.md`는 10/5 이후 갱신이 멈춰 있다.

## 4. 커밋·배포

- `deploy.yml`은 `main`·`master`·`fix/**`·`feat/**` 푸시마다 **운영 배포(Pod 교체)**를 한다. 푸시가 곧 배포다.
- **배포 가능 시간:** 장 마감 후(15:35 이후) ~ 다음 날 08:30 전, 또는 휴장일. 장중 배포 금지(하루 손실 한도·진입 기록이 초기화됨).
- 재시작하면 야간 작업(투자자 동기화 18~08시, 분봉 백필)이 끊기지만 다음 라운드에서 이어받는다. 장 마감 수집은 그날 미수집이면 기동 직후 다시 실행된다.
- 이번 작업에서 수정한 파일만 `git add <파일>`로 올린다. 다른 작업의 미커밋 파일은 보고만 한다.
- 시맨틱 커밋 메시지(`fix(scope): ...`) 끝에 세션이 지정한 Co-Authored-By 줄을 붙인다.
- 푸시는 사용자 승인 후. 그다음 `gh run list --json databaseId,headSha`로 방금 커밋의 실행을 찾고 `gh run watch <id> --exit-status`로 확인한다.
- 배포 후 새 Pod 로그에서 `OAuth2 토큰 갱신 완료`, `[계좌 싱크/REAL]` 값, 오류가 없는지 확인한다.

## 5. 마무리

- 새로 알게 된 도구 동작이나 함정이 있을 때만 `task-observer` 로그와 메모리를 갱신한다.
- 최종 보고: 확인한 것과 확인하지 못한 것을 나눠 적고, 운영에 영향이 있는 다음 행동(재시작·배포·결정 대기)을 맨 앞에 둔다.
- **작업 하나가 끝날 때마다 응답 끝에 "남은 할 일" 표를 중요도 순으로 붙인다** (순위 · 할 일 · 이유/상태 · 결정 필요). 순서: ① 실거래·운영 장애·데이터 손실 위험 ② 날짜가 정해진 확인(다음 거래일 장중 확인 등) ③ 사용자 결정 대기 ④ 선택 개선. 방금 끝낸 일은 빼고 새로 생긴 일은 넣는다. 출처: 현재 대화, `WORK_HISTORY.md` 후속 할 일, 키움 메모리.
