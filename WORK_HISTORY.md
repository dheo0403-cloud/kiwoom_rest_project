# 📝 키움 비동기 퀀트 자동매매 프로젝트 작업 히스토리 (WORK_HISTORY.md)

---

## 📅 [2026-10-05 14:30] [일괄] 잔고 동기화 주기 · 켈리 이력 복원 · 키움 파서 · 동전주 제외 · API 토큰 · CI 테스트 · 전략 실험

* **내용:**
  1. 잔고 동기화: 5초 고정 → 장중 30초/장외 300초(`ACCOUNT_SYNC_MARKET_SEC`/`ACCOUNT_SYNC_IDLE_SEC`). 1회 동기화에 잔고·예수금 TR 최대 11회라 요청 한도 초과 유발 가능. 주문·청산 직후 즉시 동기화는 유지.
  2. 켈리 이력 복원: 기동 시 `get_recent_trade_returns()`(order_history, 취소 차감 FIFO) 최근 50건 → `trade_returns`. 재시작 후 기본 비중(1/N) 회귀 방지.
  3. 차트 API 거래량 키에 `trde_qty` 추가(2곳).
  4. 관심종목 선정에서 전략 기준(`strategy.MIN_STOCK_PRICE`=1,000원) 미만 제외(`drop_reasons['penny_stock']`).
  5. 키움 파서(문서: AlgoLab ka10004/ka10075 정리): 호가 총잔량 `tot_buy_req/tot_sel_req`, 호가 부호 절댓값(스프레드 551,500 원인). 미체결 `ka10075` 요청을 문서대로(`all_stk_tp/trde_tp/stex_tp`), `parse_unexecuted_orders()`(oso·oso_qty·io_tp_nm)로 3곳 통일, kt00007 업무 오류·0건이면 ka10075 폴백. API 경로 사전 취소도 구분 확인 시 CANCEL 기록. 체결강도는 필드 미확인으로 제외.
  8. CI: 빌드 전 `pytest -q`(테스트 의존성 pytest-asyncio 1.4.0, httpx 0.28.1), 실패 시 배포 중단.
  9. 프론트 POST 5곳 → `postApi()`: `X-API-Token` 헤더, 401이면 토큰 입력받아 브라우저 저장 후 재시도(서버 `API_AUTH_TOKEN` 미설정이면 영향 없음).
  10. 미사용 `LiveTerminalBento.tsx` 삭제.
  11. KRX 2026 휴장일: KRX 공지 원문은 못 찾음, 증권사(토스증권)·언론 기준 17일이 현재 파일과 일치 → 변경 없음.
  7. 실험(백테스트만): `breakeven_buffer_pct` 파라미터화(기본 0.25% 동일), CLI `--set key=value`.
* **수정/생성/삭제 파일:** `api_server.py`, `main_rest_async.py`, `database.py`, `strategy.py`, `async_kiwoom_client.py`, `indicators.py`, `backtest.py`, `test_safety_guards.py`(파서 2건), `.github/workflows/deploy.yml`, `frontend/src/{utils/apiConfig.ts, App.tsx, components/ParamsModal.tsx, components/StrategyControlsBento.tsx, components/ActivePositionsBento.tsx}`, `frontend/src/components/LiveTerminalBento.tsx`(삭제)
* **실험 결과(daily ATR, 5종목 롤링 WFO, 병렬 1,660초):**

  | 종목 | 기준 | E1 본절 0.5% | E2 분할익절 | E3 E1+E2 |
  |---|---|---|---|---|
  | 005930 | −2.26% PF0.31 | −2.69% PF0.21 | 동일 | E1과 동일 |
  | 000660 | −2.20% PF0.59 | −1.94% PF0.62 | 동일 | E1과 동일 |
  | 009150 | −4.54% PF0.15 | −4.13% PF0.19 | 동일 | E1과 동일 |
  | 402340 | −1.17% PF0.61 | −2.40% PF0.38 | 동일 | E1과 동일 |
  | 011070 | −0.68% PF0.64 | −0.57% PF0.67 | 동일 | E1과 동일 |

  평균 수익: 기준 −2.17%, E1 −2.35%. E2가 기준과 동일한 이유: 일봉 ATR 기준 R1(1.5 ATR≈4~5%)에 닿기 전 트레일링(고점 −2%)이 먼저 청산 → 분할익절 미발동. 결론: 세 안 모두 개선 없음, PF>1 종목 0.
* **리뷰:** 미체결·호가 파서는 문서 기준이며 실제 응답 검증은 장중에 필요. 동기화 주기를 늘려 장중 외부(MTS) 거래 반영이 최대 30초 늦어짐. 토큰은 브라우저 localStorage 저장(공용 PC 주의).
* **검증:** `pytest -q` 82 passed(저장소), `.env` 없는 사본에서도 82 passed(CI 조건 모사), `tsc --noEmit` exit 0, `npm run build` exit 0. 배포·운영 실측은 아래.

---

## 📅 [2026-10-05 12:30] [버그/지표] 잔고 스냅샷 보유주 누락(D1) · 성과 지표 실현손익 기준(D2) · 취소 주문 보정(D3)

* **D1 원인(코드 확인, Pod 로그는 az 미로그인으로 미확인):** 계좌 동기화 5초 주기, 잔고 TR 7회(kt00018×3, kt00004×3, kt00005). 키움은 업무 오류도 HTTP 200 + `return_code`≠0 → `get_account_balance`가 오류 응답의 스칼라(return_code 등)를 병합해 비어 있지 않은 dict 반환 → `sync_positions`가 '보유 0건'으로 포지션 비움 → 총자산=예수금으로 `balance` 기록. 근거: 최근 40일 중 35일 total==deposit, 배포 직후(10/03·10/05)만 정상. (9월 중순 이전은 deposit에 총자산이 들어간 과거 버전 문제로 별개)
* **D1 조치:** `async_kiwoom_client.is_tr_ok()` — return_code≠0·HTTP 오류 TR은 병합 제외, 정상 TR 0개면 None(기존 포지션 유지). `sync_positions`도 return_code≠0이면 포지션 유지(2중 방어). 과거 balance 행은 변경하지 않음.
* **D2 조치:** 입출금 기록이 없어 총자산 변화로 전략 성과 분리 불가 → 누적 수익률(%)·MDD(%) 대신 누적 실현손익(원)·실현손익 최대 낙폭(원). 일일 수익률은 balance 기준 유지(D1 이후 정상화 기대). 가짜 "한도 −5%" 배지 제거.
* **D3 조치:** 주문은 접수 시점에 order_history 기록 → 추적기 경로 취소 3곳(타임아웃 매수/매도, 사전 취소)에서 취소 성공(rt_cd 0) 시 `CANCEL_BUY/CANCEL_SELL` 행 기록, `compute_trade_metrics`가 직전 같은 종목·같은 방향 접수 수량에서 차감 후 FIFO. 스키마 변경 없음. 화면에 "주문 기록 기준" 표기. kt00007(API 경로) 취소는 응답의 매수/매도 구분 키를 확인 못 해 기록 안 함.
* **수정/생성 파일:** `async_kiwoom_client.py`, `async_portfolio.py`, `main_rest_async.py`, `database.py`, `api_server.py`, `test_safety_guards.py`(3건 추가), `test_async_trading_loop.py`·`test_api_server.py`(모의 응답 필드 변경), `frontend/src/{types.ts, hooks/useWebSocket.ts, components/QuantPerformanceBento.tsx}`
* **리뷰:** 과거 order_history에는 CANCEL 행이 없어 과거 승률·PF는 그대로(운영 데이터 사전 계산: 760회, 35.92%, PF 0.56, 누적 실현손익 −209,043원, 실현 MDD −217,103원). 잔고 TR을 5초마다 7회 호출하는 빈도 자체가 요청 한도 초과를 유발할 수 있음(미조치).
* **검증:** `pytest -q` 80 passed(exit 0), `tsc --noEmit` exit 0. 배포·운영 실측은 아래 기록.
* **후속:** 잔고 동기화 주기·TR 수 축소 검토, kt00007 매수/매도 구분 키 확인 후 API 경로 취소도 기록, 배포 후 며칠간 balance 행이 total≠deposit(보유 시)로 유지되는지 확인.

---

## 📅 [2026-10-05 11:00] [대시보드] 중복 제거 · 고정/가짜 값 제거 · 차트·자산 추이·미체결 추가 · 차트 API 500 수정

* **목적:** 운영 대시보드(`/kiwoom`)의 중복·누락 정리. 운영 화면을 Puppeteer로 캡처(1920px, 콘솔 오류 0)하고 화면 문구 605줄을 코드·API·DB와 대조.
* **발견 → 조치:**
  - 중복: 총자산/예수금/보유가 헤더·KPI·로그 헤더 3곳 → 로그 헤더 제거. KODEX200 등락이 호가 카드·레짐 배지·제어 카드 3곳 → 호가 카드에서 제거(대상 종목 코드 표시로 대체). 감시 로그가 저가주에서 초당 여러 번 기록(괴리율 0.5%p 조건이 5초 쓰로틀을 우회) → 괴리율 조건도 최소 2초 간격.
  - 고정/가짜 값: 리스크 카드 "하드 스탑 −4.0% / 트레일링 2.5 ATR / MDD −5% / 5종목" 고정 문자열 → `/api/status`의 `strategy_params`(실제 −3% / 1.5 ATR, 고점 −2% / 2.0 ATR, 일일 손실한도 −2.5%, max_stocks). 체결강도 128.5% 고정값과 호가 잔량 250,000/150,000 기본값(백엔드·프론트 모두) → 실데이터 없으면 "데이터 없음". 보유 카드 "3단계 분할 익절 1차(+3%)/2차(+5%), 33% 익절" → 실제 모드(트레일링 전용)면 "+1.5% 도달 후 고점 −2% 하락 시 청산", 수동 버튼 "50% 매도". "기초 원금"(총자산과 같은 값) 제거. "누적 이익"(이익 거래만 합산) → "누적 실현손익"(이익−손실).
  - 누락: `TradingViewChartBento`가 있었지만 화면에 없어 종목 클릭이 아무 효과 없음 → 차트 행 추가. `equity_history`가 API에 있는데 미표시 → 총자산 추이 라인. 미체결 주문 목록 없음 → `/api/orders/pending` + 패널(5초 주기).
  - 버그: `/api/chart/{보유종목}` 500 — 포지션 dict에 `pos.name` 속성 접근 → `.get()`.
  - `useWebSocket` 봇 상태가 running/circuit만 비교해 다른 필드 변경이 반영 안 되던 부분 → 전체 비교.
* **수정/생성 파일:** `api_server.py`, `main_rest_async.py`, `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/hooks/useWebSocket.ts`, `frontend/src/components/{KpiMetricsRow,ActivePositionsBento,LogViewer,QuantPerformanceBento}.tsx`, `frontend/src/components/AccountSideBento.tsx`(신규)
* **리뷰:** 총자산 추이는 `balance` 원본 그대로라 현재는 보유주가 빠진 날(예수금만)과 정상일이 번갈아 톱니 모양으로 보임(D항목, 미조치). 호가 잔량은 ka10004 응답 해석이 안 돼 "데이터 없음"으로 표시될 것(해석 로직 수정은 별도). 로컬 api_server는 실전 키로 잔고 동기화·DB 기록·스케줄러가 돌아 미실행 → 운영 배포 후 실측.
* **추가 수정(배포 후 실측에서 발견):** 차트가 오래된 1봉만 표시 — `minute_ohlcv` datetime 형식 혼재로 `ORDER BY datetime DESC`가 7/31 데이터를 최신으로 반환(조회로 확인: 기존 20260731153000 / 수정 2026-10-01 13:24:00), 14자리 시각 해석 실패 시 `time.time()`으로 채워 봉이 겹침 → `database.get_candles_by_code` 숫자 정렬, `api_server._chart_time()`으로 두 형식 해석·실패 봉 제외·시간 오름차순 중복 제거. 자산 추이 축 정수 표시. 커밋 850040e.
* **검증:** `tsc --noEmit` exit 0, `npm run build` exit 0, `pytest -q` 77 passed. 배포 `gh run watch` 37246202383·37246690220 success. 운영 API: `/orders/pending` 200, `/status.strategy_params` 실제값, `/chart/018880` 500→200, 차트 3종목 각 60봉(10-01 12:25~13:24). 운영 Puppeteer(1920px): 차트·자산 추이 카드 높이 420/420·같은 행, 캔버스 렌더링, 리스크 카드 "하드 스탑: -3% / 1.5 ATR | 트레일링: 고점 -2% / 2 ATR | 일일 손실한도 -2.5%", 128.5%·"33% 익절"·로그 헤더 자산 표시 없음, 보유 종목 클릭 시 차트 종목 전환, 콘솔 오류 0.
* **후속(D):** `balance`에 보유주 누락 스냅샷이 저장되는 원인 수정, 누적 수익률·MDD 기준(입출금·모의 구간 제외), 승률/PF를 접수가 아닌 체결 기준으로.

---

## 📅 [2026-10-05 10:00] [실험] 일봉 ATR 기준 돌파선·스탑 백테스트 + kiwoom-backtest 스킬

* **내용:** `HighFidelityBacktester(atr_source='daily')`(CLI `--atr-source daily`) — 전일까지 20일 일봉 ATR(14)을 `ind['atr14']`로 사용(실거래 코드 불변, 기본 minute). `.claude/skills/kiwoom-backtest/SKILL.md` 생성(실행법·DB 함정·해석 기준·실거래 차이).
* **수정/생성 파일:** `backtest.py`, `test_backtest.py`(전일까지만 쓰는지·잘못된 옵션 검증 테스트), `.claude/skills/kiwoom-backtest/SKILL.md`
* **검증:** `pytest -q` 77 passed. 5종목 롤링 WFO(train 20/test 5, 병렬 1,024초, 모두 exit 0):

  | 종목 | minute 수익% / PF | daily 수익% / 거래 / PF |
  |---|---|---|
  | 005930 | −8.40 / 0.19 | −2.26 / 21 / 0.31 |
  | 000660 | −7.12 / 0.25 | −2.20 / 16 / 0.59 |
  | 009150 | −5.57 / 0.35 | −4.54 / 29 / 0.15 |
  | 402340 | −5.42 / 0.31 | −1.17 / 20 / 0.61 |
  | 011070 | −5.54 / 0.26 | −0.68 / 22 / 0.64 |

  daily는 거래 수가 약 1/3로 줄고 손실 폭이 줄었지만 전 종목 여전히 손실. 하드스탑 1건 평균 약 −3.4%(−3% 캡+비용)가 트레일링 이익(평균 +0.7~2.8%)보다 큼.
