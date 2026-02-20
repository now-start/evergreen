# Evergreen v5 운영전략 기술설계서

## 1. 설계 목표
- 연구용 `src/test/python/v5.py` 로직을 운영 서비스 구조로 이관한다.
- 데이터-전략-평가-리포트 계층을 분리해 유지보수성과 재현성을 높인다.
- `v4`를 shadow 전략으로 병행 계산 가능하도록 동일 인터페이스를 강제한다.

## 2. 아키텍처 개요
## 2.1 계층
- Ingestion Layer
  - OHLCV 수집, 정합성 검사, canonical dataset 생성
- Strategy Engine Layer
  - `V5Engine` (primary), `V4Engine` (shadow)
- Execution Layer
  - 잔액 조회, 주문 가능 조회, 주문 생성/조회/취소
  - 주문 상태 동기화(reconciliation)
- Evaluation Layer
  - hold-out / walk-forward 실행기
- Storage Layer
  - runs, params, rows, summaries 저장
- Serving Layer
  - 결과 조회 API, 리포트/대시보드

## 2.2 실행 흐름 (일일 배치)
1. 데이터 수집 및 전일 기준 데이터셋 확정
2. `config_version` 로드
3. `v5` 신호 계산 + shadow `v4` 계산
4. 결과 저장(run metadata + summary + rows)
5. 리포트 생성(JSON/CSV/이미지)
6. 알림 전송(성공/실패, 리스크 이벤트)

## 2.3 실주문 실행 흐름 (LIVE 모드)
1. `Signal` 생성 후 주문 의도(`target_position`) 계산
2. `accounts`/`orders/chance` 조회로 실행 가능성 검증
3. 리스크 게이트 통과 시 주문 생성
4. 주문 UUID 저장 후 폴링으로 체결상태 조회
5. 부분체결/미체결 timeout 시 취소 또는 대체주문 정책 적용
6. 체결결과를 포지션/성과 스토어에 반영

## 3. 모듈 설계
## 3.1 데이터 모델
### CandleBar
- `timestamp`, `open`, `high`, `low`, `close`, `volume`

### StrategyParamsV5
- `fee_per_side`, `slippage`
- `regime_ema_len`, `atr_period`
- `atr_mult_low_vol`, `atr_mult_high_vol`
- `vol_regime_lookback`, `vol_regime_threshold`
- `regime_band`

### BacktestRowV5 (핵심 필드)
- price/regime:
  - `close`, `regime_anchor`, `regime_upper`, `regime_lower`
- volatility/adaptive exit:
  - `atr_price_ratio`, `vol_percentile`, `volatility_is_high`
  - `atr_trail_multiplier_applied`, `atr_trail_stop`
- decision/state:
  - `setup_buy`, `setup_sell`, `trail_stop_triggered`
  - `buy_signal`, `sell_signal`, `pos_open`, `trade`
- performance:
  - `ret_oo`, `equity`, `equity_bh`

### BacktestSummary
- `final_equity`, `final_equity_bh`, `cagr`, `mdd`, `trades`, `range`

## 3.2 엔진 인터페이스
```python
class StrategyEngine(Protocol):
    def run(self, bars: list[CandleBar], params: Any) -> BacktestResult: ...
```

`V5Engine`와 `V4Engine`은 동일 인터페이스를 구현한다.

## 4. 핵심 알고리즘
## 4.1 regime 계산
- 일봉 close에 대해 EMA(`regime_ema_len`) 계산
- `regime_band`로 upper/lower band 생성
- close의 위치와 이전 regime을 이용해 `BULL/BEAR/UNKNOWN` 결정

## 4.2 adaptive ATR exit
1. ATR 계산(`atr_period`)
2. `atr_price_ratio = ATR / close`
3. lookback 윈도우(`vol_regime_lookback`) 기준 백분위 산출
4. 백분위가 `vol_regime_threshold` 이상이면 high-vol
5. high-vol이면 `atr_mult_high_vol`, 아니면 `atr_mult_low_vol` 적용
6. `trail_stop = highest_close_since_entry - multiplier * ATR`

