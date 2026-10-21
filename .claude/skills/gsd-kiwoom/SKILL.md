---
name: gsd-kiwoom
description: 키움 실전 자동매매 봇(kiwoom_rest_project·kiwoom DB·kiwoom-bot Pod) 전용 작업 절차 — 운영 점검, 버그 수정, 수집·매매 변경, 배포, 연구.
---

# gsd-kiwoom — 키움 실전 봇 전용 실행 절차

요청: `$ARGUMENTS`

이 봇은 **실계좌(REAL)로 주문을 낸다.** 조회는 바로 하고, 운영을 바꾸는 일은 모두 사전 승인을 받는다. 일반 규칙(한국어, 최소주의, 리뷰·WORK_HISTORY 형식)은 CLAUDE.md를 따른다. 도구 연결 실패·대체 경로가 필요하면 `reference/tools.md`를 읽는다.

**토큰 절약:** 아래 정보로 답이 되면 다시 조사하지 않는다. 파일은 Grep으로 좁혀 필요한 줄만 읽고, repomix·graphify는 구조를 처음 볼 때만 쓴다. UI 스킬은 화면을 바꿀 때만 불러 쓴다. 에이전트(Explore 등)는 사용자가 원할 때만 쓴다. Pod 로그는 파일로 저장한 뒤 Grep으로 본다(대화에 그대로 출력하지 않음).

## 1. 운영 정보

- 클러스터 `portal-aks-prod`, 네임스페이스 `mzc-apps`, 배포 `kiwoom-bot`(라벨 `app=kiwoom-bot`).
- kubectl(`~/.azure-kubectl/kubectl.exe`, PATH 아님)은 **한 명령씩 단순 형태**로 실행한다(변수·파이프로 묶으면 자동 모드에서 거부): `get pods -n mzc-apps` / `logs <pod> -n mzc-apps --since=8h --tail=20000 > <scratchpad>\bot.log` 후 Grep.
- **승인 필요:** `rollout restart`, `set env`, `scale`, 배포 푸시, DB 쓰기, 키움 주문·토큰 발급.
- **로컬에서 키움 토큰 발급 금지**(봇 토큰과 충돌). 키움 호출은 봇에서만.
- 모드: 코드는 `IS_REAL`/`KIWOOM_MODE`만 본다(`TRADING_MODE`는 무시). 현재 REAL.
- Git Bash에는 `sed`·`grep`·`ls`·`claude`가 없다 → 셸은 PowerShell, 검색은 Grep/Glob.

### 운영 DB 조회 (MariaDB `kiwoom_quant_db`, SELECT만)

`$env:PYTHONUTF8=1; python -X utf8 "<이 스킬 폴더>\scripts\kq.py" "SELECT ..."` (SELECT/SHOW 외 거부, UTF-8 안 주면 cp949 오류)

함정:
- `logs.timestamp`는 KST, `order_history.timestamp`·`NOW()`는 **UTC**. `order_history`는 체결이 아니라 **주문 접수** 기록.
- `minute_ohlcv`(8GB+)는 code 조건 없이 집계하지 않는다. 크기는 `information_schema.tables`.
- 분봉 datetime은 14자리와 19자(9/8~10/7 실시간분)가 섞여 있다.
- `balance`는 날짜별 1행 upsert(빈 잔고 TR로 덮어쓴 10/8 17,205원 사례).

## 2. "확인해야 할 사항 확인해줘" — 점검 순서 (모두 조회만, 결과는 항목·결과·근거 표)