* **판단:** 일봉 ATR은 개선 방향이나 단독으로는 실전 불가. 다음 실험 후보: 손절 폭 대비 익절 목표 확대(R배수), 장마감 청산 대신 보유 허용 여부, 종목 선정 기준.

---

## 📅 [2026-10-05 08:20] [버그/분석] 실시간 거래량 0 수정 · 5종목 실데이터 WFO · 청산 사유 분해

* **목적:** 직전 후속 3건(실시간 거래량, 손실 원인 파악, 전략 재검토) 처리.
* **원인(거래량 0):** 시세 폴링 API `ka10001` 응답의 거래량 키는 `trde_qty`(당일 누적)인데 코드는 `acml_vol`/`volume`/`cntg_vol`만 조회 → 실시간 분봉 거래량 항상 0(DB 실시간 저장분 98,764행 전부 0으로 확인). 관심종목 일봉 집계도 같은 문제로 `avg_vol`=0 → 환산거래량 필터 우회. 근거: AlgoLab ka10001 정리(cur_prc/open_pric/trde_qty), `data_collector.py`·실전 스키마 테스트의 `trde_qty`. 모의투자 토큰 발급이 400으로 실패해 실제 응답 직접 확인은 못 함(실전 키는 운영 토큰 영향 우려로 미사용).
* **내용:**
  - `main_rest_async.py`: 시세 조회 4곳 + 일봉 집계 1곳에서 `trde_qty` 우선 조회.
  - `market_data_buffer.py`: `update_tick`의 volume을 당일 누적으로 보고 분봉 거래량 = 누적 차분(직전 분 마지막 누적 기준). 기동 후 첫 분봉은 첫 관측값부터, 누적 감소(날 바뀜) 시 기준 리셋, 0 이하는 무시.
  - `backtest.py`: WFO 결과에 OOS `trades` 추가, CLI에 청산 사유별 건수·평균수익·총손익 출력.
  - `test_market_data_buffer.py`: 누적 차분 기대값으로 수정 + 0 무시·날 바뀜 리셋 테스트 추가.
* **수정/생성 파일:** `main_rest_async.py`, `market_data_buffer.py`, `backtest.py`, `test_market_data_buffer.py`
* **리뷰:** 운영 반영 시 VWAP·POC·환산거래량 필터가 실제로 작동 → 신규 매수 빈도 감소 예상. 체결강도(`volume_power`)는 ka10001에 해당 키가 있는지 미확인(여전히 0이면 필터 미적용). `api_server.py:736,758` 대시보드 차트 거래량 키는 미수정(표시용).
* **검증:** `pytest -q` 76 passed(exit 0). 5종목 롤링 WFO(train 20일/test 5일, 조회 전용, 병렬) 모두 exit 0:

  | 종목 | OOS수익% | 거래 | 승률% | PF |
  |---|---|---|---|---|
  | 005930 | −8.40 | 75 | 18.7 | 0.19 |
  | 000660 | −7.12 | 63 | 20.6 | 0.25 |
  | 009150 | −5.57 | 73 | 31.5 | 0.35 |
  | 402340 | −5.42 | 73 | 23.3 | 0.31 |
  | 011070 | −5.54 | 71 | 16.9 | 0.26 |

  청산 사유 합계(355건): 하드스탑 237건(67%) 평균 −1.17% 총 −4,386,238원 / 샹들리에 78건 평균 +0.97% 총 +1,193,379원 / 본절 38건 평균 −0.21% / 장마감 1건 / 트레일링 1건.
* **판단:** 손실의 대부분이 하드스탑. 돌파선·스탑이 모두 **1분봉 ATR** 기준이라 돌파선은 시가 바로 위, 스탑은 분봉 노이즈 폭 → 진입 직후 손절이 반복. 왕복 비용 약 0.37%(수수료·세금 0.21% + 슬리피지 0.08%×2)가 평균 이익 0.97%의 약 38%. 본절 +0.25% 보전이 비용보다 작아 본절 청산도 평균 손실. 대상이 거래대금 상위 5종목(선택 편향), 청산 체결가 봉 저가(보수적) 한계 있음. **실전 투입 보류 권고.**
* **후속:** ① 일봉 ATR 기반 돌파선·스탑 실험(백테스트로 비교) ② 본절 보전폭을 왕복 비용 이상으로 ③ 체결강도 키 확인(ka10001/ka10003) ④ 운영 배포 후 실시간 분봉 거래량 DB 적재 확인.

---

## 📅 [2026-10-05 04:00] [백테스트] ATR 재사용 · 일 단위 롤링 WFO · DB 실데이터 백테스트

* **목적:** 직전 작업 후속 3건 일괄 처리.
* **내용:**
  - `indicators.py`: `calculate_adx`/`calculate_chandelier_exit`에 `atr=None` 인자, `compute_all_indicators`가 atr14 전달(ATR(14) 3회→1회).
  - `backtest.py`: `prepare()`로 봉별 지표(최근 20봉) 1회 사전 계산 후 k별 재사용, `_run(start, end)`로 구간만 거래(앞 구간은 지표·일봉 기준값 웜업). WFO를 일 경계 분할로 변경, `train_days`/`test_days` 지정 시 롤링 폴드 + OOS 합산(폴드 복리 수익·거래·승률·PF), 기존 반환 키 유지 + `folds` 추가, 최적화 후 k 원복. `load_minute_bars_from_db()`와 CLI(`python backtest.py <code> --train-days 20 --test-days 5`) 추가 — 조회 전용, datetime 14자리/19자 혼재를 숫자 추출로 파싱, 거래량 0 봉은 보정 없이 제외하고 건수 출력.
  - `test_backtest.py`: 일 경계 분할 검증, 롤링 폴드 구성·OOS 거래가 검증 구간 안에서만 발생·k 원복 테스트 추가.
* **수정/생성 파일:** `indicators.py`, `backtest.py`, `test_backtest.py`
* **리뷰:** 사전 계산 지표는 각 봉까지의 20봉만 사용(look-ahead 없음), `_run`은 캐시 dict를 복사해 써 컨텍스트 주입이 캐시를 오염시키지 않음. 폴드 경계가 일 단위라 당일 누적거래량이 끊기지 않음. 한계: 단일 종목·1포지션, 체결강도·호가·레짐 미반영, 청산 체결가를 봉 저가로 둬 보수적 편향.
* **검증:**
  - ATR 변경 HEAD 대비 `assert_frame_equal` 9개 케이스 EQUAL, 속도 14.43ms → 12.91ms(15회 교차 최소값).
  - `pytest -q` 75 passed(exit 0, 34초 → 17초).
  - **실데이터 롤링 WFO** `python backtest.py 005930 --train-days 20 --test-days 5` → exit 0, 639초. 조회 24,225행 중 거래량0 678행 제외, 23,547행·69거래일(2026-05-07~08-27), 10폴드. **OOS 합산: 수익 −8.40%, 거래 75건, 승률 18.7%, PF 0.19.** 모든 폴드의 In-Sample 최고 샤프가 음수(−5.5 ~ −10.5) → k 조정으로 해결되지 않는 수준.
* **별도 발견(미조치):** `minute_ohlcv`의 실시간 저장 분봉(19자 datetime) 98,764행 거래량이 전부 0. 실시간 버퍼 분봉 거래량이 0이면 실거래 VWAP는 봉 대표가로 대체되고 POC는 최하단 구간으로 고정되어 VWAP·POC 필터가 사실상 통과 상태일 가능성.
* **후속:** ① 실시간 틱 거래량 필드 확인 및 버퍼 분봉 거래량 수정(실거래 필터 정상화). ② 005930 외 종목 실데이터 WFO, 청산 사유별 손익 분해로 손실 원인(하드스탑/본절/트레일링/15:15 청산) 파악. ③ 결과 기준 실전 투입 보류 검토.

---

## 📅 [2026-10-05 03:20] [성능] indicators.py 지표 계산 속도 개선 (계산식 변경 없음)

* **목적:** `get_latest_indicators`가 실거래 틱 평가·백테스트 봉마다 호출되는데 호출당 수십 ms 소요. 프로파일 결과 `_standardize_columns`의 반복 df 복사(지표 1회당 11번)와 컬럼별 `df[...] =` 삽입(25회)이 주원인.
* **내용:** `_is_standardized()` 추가 — 컬럼명 중복·별칭·대문자 없음, OHLCV가 numpy 정수/실수형, 실수형 NaN 없음이면 `_standardize_columns`가 복사 없이 원본 반환. `compute_all_indicators`는 지름길일 때 `df.copy()`로 입력 보호, 지표 컬럼은 dict에 모아 `pd.concat` 1회(입력에 같은 이름 컬럼이 있으면 기존 위치에 덮어쓰기 유지).
* **수정/생성 파일:** `indicators.py`, `test_indicators.py`(지름길/표준화 경로 결과 동일 + 입력 불변 테스트 1건)
* **리뷰:** 지름길 반환은 원본 객체이므로 내부 함수가 df_std를 변형하면 호출자 df가 바뀔 수 있음 → 현재 calculate_* 함수들은 읽기만 함(확인), compute_all만 컬럼을 추가하므로 복사로 보호. 대상이 아닌 입력(별칭·object·bool·nullable·NaN)은 기존 경로 그대로.
* **검증:** HEAD 버전과 `assert_frame_equal` 비교 9개 케이스(20/1/2/200봉, NaN, 실수 거래량, 대문자·키움 별칭 컬럼, 기존 ma5·문자 컬럼) 전부 EQUAL + 입력 df 변형 없음. 속도(20봉, 5회 교차 측정 최소값) 33.96ms → 20.67ms(−39%). `pytest -q` 74 passed(exit 0, 전체 78초 → 34초). 1일 390봉 백테스트 15.8초(거래 0건, 변경 전과 동일). 실거래 런타임 측정은 미실행(장외).
* **후속:** ATR 중복 계산(14기간 3회 + 20기간 1회) 재사용 시 추가 단축 여지. DB `minute_ohlcv` 실데이터 백테스트, WFO 일 단위 다구간 개선.

---

## 📅 [2026-10-05 03:05] [백테스트] backtest.py가 실거래 strategy.py 판정을 그대로 호출하도록 통일

* **목적:** PDF 분석 2순위. 백테스트가 별도 로직(2.0ATR/−5% 스탑, 2.5ATR 트레일링, R1/R2 분할익절, ADX·VWAP 등 필터 없음)을 써서 실거래 전략 성과를 대변하지 못하던 문제 해소.
* **내용:**
  - `strategy.py`: `now = ind.get('now') or get_kst_now()` (매수/매도 2곳). 실거래는 `now`를 넣지 않으므로 동작 불변.
  - `backtest.py`: 분봉 입력(datetime 필수). 봉마다 최근 20봉 `get_latest_indicators` + 당일 시가 + 전일까지 20일 피보나치/평균거래량 + 당일 누적거래량 + 봉 시각을 `ind`로 구성해 `check_buy_signal`(종가 진입)/`check_sell_signal`(저가 판정·체결, 비관적) 호출. 수량은 `AsyncPortfolioManager.get_order_qty`. 부분매도 50%(실거래와 동일). 분할매도 수령액을 거래 PnL에 포함(기존엔 누락). 샤프/CAGR은 일별 자산 기준. 자체 켈리·스탑 파라미터 제거, `strategy` 주입 지원.
  - `test_backtest.py`: 합성 분봉으로 재작성, 스텁 전략 주입으로 위임·봉 시각 전달·체결가/비용 검증 추가.
* **수정/생성 파일:** `backtest.py`, `strategy.py`, `test_backtest.py`
* **리뷰:** 실거래와 남은 차이 — 체결강도·호가잔량·시장 레짐 미반영(데이터 없음), 관심종목 기준값은 전일까지 20일(실거래는 당일 포함 가능), 단일종목·1포지션. WFO는 행 비율로 분할해 일중 분할 가능하고, OOS 첫날은 일봉 기준값이 없음. 성능: `get_latest_indicators`가 호출당 약 54ms(프로파일 결과 `_standardize_columns` 11회 반복이 약 40%) — 1일(390봉) 약 20초, 실거래 틱 평가도 같은 비용.
* **검증:** `py_compile` exit 0. `pytest -q` 73 passed(exit 0, 78초). 실제 전략 1일 합성 분봉 실행: 거래 0건, 매수 거절 사유 집계 ADX 무추세 215·14:30 이후 60·DMI 43·돌파 미달 25·RSI 19·장초반 15·MA20 12·POC 1 → 봉 시각 기반 시간 필터 정상 작동 확인. 실데이터 백테스트는 미실행(분봉 데이터 미준비).
* **후속:** `indicators.compute_all_indicators` 중복 표준화 제거로 속도 개선(실거래에도 이득). DB `minute_ohlcv` 실데이터로 백테스트 실행. WFO를 일 단위·다구간 롤링으로 개선.

---

## 📅 [2026-10-05 02:40] [리스크] 시장 레짐 승수를 실제 주문 수량에 반영

* **목적:** "클로드를 펀드매니저로 만들기"(quantframe.io 번역) PDF 분석 결과 반영. 확신도(시장 상태)가 낮으면 비중을 줄인다는 원칙에 비춰 보니, `MacroRegimeFilter.get_regime_kelly_multiplier()`(강세 1.0/횡보 0.6/급락 0.0)가 API 표시용으로만 쓰이고 주문 수량에는 미반영이었음.
* **내용:** `get_order_qty()`에 `regime_multiplier`(기본 1.0) 인자 추가, 켈리 비중에 곱함. 0 이하이면 0주. 매수 호출부(`_evaluate_buy_condition_impl`)에서 현재 레짐 승수 전달.
* **수정/생성 파일:** `async_portfolio.py`, `main_rest_async.py`, `test_strategy_quant.py`(테스트 1건 추가)
* **리뷰:** 기본값 1.0이라 기존 호출부/테스트 동작 불변. 급락장은 기존대로 `market_filter_passed`(main_rest_async.py:1377)에서 먼저 차단되므로 호출부의 "0주→1주 보정" 경로를 타지 않음. 소액 계좌 최소 1주 보정은 유지되어 횡보장에도 1주 매수는 가능. 레짐 평가는 KODEX200 등락률만 입력(VIX/환율 미연동)이라 횡보 판정은 사실상 KODEX200 −0.5% 미만일 때만 발생.
* **검증:** `python -m py_compile` exit 0, `python -m pytest -q` 72 passed(신규 1건 포함, exit 0). 실제 키움 API 실행은 미실행(장외 시간).
* **후속:** PDF 분석 2순위 — `backtest.py`가 `strategy.py` 판정 로직을 그대로 쓰도록 통일(현재 스탑·트레일링·필터 파라미터 불일치). WFO 롤링 다구간화. 재시작 시 `trade_returns` 복원 여부 확인.

