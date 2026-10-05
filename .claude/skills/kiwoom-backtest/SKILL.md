---
name: kiwoom-backtest
description: 키움 자동매매 전략을 DB 분봉(minute_ohlcv) 실데이터로 롤링 WFO 백테스트하고 결과를 해석한다. 전략·지표·청산 규칙을 바꾸기 전후 비교, 실전 투입 판단, "백테스트 돌려줘", "전략 성과 확인", "손실 원인 분석" 요청 시 사용.
---

# kiwoom-backtest

`backtest.py`는 실거래와 같은 `strategy.py`(매수/매도 판정)와 `AsyncPortfolioManager.get_order_qty`(켈리 수량)를 그대로 호출한다. 전략을 바꿨다면 이 스킬로 바꾸기 전/후를 같은 조건에서 비교한다.

## 실행

```bash
# 단일 종목 (DB 조회 전용, 약 70일·2.3만 봉 기준 10~11분)
PYTHONIOENCODING=utf-8 python backtest.py 005930 --train-days 20 --test-days 5
# 실험 옵션: 일봉 ATR로 돌파선·스탑 계산 (기본 minute = 실거래와 동일)
PYTHONIOENCODING=utf-8 python backtest.py 005930 --atr-source daily
```

- 여러 종목은 백그라운드로 병렬 실행한다(프로세스당 1코어). 실행 중 코드를 고칠 예정이면 `backtest.py`를 scratchpad에 복사해 `PYTHONPATH=.`로 돌린다.
- stdout이 파일로 리다이렉트되면 끝날 때까지 로그가 비어 보인다(버퍼링). 정상이다.
- `.env`의 `DB_*`로 원격 MariaDB에 붙는다. SELECT만 한다. 쓰기 쿼리를 추가하지 않는다.
- 종목 후보 조회: `SELECT code, COUNT(DISTINCT LEFT(datetime,8)) d FROM minute_ohlcv WHERE LENGTH(datetime)=14 AND volume>0 GROUP BY code ORDER BY d DESC LIMIT 10`

## DB 분봉 함정

- `datetime`이 문자열이고 두 형식이 섞여 있다: 14자리 `20260827151900`(과거 수집분, 거래량 정상)과 19자 `2026-09-08 13:40:00`(실시간 버퍼 저장분). 로더는 숫자만 추출해 파싱한다.
- 2026-10-05 이전 실시간 저장분은 거래량이 전부 0이다(ka10001 거래량 키 `trde_qty` 미조회 버그, 커밋 4f74f15에서 수정). 로더가 거래량 0 봉을 제외하고 건수를 출력한다. 값을 채워 넣지 않는다.
- MIN/MAX(datetime)는 문자열 비교라 형식이 섞이면 틀린다. 기간은 파싱 후 계산한다.

## 결과 해석

- 출력: 폴드별 best_k·IS샤프·OOS 수익/거래/승률, OOS 합산(폴드 복리 수익, PF), 청산 사유별 건수·평균수익·총손익.
- 먼저 볼 것: PF(1 미만이면 손실 전략), 모든 폴드 IS샤프 부호(전부 음수면 k 조정으로 해결 안 됨), 손실이 몰린 청산 사유.
- 왕복 비용 약 0.37%(수수료·세금 0.21% + 슬리피지 0.08%×2). 평균 이익이 이보다 충분히 커야 한다.
- 편향을 결과와 함께 적는다:
  - 보수적: 청산 판정·체결을 봉 저가로 한다.
  - 낙관적: 체결강도·호가잔량·시장 레짐(KODEX200) 필터가 없다(분봉 OHLCV에 없음).
  - 선택 편향: DB에 많이 쌓인 종목은 거래대금 상위(관심종목) 종목이다.
  - 단일 종목·1포지션만 시뮬레이션한다.

## 실거래와의 차이

| 항목 | 실거래 | 백테스트 |
|---|---|---|
| 판정 시점 | 틱 폴링(0.3초 간격) | 1분봉 단위 (진입=종가, 청산=저가) |
| 관심종목 기준값 | 당일 포함 일봉 20개 | 전일까지 일봉 20개 |
| 동시 보유 | 최대 5종목 | 1종목 1포지션 |

## 검증

`backtest.py`를 바꾸면 `python -m pytest -q test_backtest.py`(스텁 전략으로 위임·봉 시각·체결가·폴드 구성 검증)를 실행한다.