## 4.3 주문/포지션 상태 전이
- Entry:
  - `BEAR -> BULL` 전환 시 진입
- Exit:
  - `BULL -> BEAR` 또는 `trail_stop_hit`
- Cost:
  - `turnover * (fee_per_side + slippage)`

## 5. 저장소 설계
## 5.1 테이블 제안
- `strategy_run`
  - `run_id`, `strategy_version`, `mode(primary/shadow)`, `config_version`
  - `dataset_id`, `started_at`, `ended_at`, `status`
- `strategy_run_param`
  - `run_id`, `param_json`, `param_hash`
- `strategy_run_summary`
  - `run_id`, `final_equity`, `final_equity_bh`, `cagr`, `mdd`, `trades`, `range`
- `strategy_run_row`
  - `run_id`, `ts`, `row_json` (또는 주요 필드 정규화 컬럼)

## 5.2 인덱스
- `strategy_run(strategy_version, started_at desc)`
- `strategy_run_summary(run_id)`
- `strategy_run_row(run_id, ts)`

## 6. API 설계 (초안)
## 6.1 조회
- `GET /api/backtest/runs?strategy=v5&limit=50`
- `GET /api/backtest/runs/{run_id}/summary`
- `GET /api/backtest/runs/{run_id}/rows?from=...&to=...`
- `GET /api/backtest/compare?base=v5&shadow=v4&period=30d`

## 6.2 실행
- `POST /api/backtest/run`
  - body: `strategy_version`, `config_version`, `dataset_range`, `mode`

## 6.3 거래 실행(초안)
- `POST /api/trading/signal-execute`
  - body: `strategy_version`, `signal_ts`, `mode(PAPER|LIVE)`
- `GET /api/trading/orders/{uuid}`
- `POST /api/trading/orders/{uuid}/cancel`

## 7. 검증 설계
## 7.1 테스트 계층
- Unit Test
  - regime 계산, ATR 계산, percentile 계산, state transition
- Integration Test
  - 입력 데이터→결과 저장까지 end-to-end
- Regression Test
  - 연구 엔진(`v5.py`) 대비 요약/row 필드 diff 허용범위 검증

## 7.2 회귀 허용치 예시
- `final_equity` 상대 오차 < 1e-6
- `cagr`, `mdd` 절대 오차 < 1e-6
- `trades` 완전 동일

## 8. 운영/모니터링
## 8.1 필수 메트릭
- 배치:
  - 실행시간, 성공/실패, 재시도 횟수
- 전략:
  - 일일 신호 수, turnover, 실시간 DD 추정, exposure
- 데이터 품질:
  - 결측 바 수, 중복 제거 수, 타임스탬프 이상 건수

## 8.2 알림 조건
- 데이터 정합성 실패
- MDD 임계치 초과
- turnover 급증
- v5-v4 성능 격차 임계치 이탈
- 주문 실패율 급증
- 미체결 잔존 시간 초과

## 9. 마이그레이션 계획
1. 코드 이관:
  - `v5.py` 핵심 로직을 운영 모듈로 이동
2. 병행 검증:
  - 기존 연구 노트북 결과와 1차 diff
3. shadow 운영:
  - `v5(primary)` + `v4(shadow)` 동시 4주
4. 커트오버:
  - 승인 기준 충족 시 운영 정책 확정

## 10. 오픈 이슈
- 실거래 주문 단위/슬리피지 모델의 시장별 보정 방식
- 거래소 장애/휴장일 처리 정책
- 장기적으로 다자산 확장 시 포지션 합산 리스크 규칙
- 주문 재시도/대체주문 시 멱등성 보장 방식(identifier 키 전략)

## 11. 외부 연동 참조
- Upbit API 연동 상세 계약은 아래 문서를 단일 출처로 사용한다.
  - `docs/upbit_integration_spec.md`