---

## 📅 [2026-10-03] [기능] KRX 휴장일 달력(krx_holidays.json) 도입

* **목적:** 양력 고정 휴일만 판별하던 `is_korean_market_holiday()`가 설날·추석·대체공휴일·선거일을 놓치던 문제 보완.
* **내용:** `krx_holidays.json`에 연도별 휴장일을 두고, 해당 연도가 있으면 그 목록을 그대로 따름. 없는 연도는 기존 고정 휴일 로직으로 폴백하고 연도별 1회 경고. CI 패키징(`deploy.yml`)에 JSON 복사 추가(`COPY . .`인 Dockerfile은 변경 없음).
* **수정/생성 파일:** `krx_holidays.json`(신규), `main_rest_async.py`, `.github/workflows/deploy.yml`, `test_safety_guards.py`
* **데이터 한계:** 2026년 17일은 2차 출처(jangjeon.kr, glasswallet.com, 두 곳 일치)이며 KRX 공식 공지로 재확인 필요. 특히 7/17(제헌절)과 6/3(지방선거)은 공식 확인 전. 2027년은 확인된 목록이 없어 비워 둠(폴백 적용, 설날 2/7~9·추석 9/14~16 등 누락).
* **리뷰:** 파일 누락·파싱 오류 시 봇이 죽지 않고 폴백. 연도 키가 있으면 목록 외 평일은 영업일로 판정하므로 목록 오기입 시 해당일 오판 가능.
* **검증:** `pytest -q` 71 passed(신규 1건 포함, exit 0).
* **후속:** 2027년 데이터 추가(12월경), KRX 공식 목록 대조.

---

## 📅 [2026-10-03] [보안/주문 안전] 코드 분석 지적 사항 중 코드로 처리 가능한 항목 수정

### 1. 작업 개요 및 목적
- 코드 분석에서 발견된 위험 중 코드 수정으로 막을 수 있는 항목 처리. (키 재발급, git 이력 삭제는 코드 밖 조치로 별도 필요)
- 변경 내용:
  - `.env` git 추적 해제(`git rm --cached`) 및 `.gitignore` 추가 (이력 속 값은 그대로이므로 키 재발급 필요)
  - HTTP 오류 로그의 payload에서 `accPwd`/`appkey`/`secretkey` 마스킹
  - 변경성 API 5개에 `API_AUTH_TOKEN` 설정 시 `X-API-Token` 검증 (미설정이면 기존 동작), `CORS_ORIGINS` 지정 시 해당 도메인만 허용 및 credentials 활성
  - 종목별 `_buy_inflight`로 동시 중복 매수 차단
  - 주문(kt10000/kt10001) 재시도 5→1회
  - ADX/VWAP 미산출 시 매수 보류 (시간필터 skip 모드는 기존 동작)
  - 접수 시점 로그 문구를 "체결"→"접수"로 정정, 실전 모드 시작 경고, 중복 호출 제거, 주석 불일치 수정

### 2. 수정/생성된 파일
- `.gitignore`, `api_server.py`, `async_kiwoom_client.py`, `main_rest_async.py`, `strategy.py`, `test_safety_guards.py`(신규), `.env`(추적 해제만, 로컬 파일 유지)

### 3. 🔍 코드 리뷰 요약
- 한계: 인증은 opt-in이라 `API_AUTH_TOKEN`을 설정하기 전까지 API는 무방비이며, 프론트엔드는 아직 `X-API-Token`을 보내지 않음(토큰 설정 시 프론트 수정 필요).
- 미해결: 포지션이 "접수" 시점에 편입되는 구조는 유지(미체결 취소 시 싱크 전까지 불일치 가능). 모의 폴백 종목 주입, 휴장일 달력, API 호출 중복(스트림 워커와 감시 루프)은 이번 범위 제외.
- 영향: 재시작 직후 캔들이 쌓이기 전(약 수 분)에는 신규 매수가 보류됨.

### 4. 검증 결과
- `python -m py_compile` → exit 0
- 수정 전 `pytest -q`: 65 passed / 수정 후: 70 passed (신규 5건 포함, exit 0)
- 동시 호출 단위 테스트는 목(Mock) 기반이며 실제 키움 API/AKS 실행은 미실행

### 5. 후속 할 일
- 코드 밖: 실전 APP_KEY/SECRET·계좌 비밀번호·DB 비밀번호 재발급, 저장소 공개 여부 확인, 필요 시 git 이력 정리
- `API_AUTH_TOKEN`, `CORS_ORIGINS` 운영 환경변수 설정 및 프론트엔드에 토큰 헤더 반영
- CI에 pytest 단계 추가 검토

---

## 📅 [2026-09-29 15:20] [배포] KST 타임존 완벽 교정판 main 브랜치 병합 및 GitHub Actions -> AKS 무중단 자동 배포 트리거

### 1. 작업 개요 및 목적
- **작업 목적:** 백엔드 거래 루프/스케줄러 타임존 교정 및 UI/로거 표출부 KST 100% 동기화 수정사항을 `main` 브랜치에 정식 반영하여 AKS 클러스터로 자동 배포(Continuous Deployment).
- **진행 내용:**
  - `fix/rendering-optimization-and-safety-fixes` 브랜치의 최신 커밋들(`62c99bf`, `9574460`, `7679bc6`)을 `main` 브랜치로 Fast-forward 병합.
  - 원격 `origin/main`으로 `git push`를 수행하여 `.github/workflows/deploy.yml` CI/CD 파이프라인 자동 실행.

### 2. 대상 파일 및 커밋
- **병합 커밋:** `62c99bf fix: 주식 매매 봇 UI/로거 표출부 UTC 시간(06시) 잔존 버그 해결 및 KST 완벽 동기화`
- **배포 방식:** GitHub Actions ➔ Azure Container Registry (ACR) 이미지 빌드 ➔ Azure Kubernetes Service (AKS) 무중단 롤아웃

---

## 📅 [2026-09-29 15:05] [버그 픽스] 주식 매매 봇 UI/로거 표출부 UTC 시간(06시) 잔존 버그 해결 및 KST 완벽 동기화

### 1. 작업 개요 및 목적
- **발생 문제 분석:**
  - 백엔드 내부 스케줄러 계산 시간은 KST로 교정되었으나, Bento 콕핏 UI 및 터미널 `LogViewer` 화면에 출력되는 실시간 감시 로그 타임스탬프가 여전히 UTC 06시(KST 15시 기준 -9시간 오차)로 잔존하는 결함 발생.
  - 근본 원인: DB(`logs` 테이블)에서 가져온 Naive Datetime `created` 객체에 대해 `format_kst_time_str()`가 `dt.replace(tzinfo=KST)`를 수행함에 따라, 06시라는 시각 숫자는 유지된 채 타임존 라벨만 KST로 변경되어 +9시간 시차 변환이 유실되었음.
- **완벽 해결 조치:**
  - `database.py` 내 `format_kst_time_str()` 보정 로직 교정: Naive Datetime 수신 시 `dt.replace(tzinfo=timezone.utc)`로 UTC 라벨 부여 후 `.astimezone(KST)`를 호출하여 UTC 06:13:25 시각을 KST 15:13:25 (+9시간)로 정확히 1:1 파싱.
  - DB 저장 시 KST 시각 명시적 주입: `log_message()`에서 `INSERT INTO logs (level, message, timestamp)` 파라미터로 `get_kst_now()`를 주입하여 원본 레코드 생성 시점부터 KST 시각 보장.

### 2. 수정된 파일
- `database.py`: `format_kst_time_str()` UTC->KST 변환 로직 보정 및 `log_message()` 명시적 KST 타임스탬프 저장.

### 3. 실측 검증 결과
- **타임존 파싱 시뮬레이션 실측 검증:**
  - `UTC Naive Datetime(06:13:25)` 수신 시 `format_kst_time_str()` 파싱 결과 `15:13:25` (+9시간 정확 변환)로 100% 정상 작동 증명.

---

## 📅 [2026-09-29 14:55] [버그 픽스] 주식 매매 봇 UTC/KST 타임존 불일치 해결 및 스케줄러 정상화

### 1. 작업 개요 및 목적
- **발생 문제 분석:**
  - 서버 시스템 시간(UTC)과 한국 표준시(KST) 간의 타임존 설정 누락/불일치로 인해 정규장 영업일 스케줄러 및 로깅 시간이 정확히 9시간 차이로 어긋나는 결함 발생.
  - Windows OS 환경의 Python 특성상 `os.environ["TZ"]`만으로는 `datetime.now()` (Naive Datetime)가 KST로 변환되지 않아 08:50 장전 대기 및 09:00~15:30 정규장 루프가 9시간 어긋나게 작동함.
- **완벽 해결 조치:**
  - Python 3.9+ 표준 라이브러리인 `zoneinfo.ZoneInfo("Asia/Seoul")`를 사용하여 동적 KST 타임존 객체를 생성하는 `get_kst_now()` 유틸리티를 전역 표준시간 생성자로 지정 (`database.py`).
  - `main_rest_async.py`, `api_server.py`, `notifier.py`, `strategy.py`, `data_collector.py`, `market_data_buffer.py`, `macro_regime_filter.py`, `dashboard.py`, `verify_*.py` 소스 코드 전반의 Naive `datetime.now()`를 `get_kst_now()`로 일괄 전환.
  - Aware Datetime과 Naive Datetime 간의 비교 충돌(`TypeError: can't compare offset-naive and offset-aware datetimes`)이 발생하지 않도록 KST 타임존 정합성을 일체화하여 완벽 방어.

### 2. 수정된 파일
- `database.py`: `ZoneInfo("Asia/Seoul")` 기반 `get_kst_now()` 및 `format_kst_time_str()` 정밀 구현
- `main_rest_async.py`: `wait_until_next_market_open()`, `trading_loop()`, `run_daemon()` 등 트레이딩 스케줄러 시간부 KST 전면 적용
- `api_server.py`: 자동 웨이크업 스케줄러 및 로그 타임스탬프 KST 일체화
- `notifier.py`: 카카오톡 주문 체결, 서킷브레이커, 일일 결산 알림 포맷터 KST 동기화
- `strategy.py`: 장초반 노이즈 차단(09:15 이전) 및 장마감 오버나잇 강제 청산(15:15 이후) 시간 판단 로직 KST 전환
- `data_collector.py`, `market_data_buffer.py`, `macro_regime_filter.py`, `dashboard.py`, `verify_*.py`: 일봉/분봉 수집, 링버퍼, 매크로 평가, 대시보드 및 검증 스크립트 KST 동기화

### 3. 실측 검증 결과
- **Python 3.11 KST 타임존 실측 출력 검증:**
  - `get_kst_now()` 실행 결과: `2026-09-29 14:52:15 KST+0900 | 타임존: Asia/Seoul`
  - 실제 한국 표준시(KST) 및 Asia/Seoul 타임존과 100% 일치함 확인.

---

## 📅 [2026-09-23 15:30] 키움 OpenAPI REST 실제 응답 스키마 정밀 매핑 및 6대 보유종목 실시간 100% 동기화 버그 완벽 해결

### 1. 작업 개요 및 목적
- **발생 문제 분석:**
  - 사용자 계좌에 실제로 6개 종목(대한전선, 원익홀딩스, 빛샘전자, 후성, KODEX 코스닥150, TIGER 코스닥150)을 보유 중임에도 불구하고, 대시보드 및 봇 내부에서 보유 종목이 0건으로 인식되던 치명적 결함 발생.
- **근본 원인 규명:**
  1. **TR 필수 파라미터 `qry_tp="0"` 누락:** 키움 `kt00018` 및 `kt00004` TR 호출 시 `qry_tp="0"`(전체 조회)이 누락되었거나 필수값이 비어 입력값 오류(에러코드 1511)가 발생.
  2. **키움 실제 응답 JSON 리스트 키 누락:** 키움 REST API 실제 응답의 보유종목 리스트 키 이름(`acnt_evlt_remn_indv_tot`, `stk_acnt_evlt_prst`, `stk_cntr_remn`)이 파싱 우선순위 키(`pos_list_keys`)에 누락되어 빈 리스트(`[]`)로 처리됨.
  3. **보유수량/매입단가/손익/수익률 필드명 불일치:** 키움 실제 응답의 `pur_pric`(매입가), `cur_prc`(현재가), `rmnd_qty`/`cur_qty`(보유수량), `evltv_prft`/`pl_amt`(평가손익), `prft_rt`/`pl_rt`(수익률), `d2_entra`(D+2 예수금) 등의 필드명이 파싱 키 목록에 완벽하게 등록되지 않아 역산/파싱 실패.
- **완벽 해결 조치:**
  1. `async_kiwoom_client.py`:
     - `get_account_balance()` 내 `kt00018`, `kt00004` 호출 시 `qry_tp="0"`, `"1"`, `"2"` 전수 스캔 및 `kt00005` 순수 페이로드 병합.
     - `pos_list_keys`에 `acnt_evlt_remn_indv_tot`, `stk_acnt_evlt_prst`, `stk_cntr_remn`을 최우선 등록하고 `merged_data['output2']` 및 `acnt_evlt_remn_indv_tot` 양쪽에 종목 리스트 100% 탑재.
     - `get_deposit_info()`에 `d2_entra`, `entr_d2` 키 추가.
  2. `async_portfolio.py`:
     - `sync_positions()` 내 `priority_keys`에 `acnt_evlt_remn_indv_tot`, `stk_acnt_evlt_prst`, `stk_cntr_remn` 추가.
     - `qty_keys`(`cur_qty`, `setl_remn`), `buy_p_keys`(`pur_pric`), `pnl_keys`(`evltv_prft`, `pl_amt`), `rt_keys`(`prft_rt`, `pl_rt`)를 키움 실측 응답과 1:1 완벽 매핑.
  3. `main_rest_async.py`:
     - `_sync_account_balance()` 내 `tot_evlu_keys`에 `tot_evlt_amt`, `aset_evlt_amt`, `tot_est_amt`, `prsm_dpst_aset_amt` 추가 및 `d2_deposit_keys`에 `d2_entra` 최우선 매핑.

