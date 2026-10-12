---
name: kiwoom-cycle
description: 키움 봇 자기 개선 사이클(phil CYCLE.md 이식) — 미검토 일일 채점·주간 판정표 확인, 관찰 기록, 주간 계획표, 기준을 넘은 항목만 수정안→백테스트→승인→배포. 평일 키움 작업을 시작할 때, "리포트 확인", "주간 검토", "전략 회고", "채점 봐줘" 요청 시 사용.
---

# kiwoom-cycle — 채점 → 관찰 → (주간) 수정 사이클

기준 문서: `research/weekly_review_rules.md` (수치는 여기서만 읽는다. 이 스킬에 숫자를 복사하지 않는다).
실계좌 봇이다. 운영 변경·배포 규칙은 `gsd-kiwoom` 스킬을 따른다.

## 내 것 / 내 것이 아닌 것 (phil의 소유 구분)

| 구분 | 대상 | 규칙 |
|---|---|---|
| 고칠 수 있음 | `strategy_params.json` (허용 범위·1회 폭 안에서, 주 1개, 승인 후) | 근거 없는 수정 금지 |
| 기록할 수 있음 | `strategy_journal` 테이블 (`strategy_journal.py add`) | **승인 없이 기록 가능한 유일한 DB 쓰기** |
| 보호 영역 | `limits.py`, `test_limits.py`, `.github/` | 직접 고치지 않는다. 바꿔야 하면 `proposal`로 근거를 남기고 사용자가 결정. 사용자가 그 변경을 명시적으로 승인한 경우에만 커밋 메시지에 `[operator]`를 붙인다(CI `boundary.py`가 없으면 배포 중단). |
| 제안만 | 진입 시간대, 새 필터, 코드 로직, 한도 | `proposal` 기록 → 사용자 결정 |

## 실행 ($env:PYTHONUTF8=1; python -X utf8 …)

```
python -X utf8 strategy_journal.py unreviewed          # 미검토 REPORT·WEEKLY
python -X utf8 strategy_journal.py hypotheses          # 가설별 관찰일·기록 수
python -X utf8 strategy_journal.py list --days 14      # 최근 저널
python -X utf8 strategy_journal.py add observation <key> "<내용>" --evidence "<수치>"
python -X utf8 strategy_journal.py add review report "검토 완료" --ref <마지막 logs.id>
python -X utf8 skipped_signal_report.py [--days 20 | --weekly]   # 리포트 다시 계산(조회만)
```

## 절차

1. **미검토 확인:** `unreviewed`. 없으면 "미검토 없음" 한 줄로 끝낸다.
2. **일일 관찰 (REPORT마다):**
   - 실제 매수 vs 거른 신호, 손절 비율, 시간대 쏠림을 본다.
   - 기준 문서 §2대로 가설을 `observation`으로 기록한다(근거 수치 필수, 같은 가설 하루 1회).
   - 앞선 `decision`이 있으면 반영 후 경과일을 세고 §6 `followup` 시점이면 기록한다.
   - 전략은 고치지 않는다.
3. **주간 (WEEKLY가 있으면):**
   - `hypotheses`와 판정표로 **주간 계획표**를 만든다: 가설 | 관찰일 | 표본 | 차이 | 기준 충족(§3) | 이번 주 조치.
   - §7 전체 점검을 먼저 확인한다(4주 연속 음수면 규모 축소·중단·전략 교체를 먼저 제안).
   - 기준을 충족한 항목이 없으면 "이번 주 수정 없음"으로 끝낸다.
   - 충족 항목이 있으면 **1개만** 고른다 → §4 범위의 수정안을 `proposal`로 기록 → `kiwoom-backtest` 스킬로 전후 비교(§5) → 결과표를 사용자에게 보여 주고 승인 요청.
   - 승인: `strategy_params.json` 수정 → `pytest -q` → 장 마감 후 커밋·배포(gsd-kiwoom §4) → `decision` 기록.
   - 기각·백테스트 실패: `rejected`로 사유 기록. 같은 가설을 기준을 바꿔 다시 돌리지 않는다.
4. **검토 완료 표시:** `add review report "검토 완료" --ref <처리한 마지막 logs.id>`.
5. **보고:** 미검토 처리 건수, 새 관찰, 주간 계획표(있으면), 대기 중인 결정만. 수치는 리포트 그대로 인용한다.

## 하지 않는 것

- 하루·한 주 결과만으로 수정, 결과를 보고 기준 문서 변경(바꾸려면 "2차"로 날짜·이유), 두 개 이상 동시 변경.
- 보호 영역 직접 수정, 사용자 승인 없는 `[operator]` 표시, 장중 배포.
- `strategy_journal` 외 테이블 쓰기.
