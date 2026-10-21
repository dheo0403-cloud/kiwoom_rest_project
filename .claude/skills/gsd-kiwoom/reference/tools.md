# 키움 프로젝트 도구 현황 (2026-10-08 실측, 10-14 갱신, `claude mcp list`·디렉터리 확인)

도구 연결 문제·대체 경로가 필요할 때만 읽는다.

| 구분 | 이름 | 상태/위치 | 키움에서 쓰는 곳 | 실패 시 대체 |
|---|---|---|---|---|
| MCP | **kiwoom-spec** | √ 연결 (kiwoom_rest_project 로컬 등록, uv 실행) | TR 스펙·필드 의미: `spec_search` → `spec_show` → `get_example`. 키움 API는 호출하지 않음 | `C:\Users\MZC01-MICHAEL\tools\Kiwoom-REST-API` 문서 Grep |
| MCP | **context7** | √ 연결 (user scope) | aiohttp·aiomysql·pandas·Streamlit·FastAPI 문서 | WebFetch |
| MCP | repomix | √ 연결 (user scope) | 저장소 요약 (보통 Grep/Glob로 충분) | Grep/Glob |
| MCP | bigquery | √ 연결 (claude_project `.mcp.json`) | 키움에서는 안 씀 (DB는 MariaDB) | — |
| MCP | claude.ai Claude Docs / Mem0 | √ 연결 | 공유 문서를 요청받았을 때만 / 장기 기억은 파일 메모리 우선 | `MEMORY.md` |
| MCP | **claude-in-chrome** | √ 연결 (10/14 확인) | 사용자의 실제 Chrome으로 대시보드 확인 | Puppeteer(`verify-ui`) |
| MCP | GitHub | 홈 폴더 세션에만 등록 | — | `gh` CLI |
| 플러그인 | 없음 | marketplace 캐시·내장 `cc-plugin-diff`만 (10/14 확인) | — | — |
| 프로젝트 스킬 | **kiwoom-backtest** | `.claude/skills/kiwoom-backtest` | 전략 변경 전후 분봉 WFO·일봉 백테스트 | — |
| 사용자 스킬 | ponytail · karpathy-skills · agent-skills · markitdown · task-observer · github-deploy | `~/.claude/skills` 등 | 슬림 코드, 엣지케이스 테스트, 문서 변환, 관찰 로그, 푸시 | — |
| UI 스킬 | vercel-react-best-practices · frontend-design · redesign-existing-projects · web-design-guidelines | `~/.claude/skills` (`~/.agents/skills` 링크) | React 패턴, 화면 디자인·리디자인, UI 점검 | web-design-guidelines는 GitHub에서 가이드라인을 받아 오므로 인터넷이 필요함 |
| 동기화 스킬 | xlsx · pdf · deep-research · skill-creator · chrome-browser | claude.ai anthropic-skills | 백테스트 결과 엑셀 / 리포트 PDF / 여러 출처 조사 / 스킬 개선 / Chrome 사용 전 | — |
| AICompany 연동 | `AICompany/scripts/daily-check.mjs` | 키움팀이 `krx_holidays.json`과 운영 API(GET)를 읽음 | `/kiwoom/api/health` 등이 인증 없이 열려 있음(AICompany 할 일 C-8) | — |
| 내장 스킬 | code-review · simplify · security-review · loop · schedule · dataviz · Artifact | 내장 | 변경 리뷰, 주기 점검, 차트 | — |
| 에이전트 | Explore · Plan · general-purpose | 내장 | 넓은 탐색·설계. 사용자가 원할 때만 | 직접 Grep/Read |
| CLI | `gh`, `az`, `python`(3.11, pytest), `node` | PATH | Actions 확인, Azure | — |
| CLI | kubectl | `~/.azure-kubectl/kubectl.exe` (PATH 아님) | Pod 조회·로그 | — |

- 세션 시작 때 context7·repomix 등이 60초 시간 초과로 실패할 수 있다(10/14 사례, 다시 점검하니 √).
- MCP 실패 시 `claude mcp list`로 다시 점검하고 `/mcp` Reconnect를 요청한다(Claude는 직접 재연결 불가).
- PowerShell에서 `claude mcp add`에 `--`를 넘길 땐 `'--'`처럼 따옴표로 감싼다.