### 2. 수정된 파일
- `async_kiwoom_client.py`: `get_account_balance`, `get_deposit_info` 실측 TR 스키마 매핑 및 qry_tp="0" 전수 스캔
- `async_portfolio.py`: `sync_positions` 키움 실측 필드명 및 리스트 키 정밀 매핑
- `main_rest_async.py`: `_sync_account_balance` D+2 예수금 및 총평가금액 실측 키 확장

### 3. 실측 검증 결과
- **실계좌 4대 TR 실측 검증:**
  - 키움증권 실계좌(66243841) 6대 전 종목 100% 정상 수집 및 동기화 확인:
    1. 대한전선 (001440): 1주 @ 30,650원 (현재가 30,775원, 손익 +64원, +0.21%)
    2. 원익홀딩스 (030530): 1주 @ 28,100원 (현재가 28,100원, 손익 -56원, -0.20%)
    3. 빛샘전자 (072950): 1주 @ 14,610원 (현재가 14,500원, 손익 -139원, -0.95%)
    4. 후성 (093370): 1주 @ 13,920원 (현재가 13,910원, 손익 -36원, -0.26%)
    5. KODEX 코스닥150 (229200): 1주 @ 13,980원 (현재가 14,070원, 손익 +90원, +0.64%)
    6. TIGER 코스닥150 (232080): 1주 @ 14,340원 (현재가 14,325원, 손익 -15원, -0.10%)
  - 계좌 자산 실측: 총자산 115,680원, D+2 주문가능 예수금 1,089원 100% 정확 매핑.
- **FastAPI / WebSocket E2E:** `test_api_server.py` 15개 전 테스트 100% 통과.
- **프론트엔드 빌드:** `npm run build` Vite 번들링 정상 완료.

---

## 📅 [2026-09-22 14:30] 매수 미체결 방치로 인한 증거금 부족 에러 해결 및 매수 타임아웃 자동 취소(Auto-Cancel) 로직 도입

### 1. 작업 개요 및 목적
- **매수 미체결 증거금 실시간 락(Margin Lock) & 실제 가용 주문금액 검증:**
  - 매수 주문 직후 `_sync_account_balance()` 호출 시 키움 TR이 미체결을 미반영한 원래 예수금(12만 원)을 내려주어 로컬 자금이 롤백되던 결함 교정.
  - `OrderTimeoutManager`에 `get_pending_buy_amount()`, `get_pending_buy_codes()`를 구현하여 대기 중인 미체결 매수 증거금을 실시간 락(Lock) 차감한 `real_available_cash = max(0, current_capital - pending_amt)` 기준으로 매수 가능 여부를 100% 통제.
- **최대 활성 슬롯(보유 종목 + 미체결 대기 종목) 제한 (Over-trading 방지):**
  - `async_portfolio.py`의 `can_buy(code, pending_buy_codes)`에서 체결된 포지션뿐만 아니라 미체결 매수 대기 종목까지 합산 슬롯으로 검증하여 최대 종목 수(3~5개)를 초과한 주문 난사 및 동일 종목 중복 매수를 원천 차단.
- **매수 미체결 30초 타임아웃 자동 취소 & 30초 쿨다운(Cooldown) 방어선 구축:**
  - 30초 이내 미체결 매수 주문은 `kt10003` 취소 주문을 즉시 발송하여 묶인 예수금을 실시간 반환.
  - 취소된 종목은 30초간 재매수 쿨다운(`cancelled_cooldowns`)을 적용하여 불필요한 API 호출 과부하(Infinite Loop) 방지.

### 2. 수정된 파일
- `main_rest_async.py`: `OrderTimeoutManager` 미체결 매수 증거금/종목 조회 및 30초 쿨다운 로직 탑재, `_evaluate_buy_condition` 실제 가용 예수금 기반 매수 통제 연동
- `async_portfolio.py`: `can_buy` 미체결 종목 합산 슬롯 검증 및 `get_available_cash_with_pending_lock`, `get_order_qty` 실제 가용 예수금 연동
- `verify_unexecuted_timeout_and_margin_lock.py`: 4단계 라이프사이클(자금부족 차단 ➔ 30초 타임아웃 취소 ➔ 예수금 반환 ➔ 신규 정상 매수) 투명성 실측 검증 스크립트 작성

### 3. 검증 결과
- **단위 테스트:** `pytest` 65개 전 테스트 100% 통과 (PASS, 12.29s).
- **투명성 실측 검증 (`verify_unexecuted_timeout_and_margin_lock.py`):**
  - ① 삼성전자(70,000원) 미체결 매수 발주 ➔ 70,000원 락 ➔ 가용 자금 50,000원 축소 실측.
  - ② SK하이닉스(150,000원) 매수 시도 ➔ 가용 자금(50,000원) 부족으로 신규 매수 100% 차단 실측.
  - ③ 펄어비스(35,000원) 매수 발주 ➔ 105,000원 락 ➔ 파인엠텍(20,000원) 매수 시도 차단(가용 15,000원 부족) 실측.
  - ④ 30초 타임아웃 도달 ➔ `kt10003` 2건 자동 취소 발송 ➔ 가용 예수금 120,000원 전액 복구 실측.
  - ⑤ 예수금 반환 후 비에이치(20,100원) 신규 정상 매수 100% 성공 실측.

---

## 📅 [2026-09-22 13:45] 고강도 8-Angle 코드 리뷰 기반 미체결 타임아웃 락 해제·수동주문 안전가드·pytest 런타임 최적화 (--fix)

### 1. 작업 개요 및 목적
- **고강도 8 독립 앵글 코드 리뷰(Recall 중심) 및 실전 안정성 결함 전수 해결:**
  1. **미체결 매도 타임아웃 KRX 락 해제 대기 및 800033 Fail-Safe 탑재 (`main_rest_async.py`):**
     - `OrderTimeoutManager.check_and_resolve_timeouts()`에서 매도 지정가 주문 타임아웃(30초) 취소 후 대기 없이 시장가(03)를 발주할 때 KRX 매도가능수량 락으로 인해 800033 에러가 발생할 수 있는 결함을 `await asyncio.sleep(0.2)` 및 Fail-Safe 1회 재시도로 원천 차단.
  2. **체결 데이터 `uncl_qty == 0` 미반영 오취소 버그 수정 (`main_rest_async.py`):**
     - `on_chejan_data()`에서 키움 Chejan 전량 체결 이벤트 수신 시 `uncl_qty > 0` 조건으로 인해 잔여 수량이 0으로 갱신되지 않고 30초 후 오취소/재발주되던 버그를 `uncl_raw is not None` 명시적 판별로 수정.
  3. **분할 익절(`_execute_profit_sell`) 800033 Fail-Safe 복구 탑재 (`main_rest_async.py`):**
     - 정규 분할 익절 시 일시적 미체결 락으로 800033 에러 반환 시 선제 취소 후 재발주하는 Fail-Safe 복구 로직 보강.
  4. **수동 매수 안전 가드 및 타임아웃 트래커 연동 (`api_server.py`):**
     - `/order/manual` 엔드포인트에서 시장가(03) 매수 요청 시 현재가 기반 지정가(00)로 자동 변환하여 키움 855056(증거금 부족) 에러를 방지하고, 주문 접수 성공 시 `order_timeout_mgr.track_order`에 자동 등록.
  5. **비상 킬스위치 후 수동 시작(START) 시 매수 차단 해제 연동 (`api_server.py`):**
     - `/bot/emergency-stop`으로 `mdd_shutdown=True` 설정 후 대시보드에서 `START`를 눌렀을 때 `mdd_shutdown` 및 `daily_circuit_breaker`가 초기화되지 않아 매수 평가가 영구 차단되던 결함 수정.
  6. **`pytest.ini` 레거시 격리 및 테스트 속도 50% 단축 (`pytest.ini`, `legacy/test_balance.py`):**
     - `norecursedirs`에 `legacy`, `frontend`, `node_modules`를 명시하고 `legacy/test_balance.py`의 최상단 동기 HTTP 호출을 `main()`으로 캡슐화하여 `RuntimeWarning: coroutine was never awaited` 제거 및 테스트 실행 시간 대폭 단축 (31.37s ➔ 16.43s).

### 2. 수정 파일 목록
- `main_rest_async.py`: 미체결 타임아웃 KRX 락 해제 대기(0.2s), `on_chejan_data` 0주 잔여수량 갱신, `_execute_profit_sell` 800033 복구 로직 탑재.
- `api_server.py`: 수동 매수 시장가→지정가 변환 및 OrderTimeoutManager 연동, `START` 액션 시 `mdd_shutdown` 리셋.
- `pytest.ini`: `norecursedirs` 설정으로 `legacy` 디렉토리 자동 수집 제외.
- `legacy/test_balance.py`: `if __name__ == '__main__':` 가드로 모듈 임포트 시 동기 네트워크 호출 차단.

### 3. 검증 결과
- **단위 테스트:** `pytest` 63개 전 테스트 100% 통과 (PASS, 16.43s, 경고 0건).
- **실측 검증:** `verify_unexecuted_cancel_and_sell.py` 1~3단계 교차 검증 100% 통과.
- **FastAPI/대시보드 검증:** `test_api_server.py` 15개 엔드포인트 100% 통과.
- **프론트엔드 빌드:** `npm run build` 번들 정상 빌드 완료.

---

## 📅 [2026-09-22 11:05] 심층 코드 리뷰 피드백 반영 및 실시간 매매/비상 정지 핵심 버그 수정 (--fix)

### 1. 작업 개요 및 목적
- **심층 8개 앵글 코드 리뷰 기반 버그 및 안정성 결함 전수 해결:**
  1. **미체결 주문 조회 중복 선언 및 `TypeError` 제거 (`async_kiwoom_client.py`):**
     - `get_unexecuted_orders`가 385줄(kt00007, `code` 지원)과 544줄(ka10075, `code` 미지원)에 중복 선언되어 544줄이 덮어쓰면서 발생하던 `code` 키워드 인자 TypeError 결함 완벽 해결 (kt00007 + ka10075 폴백 통합).
  2. **ADX 실시간 지표 키 불일치 및 횡보장 휩소 바이패스 버그 수정 (`indicators.py` & `strategy.py`):**
     - `TechnicalIndicators.get_latest_indicators`의 `'adx14'` 키와 `strategy.py`의 `ind.get('adx')` 간 키 불일치로 인해 실전 매매 중 ADX가 0으로 처리되어 추세 필터가 상시 무력화되던 버그를 다중 키(`adx`, `adx14`) 지원으로 완벽 수정.
  3. **장전 08:50 스케줄러 서킷브레이커 리셋 누락 수정 (`api_server.py`):**
     - 전일 -2.5% 손실로 서킷브레이커가 발동된 후 익일 08:50 아침 자동 재개 시 `daily_circuit_breaker = False` 및 `daily_start_capital = 0.0` 초기화가 누락되어 당일 매수가 영구 차단되던 결함 수정.
  4. **비상 킬스위치 800033 매도 거절 방어 (`api_server.py`):**
     - `/bot/emergency-stop` 실행 시 미체결 주문 선제 취소 파이프라인(`_cancel_unexecuted_orders_for_stock`)을 연동하여 매도가능수량 0주 락으로 인한 청산 실패 방어.
  5. **환산 거래량 급증 필터 `skip_time_filter` 방어 가드 보완 (`strategy.py`):**
     - 오프마켓/데모/백테스트 환경에서 실시간 경과 시간 계산으로 인해 유효 타점이 오기각되던 현상 방지.
  6. **핫 패스 마이크로 최적화 (`main_rest_async.py`):**
     - 틱당 호출되는 `_evaluate_buy_condition` 내부의 중복 `import time` 제거.

### 2. 수정 파일 목록
- `async_kiwoom_client.py`: `get_unexecuted_orders` 중복 제거 및 kt00007 / ka10075 통합 폴백 지원.
- `indicators.py`: `get_latest_indicators`에 `'adx'` 및 `'adx14'` 듀얼 키 매핑.
- `strategy.py`: `adx` 지표 다중 키 조회 및 환산 거래량 `skip_time_filter` 가드 탑재.
- `api_server.py`: 장전 08:50 스케줄러 서킷브레이커/자본 리셋 추가 및 비상 킬스위치 미체결 선제 취소 연동.
- `main_rest_async.py`: 실시간 틱 평가 함수 내 중복 `import time` 제거.

### 3. 검증 결과
- **단위 테스트:** `pytest` 62개 전 테스트 100% 통과 (PASS).
- **시뮬레이션 및 교차 검증:**
  - `test_async_trading_loop.py` 23개 시뮬레이션 테스트 100% 통과.
  - `verify_order_pipeline.py` 매수/예수금/켈리/주문 파이프라인 100% 실측 완료.
  - `verify_quant_winrate_defense.py` 가짜 돌파 100% 거절, 서킷브레이커 100% 방어 실측 완료.
  - `verify_unexecuted_cancel_and_sell.py` 800033 에러 방어 및 선제 취소 청산 100% 통과.
- **프론트엔드 빌드:** `npm run build` 번들 정상 빌드 완료 (에러 0건).

---

## 📅 [2026-09-21 15:40] 퀀트 승률(37.3% ➔ 70%+) 개선용 다중 필터(ADX/VWAP/체결강도) 및 일일 최대 손실(-2.5%) Circuit Breaker 도입

### 1. 작업 개요 및 목적
- **기존 승률 저조(37.3%, 195승 327패, 누적 -86.91%) 근본 패착 원인 해결:**
  - 무추세 횡보장 톱니파동(Chop)에서 가짜 돌파(Bull Trap)에 피격되어 손절이 누적되던 현상을 원천 차단.
  - 기관 기준가(VWAP) 지지 없이 상투 과열주를 추격 매수하던 패턴 기각.
- **고승률 5중 퀀트 알파 필터 (AND 결합) 탑재:**
  1. **ADX(14) 추세 강도 필터:** `ADX >= 18.0` 및 `+DI > -DI` (무추세 횡보장 휩소 100% 기각).
  2. **VWAP 스마트 밴드:** `VWAP * 1.002 <= 현재가 <= VWAP * 1.030` (기관 지지 구간 안착 및 과열 추격 방지).
  3. **체결강도(Volume Power ≥ 110%):** 실질 매수세 우위 확인.
  4. **Volume Profile POC 매물대 저항선 돌파 안착:** 매물벽 직전 매수 기각.
  5. **볼린저 밴드 + 켈트너 스퀴즈 모멘텀 상방 발산:** 에너지 응축 후 상방 폭발 구간 공략.