1. **Git·배포:** `git status --short`, `git log --oneline origin/<branch>..HEAD`(미푸시), `gh run list -L 4`.
2. **Pod:** `get pods`(재시작·AGE).
3. **수집:** `logs`의 `장 마감 후 시세 수집 완료%` 최근 3건(일봉·분봉 0행이면 이상), `daily_ohlcv` 최근 날짜별 건수(약 2,589), `투자자 순매수 동기화 완료%`·`investor_daily` 종목 수·MAX(date).
4. **오류 묶음:** `SELECT level, LEFT(message,90) m, COUNT(*), MIN(timestamp), MAX(timestamp) FROM logs WHERE timestamp >= '<오늘>' AND level IN ('ERROR','CRITICAL','WARNING') GROUP BY level, m ORDER BY COUNT(*) DESC LIMIT 20` — 알려진 패턴: `8005` 토큰 무효, `509247` 파생 ETF, 같은 주문번호 취소 반복, `800033` 매도가능 0주.
5. **토큰:** Pod 로그의 `8005` 건수·첫 발생, `키움 수신 필드: {}`(빈 잔고 TR).
6. **매매·잔고:** `order_history` 오늘 매수/매도(UTC 주의), `balance` 최근 3행, 로그 `[예수금 정산]`과 비교.
7. **대기 중인 결정:** 키움 메모리(모의 전환, KODEX200 추세안, 외국인 결합 검증)와 연결해 다음 할 일 제안.

## 3. 변경 작업

1. 코드는 Grep/Glob로 좁히고, TR 필드는 kiwoom-spec으로 확인한다(추측한 필드명 금지).
2. Before/After 표(원인 근거: 로그·DB 값, 수정 파일·코드) → `AskUserQuestion` 승인 → 구현.
3. 수집 실패·빈 응답은 0이나 가짜 값으로 저장하지 않고 건너뛰며 로그만 남긴다.
4. 연구 설계(`research/*_plan.md`)는 결과를 보기 전에 고정한다. 바꾸려면 날짜·이유를 적고 "2차"로 분리. 전략을 바꾸면 `kiwoom-backtest`로 전후 비교.
5. 검증: `python -m pytest -q <관련 test_*.py>`. 주문·잔고·토큰 경로를 바꿨으면 `code-review` 추가.
6. 기록: `claude_project/WORK_HISTORY.md` 최상단 `### [날짜] [kiwoom_rest_project] 제목` (`kiwoom_rest_project/WORK_HISTORY.md`는 미사용).

### 프론트(`frontend/`, React 18 + Vite + Tailwind 3) 변경

필요한 스킬만 불러 쓴다. Next.js·서버 컴포넌트 규칙은 이 프로젝트에 해당하지 않는다.
- 컴포넌트 작성·수정, 리렌더링·번들 문제: `vercel-react-best-practices`
- 새 화면·레이아웃: `frontend-design`. 기존 화면을 개선할 때는 `redesign-existing-projects`. 기존 색·간격과 차트 라이브러리(Recharts·lightweight-charts)는 그대로 쓰고, 외부 이미지(picsum 등)·새 패키지는 넣지 않는다.
- 끝나면 `web-design-guidelines`로 바뀐 파일을 점검하고 `verify-ui`로 렌더링을 확인한다.

## 4. 커밋·배포

- `main`·`master`·`fix/**`·`feat/**` 푸시 = **운영 배포(Pod 교체)**.
- **배포 가능 시간:** 장 마감 후(15:35 이후) ~ 다음 날 08:30 전, 또는 휴장일. 장중 배포 금지.
- 재시작하면 야간 작업(투자자 동기화 18~08시, 분봉 백필)은 다음 라운드에서 이어받고, 장 마감 수집은 그날 미수집이면 기동 직후 다시 실행된다.
- 이번 작업 파일만 `git add` → 시맨틱 커밋 → 승인 후 푸시 → `gh run list --json databaseId,headSha`로 방금 커밋의 실행을 찾아 `gh run watch <id> --exit-status` → 새 Pod 로그에서 `OAuth2 토큰 갱신 완료`, `[계좌 싱크/REAL]`, 오류 확인.

## 5. 마무리 보고

- 운영에 영향이 있는 다음 행동(재시작·배포·결정 대기)을 맨 앞에. 확인한 것 / 확인하지 못한 것을 나눈다.
- **남은 할 일 (키움만):** 평소에는 **상위 3개 + "나머지 N건은 변동 없음"**만 쓴다. 사용자가 할 일을 물었거나 이번 작업으로 목록이 바뀌었으면 바뀐 항목을 자세히(무엇·왜·언제·확인 방법·결정 필요) 쓴다. 순서: 실거래·장애·데이터 손실 → 날짜 정해진 확인 → 결정 대기 → 선택 개선.