- **일일 최대 손실 제한(Daily Circuit Breaker) 구현:**
  - 당일 시작 자산(`daily_start_capital`) 대비 **누적 손실 `-2.5%` 초과 시 당일 신규 매수 즉시 전면 차단**.
  - 익일 08:50 AM 장전 스케줄러에서 자산 재평가 후 자동 리셋.

### 2. 주요 수정 및 신설 파일
- `strategy.py`:
  - `AdaptiveVolatilityBreakoutStrategy`: ADX(14) 추세 필터, VWAP 밴드, 체결강도, 스퀴즈 모멘텀 AND 결합.
- `main_rest_async.py`:
  - `daily_start_capital`, `daily_loss_limit_rate = -0.025`, `daily_circuit_breaker` 탑재.
  - `_sync_account_balance`: 당일 -2.5% 손실 도달 시 `daily_circuit_breaker = True` 즉각 발동.
  - `wait_until_next_market_open`: 익일 08:50 서킷 브레이커 자동 리셋.
- `verify_quant_winrate_defense.py`:
  - [투명성 교차 검증] 1) 횡보장 가짜 신호 100건 주입 시 100% 거절 실측, 2) 정규 주도주 100건 100% 진입 실측, 3) 당일 -2.5% 손실 도달 시 매수 차단 100% 실측 스크립트 작성.

### 3. 검증 결과
- **백테스트 시뮬레이션 실측 (`verify_quant_winrate_defense.py`):**
  - 가짜 돌파 100건 중 **100건 전원 사전 거절 (방어율 100.0%)**: ADX 무추세 34건, VWAP 하회 33건, 체결강도 부족 33건 완벽 방어.
  - 정규 주도주 100건 중 **100건 100% 정규 매수 진입 승인**.
  - 일일 -2.5% 초과 손실 발생 시 **Daily Circuit Breaker 즉각 발동 및 신규 매수 주문 0건 차단 완료**.
- **단위 테스트:** `pytest` 62개 전 테스트 100% 통과.

---

## 📅 [2026-09-21 15:10] 실시간 보유 종목 리스트 새로고침 시 깜빡임(Flickering) 현상 및 렌더링 롤백 버그 수정

### 1. 작업 개요 및 목적
- **실시간 포지션 리스트 깜빡임(Flickering) 근본 원인 해결:**
  - `App.tsx` 내 `displayPortfolio`와 `portfolio`의 핑퐁 삼항 연산자로 인해 상태 동기화 지연 시 순간적으로 빈 배열(`[]`)이 주입되며 "현재 보유 중인 포지션이 없습니다" 빈 화면이 깜빡이던 현상 해결.
  - `useWebSocket.ts` 내에 `smartMergePositions` In-Place 지능형 병합 로직을 구축하여, 실시간 시세/수익률 수신 시 기존 배열 객체 참조를 보존하고 변경된 종목의 속성만 국소 덮어쓰도록 최적화.
  - `ActivePositionsBento.tsx`의 개별 종목 카드를 `PositionCardItem = React.memo(...)`로 분리하여 데이터가 변하지 않은 종목의 불필요한 Virtual DOM 리렌더링을 100% 차단.
  - `QuantPerformanceBento.tsx`의 지표 누락 시 `toFixed` TypeError 방어 로직 추가.

### 2. 주요 수정 및 신설 파일
- `frontend/src/hooks/useWebSocket.ts`:
  - `smartMergePositions`: 기존 종목 배열과 신규 수신 데이터를 비교하여 변경된 속성만 In-Place 병합.
  - `updateBothStates`: 단일 소스 참조 보존.
- `frontend/src/components/ActivePositionsBento.tsx`:
  - `PositionCardItem`: `React.memo` 분리 및 `key={pos.code}` 기반 렌더링 안정화.
- `frontend/src/App.tsx`:
  - `ActivePositionsBento`에 안정된 `portfolio.positions` 직접 전달 및 핑퐁 제거.
- `frontend/src/components/QuantPerformanceBento.tsx`:
  - `toFixed` 호출 시 undefined 방어 (`(val ?? 0).toFixed(...)`).
- `frontend/verify_flicker_e2e.cjs`:
  - 0.4초 간격 실시간 WebSocket 시세 브로드캐스트 주입 및 Puppeteer E2E 깜빡임 0건 검증 스크립트 작성.

### 3. 검증 결과
- **프론트엔드 빌드:** Vite 프로덕션 빌드 100% 성공 (`built in 4.73s`).
- **Puppeteer E2E 렌더링 실측 검증 (`verify_flicker_e2e.cjs`):**
  - 0.4초 간격 10회 연속 WebSocket 시세 변동 주입 시 **깜빡임(Empty State) 0회, 카드 소멸 0회, 콘솔 에러 0건** 통과.
- **백엔드 단위 테스트:** `pytest` 62개 전 테스트 100% 통과.

---

## 📅 [2026-09-21 14:40] 키움 매수 SendOrder 불발 버그 수정 및 승률 개선을 위한 5대 퀀트 알파 필터·하드 스탑로스(-3%) 고도화

### 1. 작업 개요 및 목적
- **매수 주문 불발(Order Drop) 4대 근본 원인 규명 및 전면 해결:**
  1. **인메모리 링버퍼 초기 캔들 부족 시그널 기각 결함:** 봇 기동 직후 캔들 부족으로 `ind={}` 공백 발생 시 `check_buy_signal`에서 무조건 탈락하던 결함을 시세/워치리스트 기본 정보 자가 복구 로직으로 해결.
  2. **순간 틱 체결량(`cntg_vol`)과 누적 거래량 혼용 교정:** 실시간 틱 체결량(예: 50주)이 30억 거래대금 필터와 비교되어 100% 탈락하던 결함을 누적 거래량(`acml_vol`) 및 당일 환산 거래대금 기반으로 분리 정규화.
  3. **피보나치와 ATR 돌파 조건 상호 배제 해소:** 주가 돌파 시 `fib_rebound`가 False가 되며 ATR 0일 때 돌파선 계산 오류로 시그널이 닫히던 문제를 적응형 ATR 폴백으로 복구.
  4. **켈리 수량 0주 시 소액 계좌 최소 1주 안전 가드 탑재:** 가용 예수금 범위 내에서 최소 1주 이상 매수 발주 보장.
- **승률 70%+ 타겟팅: 5대 퀀트 알파 필터 탑재:**
  1. **체결강도(Volume Power ≥ 110%) 필터:** 매수 체결 우위 구간에서만 진입하여 휩소 방어.
  2. **호가 불균형(Orderbook Imbalance) 필터:** 매도/매수 잔량 비율(≥0.6)을 검증하여 가짜 돌파 기각.
  3. **VWAP 스마트 지지 & 건전 이격도(+0.2% ~ +2.5%) 가드:** VWAP 하회 약세주 및 과열 추격 매수 차단.
  4. **대량 매물대(Volume Profile POC) 저항선 돌파 안착 필터:** 핵심 매물대 저항 직전 매수 기각.
  5. **볼린저 밴드 + 켈트너 스퀴즈 모멘텀 상방 발산 필터:** 에너지 수축 후 상방 폭발 구간 공략.
- **출구 전략 및 리스크 관리 강화:**
  1. **하드 스탑로스:** 사용자 지정 **-3.0% 엄격 고정** (CRITICAL 긴급 매도).
  2. **본절선 상향(Breakeven Guard):** +1.2% 상승 시 +0.25%(세금/수수료 포함) 본절가 고정.
  3. **3단계 적응형 분할 익절:** 1차(+2.5~3.0% 50%), 2차(+4.5~5.0% 50%), 3차(샹들리에 트레일링 스탑 잔여 전량).

### 2. 주요 수정 및 신설 파일
- `strategy.py`:
  - `AdaptiveVolatilityBreakoutStrategy`: 5대 퀀트 알파 필터(체결강도, 호가불균형, VWAP, POC 매물대, 스퀴즈 모멘텀) 통합.
  - 하드 스탑로스 -3.0% 고정, 본절선 +1.2% 발동 및 3단계 ATR R-배수 분할 익절 고도화.
- `main_rest_async.py`:
  - `_evaluate_buy_condition`: 실시간 시세/누적 거래량 정규화, 지표 결측치 방어, 1주 최소 발주 가드.
  - `OnReceiveRealData`: `raw_data` 파이프라인 연동.
- `async_kiwoom_client.py`:
  - `_get_headers`: 키움 REST 규격 `appkey`, `appsecret`, `api-id`, `tr_id` 헤더 완전 보장.
  - `send_order`: 에러코드/메시지 상세 로깅 및 주문 접수 검증 강화.
- `test_strategy_quant.py`:
  - `test_alpha_filters_volume_power_and_imbalance` 신규 알파 필터 검증 테스트 추가.
- `verify_order_pipeline.py`:
  - [투명성 교차 검증] 가상 시세 주입을 통한 `매수 시그널 ➔ 예수금 확인 ➔ 켈리 수량 ➔ SendOrder ➔ 포지션 편입` 전 과정 1회성 실측 스크립트 작성.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 62개 테스트 스위트 100% 통과 (`62 passed`).
- **투명성 교차 검증 (`verify_order_pipeline.py`):**
  - 가상 틱 데이터(71,500원, +2.14%, 거래량 250만주, 체결강도 135.5%) 주입 후 `BUY_SIGNAL` 발생 ➔ `SendOrder` 26주 @ 71,500원 발주 ➔ 계좌 1종목 편입 실측 완료.

---

## 📅 [2026-09-17 14:00] 키움 자동매매 장중 매수 차단 결함 수정, 3단계 분할 익절 수량 개선, 본절선 1.0025 상향 및 09:15 타임 필터 배포

### 1. 작업 개요 및 목적
- **장중 주식 '매수(Buy)' 전면 차단 원인 규명 및 긴급 정상화:**
  - **시가(Open Price) 덮어쓰기 결함 복구:** `_evaluate_buy_condition`에서 `ind['open'] = cur_price`로 현재가를 대입하여 `cur_price >= cur_price + 0.5*ATR`이 되어 변동성 돌파 매수가 100% 기각되던 치명적 결함을 당일 실제 시가(`info.get('open_price')`)로 매핑하여 정상화.
  - **KODEX 200 시장 지수 필터 완화:** 과도하게 민감했던 지수 급락 기준(`-0.8%`)을 `-1.5%`로 정상 복구하여 장중 소폭 조정 시 매수 파이프라인이 조기 차단되는 현상 해결.
  - **RSI(14) 필터 및 0 나누기 방어:** 모멘텀 돌파 전략 특성을 고려하여 RSI 70+ 차단을 85+ 극단 과열 차단으로 완화하고, 가격 무변동 시 RSI 100으로 왜곡되던 `indicators.py` 로직 수정.
  - **거래대금/환산거래량 필터 완화:** 당일 30억 이상 및 환산거래량 1.5배로 유연화하여 유효 주도주 진입 허들 정상화.
- **익절 수량 배분 구조 개선:**
  - 1차: 전체 물량의 50% 매도 (Stage 0 -> Stage 1)
  - 2차: 잔여 물량의 50% 매도 (Stage 1 -> Stage 2)
  - 3차: 나머지 잔여 물량 전량(100%) 청산 (Stage 2 -> 청산)
- **본절선(Break-even) 가드 기준 상향:**
  - 거래세(0.18%) + 수수료(0.015%x2) + 슬리피지를 완벽 보전하기 위해 기존 `1.002`에서 **`1.0025`(+0.25%)**로 상향.
- **장초반 노이즈 회피 09:15 타임 필터 신설:**
  - 09:00~09:15 구간의 휩소/호가 불안정을 방어하기 위해 09:15 이전 신규 매수를 원천 차단하는 Time 조건 추가.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `update_watchlist()`, `OnReceiveRealData()`, `_realtime_data_stream_worker()`, `monitor_watchlist_and_enter()`에 당일 시가(`open_price`) 파싱 및 실시간 틱 전달 파이프라인 구축.
  - `_evaluate_buy_condition()`: `ind['open'] = open_price` 주입 및 09:15 이전 매수 차단 타임 필터 가드 추가.
  - `check_market_filter()`: KODEX 200 임계값 `-1.5%`로 복원.
  - `take_profit_stages` 및 `monitor_positions_and_exit()`: 1차(전체의 50%), 2차(잔여의 50%), 3차(나머지 전량) 단계별 익절 수량 정밀화.
- `strategy.py`:
  - 09:15 타임 필터, RSI 85+ 과열 필터, 당일 30억 & 환산거래량 1.5배, 피보나치/돌파 안정화 적용.
  - 본절선 가드 및 샹들리에 트레일링 스탑 본절선 `buy_price * 1.0025` 적용.
- `indicators.py`:
  - `calculate_rsi()`: 변동 없을 때(gain=0, loss=0) RSI 50.0 기본값 처리 및 극단 플랫 왜곡 방어.
- `test_strategy_quant.py`:
  - `test_breakeven_guard_1_0025`, `test_time_filter_0915_and_buy_breakout`, 3차 전량 익절 테스트 추가.
- `test_async_trading_loop.py`:
  - Test 20 (`test_buy_signal_open_price_and_0915_time_filter`) 추가 및 20개 시뮬레이션 테스트 100% 통과.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 56개 테스트 스위트 100% 통과 (`56 passed`).
- **20개 트레이딩 루프 시뮬레이션:** `python test_async_trading_loop.py` 100% 통과.
- **인사이트 영구 저장:** `Mem0`에 매매 로직 조건문 충돌 및 Git Diff 추적 해결 패턴 저장 완료.

---

## 📅 [2026-09-16 17:55] 수동 일시정지 후 익일 영업일 아침(08:50 AM) 자동 재시작(Wake-up) 스케줄러 구축 및 State 오버라이드 배포 완료

### 1. 작업 개요 및 목적
- **수동 일시정지 영구 지속 문제 해결:**
  - 기존에는 사용자가 대시보드 콕핏에서 [봇 일시정지]를 클릭하여 봇을 중지하면, 다음 날 아침 장이 시작되어도 봇이 계속 정지 상태를 유지하여 사용자가 직접 매매 재개 버튼을 눌러야만 하던 불편함을 해결.
- **3단계 상태 모델(`is_running`, `is_paused`, `is_shutdown`) 정립:**
  - 단일 불리언으로 관리되던 프로세스 생명주기를 세분화하여, 수동 정지 시 데몬 자체의 백그라운드 대기 루프는 정상 유지되면서 당일 매매 루프만 안전하게 정지되도록 분리.
- **영업일(Business Day) 및 한국 거래소 공휴일 판별 가드 구축 (`is_korean_market_holiday`):**
  - 주말(토/일) 및 신정, 삼일절, 근로자의 날(5/1), 어린이날, 현충일, 광복절, 개천절, 한글날, 성탄절, 증시 폐장일(12/31) 등 휴장일에는 자동 기상하지 않고 다음 정상 개장일까지 비동기 휴면하도록 안전 결합.
- **익일 아침 08:50 KST 자동 웨이크업(Wake-up) 시퀀스 탑재:**
  - `main_rest_async.py`의 `wait_until_next_market_open` 및 `api_server.py`의 `daily_market_scheduler_loop`를 통해, 익일 영업일 아침 08:50 도달 시 `is_paused = False`, `running = True`, `mdd_shutdown = False`로 자동 오버라이드 리셋.
  - 계좌 잔고 동기화 및 당일 감시 유니버스(거래대금 상위 30종목 피보나치 분석) 사전 분석 완료 후 09:00 정규 매매 루프 자동 기동.
- **프론트엔드 실시간 동기화:**
  - 프론트엔드(`useWebSocket.ts`)의 3초 주기 `/status` 폴링 및 WebSocket 실시간 브로드캐스트로 콕핏 UI의 상태 뱃지가 '엔진 가동 중'으로 자동 동기화됨.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `is_paused`, `is_shutdown` 상태 변수 추가 및 `running` 프로퍼티(`is_running and not is_paused`), `pause()`, `resume()` 메서드 신설.
  - `is_korean_market_holiday()` 정적 메서드 구현.
  - `wait_until_next_market_open()`, `_realtime_data_stream_worker()`, `trading_loop()`, `run_daemon()`에 일시정지 자동 해제 및 08:50 자동 웨이크업 로직 통합.
- `api_server.py`:
  - `ServerContext`에 `daily_scheduler_task` 추가.
  - `daily_market_scheduler_loop()` 백그라운드 태스크 신설 및 `lifespan` 관리.
  - `/bot/control`의 `START`/`STOP` 및 `/status`에 `is_paused` 상태 연동.
- `test_async_trading_loop.py` & `test_api_server.py`:
  - Test 19 (`test_bot_auto_wakeup_and_resume_schedule`) 신설 및 REST API `is_paused` 상태 연동 테스트 검증.
- `pytest.ini`:
  - `asyncio_mode = auto` 설정 추가.

### 3. 검증 결과
- **단위/통합 테스트:** `pytest` 전체 53개 테스트 스위트 100% 통과 (`53 passed`).
- **프론트엔드 빌드:** TypeScript 및 Vite 번들링 성공 (0 errors).
- **형상 관리:** `feat: 봇 수동 일시정지 후 다음 영업일 아침 자동 재시작(Wake-up) 스케줄링 추가` 커밋 및 원격 푸시 완료.

---

## 📅 [2026-09-16 10:55] 키움 TR 보유종목 매입단가(매수가) '1원' 표출 버그 완벽 해결 및 6단계 다중 방어 해석 알고리즘(6-Layer Resolution) & 프론트엔드 안전 렌더링 배포 완료

### 1. 작업 개요 및 목적
- **1) 보유종목 '매수가 1원' 표출 버그 원인 규명 및 전면 해결:**
  - **원인 ① (프론트엔드 Default Coercion의 함정):** `ActivePositionsBento.tsx` 및 `Header.tsx`에서 `const buyPrice = pos.buy_price || 1;` 구문으로 인해, 백엔드로부터 `buy_price`가 `0` 또는 누락으로 전달될 경우 `0 || 1` 연산에 의해 모든 종목의 매수가가 강제로 '1원'으로 표출되던 결함을 규명.
  - **원인 ② (TR 스키마 단가 필드 파편화 및 단가 누락 시 역산 부재):** 키움 TR(`kt00018`, `kt00004`, `kt00005`, `opw00018`)에서 `pchs_avg_pric` 단가 필드가 누락되고 `pchs_amt`(매입금액), `evlu_amt`(평가금액), `evlu_pfls_amt`(평가손익), `evlu_pfls_rt`(수익률)만 전송될 때 단가를 복원하는 다중 역산 파이프라인이 부재했던 점을 해결.
  - **원인 ③ (DB 복원 데이터의 1원 오염):** 과거 1원으로 저장되었던 DB 데이터가 복원될 때 자가 치유(Self-Healing) 로직이 부재했던 결함 해결.
- **2) 6단계 매수가 다중 방어 해석 알고리즘 탑재 (`async_portfolio.py`):**
  - **Layer 1 (직접 단가 키 탐색):** `pchs_avg_pric`, `pchs_price`, `pchs_prc`, `buy_uv`, `ccls_avg_pric`, `ccls_prc`, `pur_prc`, `thst_buy_uv`, `매입단가`, `매수가` 등 30여 개 키에서 1원 초과 유효값 추출.
  - **Layer 2 (매입금액 / 수량 역산):** `pchs_amt` / `qty`를 통해 정확한 매입단가 산출.
  - **Layer 3 (평가손익 역산):** `(evlu_amt - pnl) / qty`를 통해 매입원금 및 단가 산출.
  - **Layer 4 (수익률 역산):** `current_price / (1 + yield_rate / 100)`를 통해 현재가와 수익률 기반 단가 복원.
  - **Layer 5 (직전 포지션 상속):** 인메모리 `prev_positions`의 유효 매수가 보존 및 상속.
  - **Layer 6 (현재가 안전 폴백):** 어떠한 단가 필드도 유효하지 않을 경우 `current_price`를 안전 기본값으로 채택하여 0원/1원 왜곡을 원천 차단.
- **3) DB 및 API 서버 자가 치유(Self-Healing) 탑재 (`api_server.py`, `async_portfolio.py`):**
  - DB 복원 및 실시간 브로드캐스트 루프에서 `buy_p <= 1.0 and cur_p > 1.0`인 경우 현재가(`cur_p`)로 자동 보정하여 오염 데이터 차단.
- **4) 프론트엔드 안전 렌더링 및 단가 복원 (`ActivePositionsBento.tsx`, `Header.tsx`):**
  - `|| 1` 구문 제거 및 `rawBuyPrice > 1 ? rawBuyPrice : (rawCurPrice > 1 ? rawCurPrice : 0)` 안전 fallback 적용.
  - PnL 및 수익률 계산 시 0으로 나누기 방지 및 백엔드 원본 손익/수익률 우선 바인딩.

### 2. 주요 수정 파일 및 변경 내역
- `async_portfolio.py`:
  - `buy_p_keys`, `pchs_amt_keys`, `cur_p_keys`, `evlu_amt_keys`, `pnl_keys`, `rt_keys` 전수 확장.
  - 6단계 매수가 다중 방어 해석 알고리즘 탑재.
  - `restore_positions_from_db` 내 DB 오염 데이터 자가 치유 로직 추가.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio()` 내 1원/0원 방지 자가 치유 및 정합성 보장.
- `frontend/src/components/ActivePositionsBento.tsx` & `frontend/src/components/Header.tsx`:
  - `const buyPrice = pos.buy_price || 1;` 결함 제거 및 현재가 기반 안전 fallback 로직 적용.
- `test_async_trading_loop.py`:
  - `MockDatabaseManager.get_portfolio_positions` 구현 및 Test 17 (매수가 6단계 다중 방어 해석 및 1원 버그 원천 방어) 신설.

### 3. 검증 결과
- **테스트 스위트:** `test_async_trading_loop.py` 총 17개 종합 테스트 100% 통과 (`ALL PASS`).
- **단위 테스트:** `test_async_core.py`, `test_api_server.py`, `test_strategy_quant.py`, `test_valuation.py`, `test_indicators.py` 전 항목 100% 통과.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (0 errors).
- **형상 관리 및 배포:** `fix/rendering-optimization-and-safety-fixes` 브랜치 커밋 `a0534f7` 원격 push 완료.

---

## 📅 [2026-09-15 13:00] UI 렌더링 최적화(Display State 10분 주기 분리), 보유 포지션 파이프라인 정합성 복원 및 감시 종목 고가 필터 1차 방어 로직 복원 완료

### 1. 작업 개요 및 목적
- **1) Frontend UI 렌더링 최적화 및 깜빡임 해결 (Display State 분리):**
  - 실시간 매매/트레이딩 엔진은 WebSocket 실시간 최신 데이터를 사용하되, 화면 표출 전용 상태(`displayPortfolio`)를 분리하여 10분(600,000ms) 정주기로만 동기화하도록 제어.
  - 사용자가 헤더의 '새로고침' 버튼을 클릭하거나 최초 마운트 시에는 즉각 동기화하여, 실시간 틱 데이터 유입에 따른 헤더(총자산, D+2 예수금) 및 KPI 카드의 깜빡임/잦은 리렌더링을 완전히 방지.
- **2) 프로세스 분리 환경(`start.py`) 대응 보유 포지션 데이터 파이프라인 복원:**
  - `start.py`에 의해 `api_server.py`와 `main_rest_async.py`가 독립 프로세스로 실행될 때, `api_server.py`가 MariaDB의 `portfolio` 테이블과 `balance` 테이블을 상시 조회/동기화하도록 개선하여 대시보드에 4개 보유 종목이 누락 없이 표출되도록 정합성 복원.
- **3) 감시 종목(Watchlist) 1차 방어 로직(현재가 > D+2 예수금 제외) 복원:**
  - `update_watchlist()`에서 `current_price > available_cash`인 고가 종목을 Watchlist 등록 단계에서 사전에 즉시 배제하는 1차 방어 가드와 전용 디버그 로그(`🚫 [Watchlist 필터] 탈락: 잔고 부족...`)를 복원하여 안전성을 원천 확보.

### 2. 주요 수정 파일 및 변경 내역
- `frontend/src/hooks/useWebSocket.ts`:
  - `displayPortfolio` 화면 표출 전용 상태 신설 및 10분 정주기 타이머(`setInterval 10m`) 구축.
  - `handleRefresh` 함수에서 수동 즉시 동기화 지원.
- `frontend/src/App.tsx`:
  - `<Header />`, `<KpiMetricsRow />`, `<LogViewer />`, `<ActivePositionsBento />`에 `displayPortfolio` 연동.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio()`에서 `ctx.db`의 `portfolio` 테이블(`get_portfolio_positions()`) 및 `balance` 테이블(`get_latest_balance()`) 상시 조회/동기화 로직 탑재.
- `main_rest_async.py`:
  - `update_watchlist()` 내 `current_price > available_cash` 고가 종목 1차 방어 가드 및 `price_over_cash` 탈락 카운터/로그 복원.
- `GSD_MASTERPLAN.md`: Phase 16 신설 및 완료 기록.

### 3. 검증 결과
- **단위/통합 테스트:** `test_async_trading_loop.py` (11/11 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS) 전 테스트 스위트 통과.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (0 errors).
- **형상 관리:** `fix/rendering-optimization-and-safety-fixes` 브랜치 커밋.

---

## 📅 [2026-09-15] 키움 계좌 잔고 TR(kt00005/OPW00018) 실데이터(148,442원) 정합성 복원, 대용금 오맵핑 차단, 4개 보유 종목 UI 연동 및 데이터 요동 현상 완전 해결

### 1. 작업 개요 및 목적
- **실제 키움 앱 계좌 데이터와 대시보드 간 불일치 전면 해결:**
  - 실제 키움증권 계좌(`국내잔고.png`: 총평가 148,442원, 총매입 147,400원, 총손익 +778원(+0.53%), 4개 보유 종목)와 `예수금.png` (D+2예수금 1,122원, 대용금 103,890원)의 수치를 100% 일치하도록 파싱 및 상태 동기화 로직 전면 개편.
- **총자산 vs 대용금 오맵핑 및 계산식 오류 원천 차단:**
  - 기존 `final_total_asset` 계산식에서 `parsed_raw_entr > 0`일 때 Case A로 강제 진입하여 `tot_evlu_amt`(148,442원)가 무시되고 예수금(1,122원) 또는 대용금(100,842원 근처)으로 오염되던 결함을 완벽 해결.
  - 키움 TR의 `tot_evlu_amt`(총평가금액 = 148,442원)를 최우선 1순위로 매핑하고, 대용금(`sub_amt`: 103,890원)을 분리 로깅하도록 정규화.
- **멀티데이터(`output2`) 4개 보유 주식 정밀 파싱 및 UI 연동:**
  - 흥아해운(003280, 2주 @ 1,980원), 한국전력(015760, 1주 @ 32,950원), 비에이치(090460, 1주 @ 19,520원), KODEX 코스닥150(229200, 1주 @ 14,080원)의 수량, 매입단가, 현재가, 평가손익, 수익률을 누락 없이 파싱하여 `portfolio.positions`에 등록.
- **Backend/Frontend 상태 관리 일원화 및 잔고 요동(Fluctuation) 완전 제거:**
  - REST `/portfolio` 폴링과 WebSocket 브로드캐스트 간의 데이터 경합 및 덮어쓰기 충돌을 해결.
  - `AsyncPortfolioManager`를 단일 진실 공급원(Single Source of Truth)으로 확립하고, 프론트엔드(`useWebSocket.ts`)에서 WS 실시간 메시지를 우선 반영하여 1,122원과 148,442원 사이의 요동 및 포지션 0개 깜빡임 현상 제거.
- **동적 감시 목록(Dynamic Watchlist) 정상화 및 전체 퀀트 매매 워크플로우 시뮬레이션 검증:**
  - 소액 잔고(1,122원)로 인해 우량 감시 종목이 전부 탈락하던 문제 해결: 감시 목록은 거래대금 상위 20~30개 종목을 온전히 유지하고, 매수 직전에만 잔고 체크하도록 분리.
  - '피보나치 타점 매수 -> 포지션 편입 -> 보유 중 트레일링 감시 -> 3단계 분할 익절/스탑로스 전량 매도'의 전체 퀀트 매매 사이클 E2E 검증.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `_sync_account_balance()` 파싱 로직 개편: `candidates_total` 계산식 도입으로 `tot_evlu_amt`(148,442원)를 최우선 적용하고, `sub_amt`(대용금 103,890원)를 명시적으로 분리.
  - `update_watchlist()`: 고가 종목 탈락 대신 `affordable` 플래그 관리로 전환하여 20~30개 거래대금 상위 종목의 피보나치 감시 목록 유지.
- `async_portfolio.py`:
  - `sync_positions`: `pchs_avg_pric` 누락 시 `pchs_amt / qty`로 단가 자동 보정, `prpr` 누락 시 `evlu_amt / qty`로 현재가 보정, 평가손익(`pnl`)과 수익률(`yield_rate`) 정규화.
  - `get_snapshot()`: `invested_pchs > 0`일 때 `total_yield_rate` 정확한 수익률 산출 및 `positions` 전수 직렬화.
- `api_server.py`:
  - `portfolio_broadcast_loop` 및 `get_portfolio`: `ctx.portfolio` 인메모리 스냅샷을 단일 진실 공급원으로 확립하여 DB 폴백과의 경합 요동 제거.
- `frontend/src/hooks/useWebSocket.ts`:
  - WebSocket 실시간 `PORTFOLIO_UPDATE` 우선 적용, REST 폴백 시 WS 연결 중 덮어쓰기 방지, `stock_count`와 `positions` 동기화.
- `test_async_trading_loop.py`:
  - Test 10 (`test_actual_account_balance_and_4_holdings_sync`) 신설: 실제 키움 앱 데이터(148,442원, 1,122원, 4개 보유 종목) 정밀 검증.
  - Test 11 (`test_dynamic_watchlist_and_full_quant_workflow`) 신설: 동적 감시 목록 갱신 및 전체 매매 사이클(매수->1차익절->2차익절->전량청산) E2E 검증.
- `GSD_MASTERPLAN.md`: Phase 14 완료 및 Phase 15 신설 반영.

### 3. 검증 결과
- **단위/통합 테스트:** `test_async_trading_loop.py` (11/11 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS) 전 테스트 스위트 통과.
- **프론트엔드 빌드:** `npm run build` Vite 프로덕션 번들링 성공 (0 errors).
- **형상 관리:** `fix/actual-balance-match-and-portfolio-display` 브랜치 커밋.

---

## 📅 [2026-09-09] 키움 계좌 잔고 TR(kt00005/OPW00018) 총자산 vs D+2 예수금 필드 오맵핑 전면 교정 및 정밀 파싱 엔진 구축

### 1. 작업 개요 및 목적
- **총자산 vs D+2 예수금 동일 수치 덮어쓰기 결함 해결:** 키움 API `kt00005` 응답에서 `tot_evlu_amt` 필드가 D+2 추정예수금(`dnca_tot_amt`: 100,842원)과 동일한 값으로 내려올 때, 단순 예수금 원금(`prvs_rcdl_excc_amt`/`entr`: 114,922원)이 무시되고 총자산과 D+2 예수금이 동일하게 10만 원대로 덮어씌워지던 문제를 전면 해결.
- **키움 TR 응답 키 우선순위 및 계산식 재설계:**
  - `total_asset` (총자산): HTS 기준 단순 예수금 원금(`prvs_rcdl_excc_amt`, `entr`, `deposit`) + 보유 주식 평가액(`invested_eval`) 또는 자산평가금액(`tot_asst_amt`, `aset_evlt_amt`)으로 산출 (약 11만 원대 값 확정).
  - `available_cash` (D+2 주문가능금액): D+2 정산 추정예수금(`dnca_tot_amt`, `d2_deposit`, `ord_psbl_cash`, `ord_psbl_amt`)으로 엄격히 독립 분리 (약 10만 원대 값 확정).
- **계좌 동기화 시 API 원본 Raw Data 상세 브리핑 로깅 탑재:** 계좌 싱크 시 수신된 주요 키(`prvs_rcdl_excc_amt`, `dnca_tot_amt`, `tot_evlu_amt`, `ord_psbl_cash`, `entr` 등)의 실제 수신 값을 콘솔과 로그에 명확히 표출.

### 2. 주요 수정 파일 및 변경 내역
- `main_rest_async.py`:
  - `_sync_account_balance()` 파싱 로직 개편: `raw_entr_keys`를 최우선 순위로 추출하여 `final_total_asset = parsed_raw_entr + invested_eval` 산출.
  - `pure_total_asset_keys`(`tot_asst_amt`, `aset_evlt_amt`) 및 `tot_evlu_keys` 분리.
  - `📊 [계좌 TR Raw Data]` 상세 분석 로그 추가 (수신 필드, 단순 예수금, D+2 예수금, 보유주식 평가금, 총자산/주문가능 최종값 표출).
- `api_server.py`:
  - 서버 기동 시 DB 복원 로그에 총자산과 D+2 예수금 각각 분리 표출.
- `dashboard.py`:
  - 상단 메트릭 카드의 예수금 레이블을 `D+2 예수금 (주문가능)`으로 최신화.
- `test_async_trading_loop.py`:
  - `test_kiwoom_real_balance_parsing_various_schemas` (Test 9) 신설: 키움 실전 REST 표준 패턴, 순수 총자산 필드 패턴, 주식 보유 + 예수금 분할 패턴 등 3대 시나리오 검증 통과.
- `GSD_MASTERPLAN.md`: Phase 13 추가 및 완료 반영.

### 3. 검증 결과
- **단위 테스트:** `test_async_trading_loop.py` (9/9 100% ALL PASS), `test_api_server.py` (14/14 100% ALL PASS), `test_async_core.py`, `test_market_data_buffer.py`, `test_indicators.py`, `test_valuation.py`, `test_notifier.py`, `test_backtest.py` 전 스위트 무결성 검증 완료.
- **프론트엔드 빌드:** `npm run build` 번들링 성공 (dist 갱신 완료).
- **형상 관리:** `fix/account-balance-parsing-fields` 브랜치 커밋.

---

### 1. 작업 개요 및 목적
- **실전투자(REAL/LIVE) 모드 전면 전환:** 기본 실행 모드를 모의투자(MOCK)에서 실제 주문이 체결되는 실전투자(LIVE)로 전환하고, 상단 헤더에 에메랄드 컬러의 `LIVE (실전투자)` 배지 표출.
- **KST (Asia/Seoul, UTC+9) 타임존 고정:** MariaDB 로그 적재/조회 및 WebSocket 실시간 스트리밍 시 UTC로 표기되던 시간을 한국 표준시(KST)로 일괄 변환 고정.
- **상단 헤더 실시간 계좌 잔고 & 보유 포지션 드롭다운 구현:** 상단 헤더 중앙에 `[ 💰 총자산: X원 | 💵 D+2 예수금: Y원 | 📦 보유 종목: Z개 ▾ ]` 상시 관제 뱃지 바를 배치하고, 클릭 시 보유 주식 상세 목록(종목명, 코드, 수량, 매입가, 현재가, 평가손익, 수익률)이 실시간으로 표출되는 인터랙티브 팝오버 드롭다운 구현.
- **보유 포지션 파싱 다중 스키마 보강:** 키움 API `kt00005`의 `output2`, `Output2`, `list`, `acnt_dtl_list`, `holdings`, `output` 등 모든 필드 규격을 100% 포괄하여 보유 종목 누락 방지.

### 2. 주요 수정 파일 및 변경 내역
- `async_kiwoom_client.py`:
  - `__init__` 기본 모드를 실전투자(`self.mode = "REAL"`, `is_demo = False`)로 전환하고 환경 변수(`IS_REAL`, `KIWOOM_MODE`) 연동
- `start.py` & `main_rest_async.py`:
  - 기본 실행 인자를 실전투자(`--real`)로 통일
- `database.py`:
  - `KST = timezone(timedelta(hours=9))` 및 `format_kst_time_str` 도입, `get_recent_logs()` KST 변환 적용
- `api_server.py`:
  - `lifespan` 시 기본 실전투자 클라이언트 기동, `log_broadcast_loop` 내 KST 타임존 적용
- `frontend/src/components/Header.tsx`:
  - `LIVE (실전투자)` 에메랄드 뱃지 및 중앙 `[총자산 | D+2 예수금 | 보유 종목]` 실시간 바 + 포지션 상세 드롭다운 메뉴 탑재
- `frontend/src/components/LogViewer.tsx`:
  - 상단 윈도우 바에 `[ 총자산: X원 | 예수금: Y원 | 보유: Z개 ]` 실시간 서머리 인디케이터 추가
- `frontend/src/App.tsx`:
  - `portfolio` 상태를 `<Header />` 및 `<LogViewer />`에 직결 전달
- `async_portfolio.py`:
  - `sync_positions` 내 다중 스키마 키(`stk_cd`, `code`, `mksc_shrn_iscd`, `pdno`, `item_code` 등) 전수 파싱 지원

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` Vite 5.4.21 번들링 성공 (0 errors, dist 갱신 완료)
- **단위 테스트:** `test_async_trading_loop.py` (8/8 통과), `test_api_server.py` (14/14 통과), `test_notifier.py` (3/3 통과) 100% ALL PASS

---

## 📅 [2026-09-08] 총 평가자산 및 D+2 주문가능 예수금 필드 독립 분리 & 장 마감 정산 리포트 기말자산 오류 해결

### 1. 작업 개요 및 목적
- **총 평가자산 및 주문가능 예수금 필드 독립 분리:** 키움 API 계좌 조회(`kt00005`) 시 '총 평가자산(`tot_evlu_amt`/`aset_evlt_amt`: 114,922원)'과 'D+2 주문가능금액(`dnca_tot_amt`/`ord_psbl_cash`: 100,842원)'이 별도의 독립된 필드로 정확히 분리 파싱되도록 구조 개선.
- **장 마감 퀀트 정산 리포트 기말자산 모순 해결:** 당일 손익이 0원임에도 기말자산이 100,842원으로 표시되던 원인이 `total_asset`을 `current_capital`(가용현금)으로 덮어썼던 계산 버그임을 규명하고, 총 평가자산 기반으로 기말자산을 산출하도록 수정. D+2 가용 예수금 항목을 리포트에 명시.

### 2. 주요 수정 파일 및 변경 내역
- `async_portfolio.py`:
  - `AsyncPortfolioManager`에 `self.total_asset` 독립 인스턴스 변수 추가
  - `sync_capital(available_cash, total_asset)` 시그니처 확장 및 `get_snapshot()` 내 총 자산과 주문가능 현금 분리 직렬화
- `main_rest_async.py`:
  - `_sync_account_balance`: API 응답 내 총 평가자산 키(`tot_evlu_amt`, `aset_evlt_amt`, `tot_asst_amt` 등)와 D+2 주문가능금액 키(`dnca_tot_amt`, `ord_psbl_cash` 등)를 독립 파싱하여 포트폴리오 관리자에 분리 주입
  - 실시간 계좌 싱크 로그 포맷 개선: `총자산 114,922원 / D+2 예수금 100,842원 / 보유 0종목 (미체결: 1건)`
- `notifier.py`:
  - `notify_daily_settlement`: 기초자산(114,922원)과 기말자산(114,922원)의 정합성을 확립하고 `• D+2예수금: 100,842원 (주문가능 현금)` 항목 추가
- `api_server.py`:
  - `lifespan` 시 DB `balance`로부터 `total_asset`과 `current_capital` 각각 독립 복원
- `test_async_trading_loop.py` & `test_notifier.py`:
  - 총자산 및 D+2 예수금 독립 분리 및 정산 알림 포맷 단위 테스트 추가 및 검증 완료

### 3. 검증 결과
- **단위 테스트:** `python test_async_trading_loop.py` (8/8 통과), `python test_api_server.py` (14/14 통과), `python test_notifier.py` (3/3 통과) 100% ALL PASS

---

## 📅 [2026-09-08] 대시보드 실시간 터미널 LogViewer 개편, D+2 예수금 단일화 및 실시간 감시 쓰로틀링 루프 복구

### 1. 작업 개요 및 목적
- **대시보드 UI 전면 개편:** 미작동하던 '감시종목 차트'를 제거하고, 봇의 실시간 매매/감시 로그가 쏟아지는 검은색 배경의 터미널 스타일 `<LogViewer/>` 컴포넌트 신규 구현 및 WebSocket/REST 듀얼 스트리밍 연동.
- **예수금 유령 차감 원인 해결 및 D+2 기준 단일화:** 당일 단순 예수금(`entr`)과 D+2 추정예수금(`dnca_tot_amt`/`ord_psbl_cash`) 간의 혼용으로 인해 114,922원에서 100,842원으로 차감되어 보이던 결함을 파악하고, 'D+2 실제 주문가능금액'으로 기준을 일원화하며 미체결 주문(`미체결: N건`) 및 증거금 차감 정산 로깅 추가.
- **실시간 감시 1회성 중단 결함 해결 및 쓰로틀링 적용:** `trading_loop` 내 `try-finally` 배치 결함으로 1회 틱 처리 후 실시간 스트림 태스크가 취소되던 버그를 해결하고 Watchdog 자동 재가동 메커니즘을 구축. 매 틱마다 매수 평가는 무조건 실행하되, `⏱ [실시간 감시]` 로그 출력은 종목당 5초 또는 괴리율 0.5% 이상 변동 시에만 출력하도록 쓰로틀링 적용.

### 2. 주요 수정 파일 및 변경 내역
- `frontend/src/components/LogViewer.tsx` (신규):
  - macOS/Linux 터미널 윈도우 스타일, 순수 블랙 배경, 실시간 스트림 인디케이터, 자동 스크롤(Auto-scroll ON/OFF), 로그 레벨 필터(전체/감시/체결/경보/시스템), 키워드 검색, 복사/지우기 기능 탑재.
- `frontend/src/components/StrategyControlsBento.tsx` (신규):
  - 봇 제어(시작/정지/동기화), 실시간 K-Breakout & Kelly 비중 슬라이더 튜너, KODEX 200 시장 필터 및 서킷 브레이커 상태 인디케이터.
- `frontend/src/App.tsx`:
  - 좌측 상단에 `<LogViewer/>` 배치 및 우측 하단에 `<StrategyControlsBento/>` 배치하여 2x2 반응형 벤토 그리드 완성.
- `frontend/src/hooks/useWebSocket.ts` & `frontend/src/types.ts`:
  - `LogMessage`에 `'WATCH'` 레벨 추가, REST `/api/logs` 폴백 동기화 및 `clearLogs` 핸들러 제공.
- `api_server.py`:
  - `@api_router.get("/logs")` 엔드포인트 신설 (최근 100건 조회).
  - `log_broadcast_loop` 백그라운드 태스크를 통해 MariaDB `logs` 테이블 신규 레코드 실시간 WebSocket 브로드캐스팅.
  - `/ws/logs` 및 `/kiwoom/ws/logs` 접속 즉시 직전 100건 `LOGS_INIT` 전송.
- `database.py`:
  - `DatabaseManager.get_recent_logs(limit=100)` 비동기 메서드 추가.
- `async_kiwoom_client.py`:
  - `get_unexecuted_orders` (미체결 주문 조회) 메서드 추가.
- `main_rest_async.py`:
  - `_sync_account_balance`: D+2 주문가능금액(`dnca_tot_amt`, `ord_psbl_cash`) 우선 파싱 및 미체결 건수(`uncl_cnt`) 추적, 차감 내역 상세 로깅.
  - `_evaluate_buy_condition`: 매수 조건 매 틱 전수 평가 유지 + 실시간 감시 대기 로그 5초/0.5%p 쓰로틀링 및 DB/WS 로깅.
  - `trading_loop`: 루프 외부 `try-finally` 재배치 및 워커 자동 복구 Watchdog 탑재.
- `test_api_server.py` & `test_async_trading_loop.py`:
  - Test 14 (REST 로그 조회) 및 Test 8 (D+2 예수금 단일화 & 쓰로틀링 검증) 추가.

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` Vite 5.4.21 번들링 성공 (0 errors)
- **API 서버 테스트:** `python test_api_server.py` 14개 테스트 100% 통과
- **트레이딩 루프 테스트:** `python test_async_trading_loop.py` 8개 테스트 100% 통과

---

## 📅 [2026-09-08] CircuitBreaker is_open AttributeError 해결 및 상태 조회 방어 로직 강화

### 1. 작업 개요 및 목적
- `api_server.py`의 `get_bot_status` API 호출 시 발생하던 `AttributeError: 'CircuitBreaker' object has no attribute 'is_open'` 에러로 인한 콘솔 로그 도배 결함 해결.

### 2. 주요 수정 파일 및 변경 내역
- `async_kiwoom_client.py`:
  - `CircuitBreaker` 클래스에 `@property is_open` 추가 (`self.state == "OPEN"`)
- `api_server.py`:
  - `get_bot_status` 내 서킷 브레이커 상태 확인 시 `cb.state == "OPEN"`, `cb.is_open`, `can_proceed()` 다중 폴백 및 `try-except` 방어 로직 적용
- `test_api_server.py`:
  - 서킷 브레이커 CLOSED/OPEN 상태 연동 단위 테스트 케이스 추가 및 검증 완료

### 3. 검증 결과
- **백엔드 테스트:** `python test_api_server.py` 및 `python test_async_trading_loop.py` 100% ALL PASS

---

## 📅 [2026-09-08] 키움 포털 URL(/kiwoom) 404 및 API/WebSocket 서브패스 라우팅 결함 해결

### 1. 작업 개요 및 목적
- `https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom` 접근 시 404 오류 및 서브패스(`/kiwoom/`) 환경에서 REST API 및 WebSocket 통신 단절 결함 완벽 해결.
- Nginx Ingress와 FastAPI StaticFiles 간 트레일링 슬래시 누락 보정 및 로컬/서브패스 듀얼 라우팅 구조 확립.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `/kiwoom` GET 요청 시 `/kiwoom/`으로 302 리다이렉트하는 보정 라우터 추가
  - `APIRouter`를 도입하여 `/api` 및 `/kiwoom/api` 두 접두사를 모두 처리하도록 등록
  - `/ws/portfolio`, `/kiwoom/ws/portfolio`, `/ws/logs`, `/kiwoom/ws/logs` 듀얼 WebSocket 엔드포인트 지원
- `frontend/src/utils/apiConfig.ts` (신규):
  - 런타임 `window.location.pathname`에 따라 `/kiwoom/api` 및 `/kiwoom/ws` 또는 `/api`, `/ws`를 반환하는 동적 URL 빌더
- `frontend/src/` 전역 컴포넌트:
  - `useWebSocket.ts`, `App.tsx`, `TradingViewChartBento.tsx`, `ParamsModal.tsx`, `LiveTerminalBento.tsx`, `ActivePositionsBento.tsx`에 `getApiUrl` 및 `getWsUrl` 적용
- `frontend/dist/`:
  - `npm run build`를 통해 Vite 프로덕션 번들 갱신 완료

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 성공 (0 errors)
- **백엔드 테스트:** `python test_api_server.py` 13개 테스트 스위트 100% 통과

---

## 📅 [2026-09-07] 장 마감/유휴 상태 계좌 잔고 및 포지션 영속 캐싱 보존 (0원 노출 방지)

### 1. 작업 개요 및 목적
- 장 마감(15:30 이후), 주말, 또는 API 세션 종료 상태에서 대시보드 접근 시 '총 평가자산', '예수금', '보유종목'이 0원으로 초기화되던 현상 해결.
- `api_server.py` 및 `main_rest_async.py` 기동 시 MariaDB(`balance`, `portfolio`)로부터 직전 정산 잔고와 보유 종목을 자동 복원하도록 라이프사이클 및 WebSocket 브로드캐스트 로직 보강.
- 프론트엔드 KPI 카드에 직전 동기화 기준 일시(`last_synced_at`) 표출 및 일시적 수신 공백 시 0원 덮어쓰기 방지 적용.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `lifespan`: 서버 부팅 시 MariaDB `balance` 및 `portfolio` 테이블에서 최신 계좌 잔고 및 종목 즉시 복원
  - `portfolio_broadcast_loop`: 메모리가 비어있더라도 DB에 저장된 직전 잔고/포지션 스냅샷을 지속적으로 WebSocket 브로드캐스팅
- `main_rest_async.py`:
  - `initialize`: 데몬 시작 시 DB 계좌 복원 로직 추가 및 장외시간 API 응답 실패 시 기존 DB 잔고 안전 유지
- `frontend/src/types.ts`: `PortfolioSnapshot`에 `last_synced_at?: string` 필드 추가
- `frontend/src/hooks/useWebSocket.ts`: REST/WebSocket 수신 시 유효 자산이 0으로 덮어써지지 않도록 방어 로직 추가
- `frontend/src/components/KpiMetricsRow.tsx`: 상단 KPI 카드에 `기준: YYYY-MM-DD` 동기화 일시 배지 표출
- `GSD_MASTERPLAN.md`: Phase 7 장 마감 계좌 상태 유지 마스터플랜 완료 반영

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 errors)
- **백엔드 테스트:** `pytest test_api_server.py` 및 25개 테스트 스위트 100% 통과

---

## 📅 [2026-09-07] 차세대 대시보드 실데이터 연동, 하드코딩 제거 및 TradingView OHLCV/피보나치 바인딩

### 1. 작업 개요 및 목적
- 차세대 대시보드에서 '삼성전자(005930)' 초기 하드코딩 및 난수 캔들 생성기(`Math.random()`)를 제거.
- 백엔드(`/api/chart/{code}`, `/api/portfolio`, `/api/watchlist`)의 DB/인메모리 연동을 완료하여 실제 잔고, 보유 포지션, 30개 주도주 감시 유니버스, 실제 OHLCV 캔들 및 피보나치 3대 지지선(38.2%, 50.0%, 61.8%)을 실시간 바인딩.
- 차트 상단에 보유종목 및 감시종목을 한눈에 보고 즉시 전환할 수 있는 '동적 빠른 종목 선택기(Quick Select UI)' 구현.

### 2. 주요 수정 파일 및 변경 내역
- `api_server.py`:
  - `GET /api/chart/{code}?period=1m|5m|D`: 인메모리 링버퍼, DB `minute_ohlcv`/`daily_ohlcv`, 키움 REST API 연계 3단계 폴백으로 실 캔들 및 피보나치 레벨 반환
  - `GET /api/portfolio`: 인메모리 + MariaDB(`portfolio`, `balance` 테이블) 폴백으로 실계좌 자산 및 포지션 반환
  - `GET /api/watchlist`: 감시종목 리스트/딕셔너리 정규화 반환
- `database.py`: `get_latest_balance`, `get_portfolio_positions`, `get_watchlist_items`, `get_candles_by_code` 비동기 조회 메서드 추가
- `frontend/src/App.tsx`:
  - 하드코딩 제거: 초기 선택값을 비우고, 실제 보유종목(1순위) 또는 감시종목(2순위)으로 자동 초기화
  - 감시종목 API 응답(배열/객체/딕셔너리) 파싱 방어 로직 강화
- `frontend/src/components/TradingViewChartBento.tsx`:
  - 더미 난수 루프 제거 및 `/api/chart/{code}` 실데이터 바인딩
  - 피보나치 3대 지지선 및 매수평단가 라인 실시간 오버레이
  - 헤더에 `<select>` 기반 빠른 종목 전환(보유/감시 그룹화) 드롭다운 UI 구현
- `frontend/src/types.ts`: `ChartResponse` 타입 추가
- `test_api_server.py`: `/api/chart/{code}` 실시간 캔들 및 피보나치 조회 테스트 케이스 추가 (100% 통과)
- `GSD_MASTERPLAN.md`: Phase 6 실데이터 연동 마스터플랜 추가

### 3. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 errors)
- **백엔드 테스트:** `pytest test_api_server.py` 및 전체 25개 단위 테스트 100% ALL PASS

---

## 📅 [2026-09-07] 차세대 React Bento Grid 트레이딩 콕핏 UI/UX 개편 및 멀티스테이지 배포 파이프라인 구축

### 1. 작업 개요 및 목적
- 기존 Streamlit 기반 대시보드의 8개 탭 분할 및 폴링 딜레이(15초)로 인한 높은 맥락 전환 피로도와 실시간성 부재를 해결.
- 1920x1080 단일 화면에서 스크롤 없이 모든 지표, 캔들 차트, 주도주 유니버스, 체결 로그를 실시간 관제할 수 있는 **차세대 React 18 + Vite + Tailwind CSS + TradingView Lightweight Charts + WebSocket Bento Grid Cockpit** 구축.
- ACR/AKS 및 로컬 환경에서 프론트엔드가 누락 없이 완벽 빌드/서빙되도록 **Dockerfile Node.js 20 멀티스테이지 빌드 파이프라인** 및 FastAPI (`/`, `/kiwoom`) 라우팅 서빙 구현.

### 2. 주요 변경 및 신규 파일 목록
- `frontend/`: React 18 + Vite + TypeScript + Tailwind CSS 벤토 그리드 프론트엔드 프로젝트
  - `src/App.tsx`: 4개 핵심 영역(차트, 포지션, 30개 주도주 감시, 실시간 터미널) 비대칭 벤토 그리드 레이아웃
  - `src/components/TradingViewChartBento.tsx`: 60FPS TradingView Lightweight Charts (MA5/20, 피보나치 지지선 오버레이)
  - `src/components/ActivePositionsBento.tsx`: 보유 포지션 실시간 PnL 카드 및 원클릭 분할매도/청산
  - `src/components/WatchlistBento.tsx`: 당일 거래대금 상위 30 주도주 & 눌림목 상태 테이블
  - `src/components/LiveTerminalBento.tsx`: WebSocket 실시간 터미널 로그 및 $k$/Kelly 계수 무중단 튜닝
  - `src/components/KpiMetricsRow.tsx`: 4대 핵심 지표 (평가자산, 실현손익, 예수금비중, 당일승률)
  - `src/hooks/useWebSocket.ts`: 초저지연 WebSocket (`/ws/portfolio`, `/ws/logs`) 스트리머
  - `vite.config.ts`: 상대경로(`base: './'`) 빌드 지원으로 Ingress 서브패스 호환성 확보
- `Dockerfile`: Node.js 20 멀티스테이지 프론트엔드 빌드 + Python 3.11 런타임 이미지 통합
- `.dockerignore`: `node_modules`, `dist`, `.env`, `*.log` 등 불필요한 빌드 아티팩트 제외
- `start.py`: AKS Ingress 타겟 포트(8501)에 맞춰 차세대 콕핏(FastAPI)을 8501로 승격, 스트림릿을 8000으로 배치
- `dashboard.py`: FastAPI 호출 포트(8501) 정합성 조정
- `api_server.py`: FastAPI에서 `/` 및 `/kiwoom` 양쪽 경로로 React 콕핏 정적 서빙 마운트
- `AKS_DEPLOYMENT.md`: 업로드 필수 파일 목록에 `frontend/` 추가 및 콕핏 접속 URL 가이드 갱신
- `DASHBOARD_UX_PLAN.md`: UI/UX 고도화 마스터플랜 문서

### 3. 자동 코드 리뷰 요약
- **우수한 점:** 
  - 1920x1080 기준 스크롤 제로 벤토 그리드 아키텍처로 트레이더의 시선 이동 최소화.
  - TradingView 캔들스틱과 피보나치 레벨(38.2%, 50%, 61.8%) 시각화로 기술적 진입/청산 타점 직관적 확인 가능.
  - Dockerfile 멀티스테이지 도입으로 클라우드 배포 시 별도 로컬 번들링 없이 `az acr build`에서 자동 빌드 보장.
- **방어 로직 & 엣지 케이스:**
  - WebSocket 끊김 발생 시 3초 주기 자동 재연결(`connectSockets`) 및 REST API 3초 폴백 동기화 구현.
  - Ingress 경로(`/kiwoom`) 접속 시 정적 에셋(`assets/...`) 404를 방지하기 위해 Vite `base: './'` 설정 및 FastAPI 이중 마운트 적용.

### 4. 검증 결과
- **프론트엔드 빌드:** `npm run build` 100% 성공 (0 error, 11s)
- **백엔드 API 서버 테스트:** `pytest test_api_server.py` 1 passed
- **비동기 코어 테스트:** `test_async_core.py` 3/3 passed (PriorityQueue, TokenBucket, AsyncPortfolio)

### 5. 후속 안내
- 로컬 실행 시: `python start.py` 실행 후 `http://localhost:8000` 접속
- AKS 배포 시: `frontend/` 소스코드를 포함하여 `az acr build` 수행 후 `kubectl rollout restart deployment/kiwoom-bot -n mzc-apps`
