# 백테스트 전략 의사결정 노트

이 문서는 루트의 `backtest_playground.ipynb`를 실행한 뒤 v1~v5 전략을 비교하고 운영 후보를 고르는 기준을 정리한다. 노트북이 README이자 스모크 테스트이며, 현재 문서는 실행 결과를 해석하는 보조 문서다.

## 실행 기준

- 데이터: 기본값은 공식 Upbit SDK를 사용하는 `upbit`, 네트워크 없이 확인할 때는 `synthetic`
- 전략 코드: `evergreen_backtest/strategies/v1.py`~`v5.py`
- 전략 설명서: `backtest_playground.ipynb`의 "전략 설명서" 섹션
- 실행 결과표: 노트북 변수 `요약`
- 계약 파일: `outputs/backtests/latest/strategy_contracts.json`
- 차트 파일: `outputs/backtests/latest/equity_test.png`

```bash
uv sync
uv run jupyter lab backtest_playground.ipynb
```

## 공통 계약

모든 버전은 같은 연동정의를 따른다.

- 입력: `StrategyInput(candles, signalIndex, position, params)`
- 출력: `StrategyEvaluation(decision, diagnostics)`
- 실행 액션: `decision.action`은 `BUY`, `SELL`, `HOLD` 중 하나
- 목표 비중: `decision.targetPositionRatio`
- 판단 이유: `decision.signalReason`

`ioContractReady=true`이면 해당 버전이 공통 Python/Java 연동정의를 만족한다는 뜻이다. `javaEngineReady=true`이면 해당 버전의 Java 네이티브 엔진까지 구현되어 있다는 뜻이다.

## 후보 전략

| 버전 | 핵심 아이디어 | 포지션 방식 | 주요 조정값 |
|---|---|---|---|
| v1 | MA + RSI | 전량 진입/청산 | `rsi_buy`, `ma_len`, `ma_slope_days` |
| v2 | EMA 레짐 + ATR 손절 | 전량 진입/청산 | `regime_ema_len`, `atr_period`, `atr_trail_multiplier`, `regime_band` |
| v3 | 레짐 + 변동성 목표 비중 | 동적 비중 | `vol_target`, `max_leverage`, `min_exposure` |
| v4 | 레짐 + 주간 EMA 필터 | 전량 진입/청산 | `weekly_ema_len`, v2 계열 파라미터 |
| v5 | 변동성 국면별 ATR 손절 | 전량 진입/청산 | `atr_mult_low_vol`, `atr_mult_high_vol`, `vol_regime_lookback`, `vol_regime_threshold` |

## 비교 기준

노트북의 `요약` 표에서 다음 지표를 우선 본다.

- 수익성: `cagr`, `final_equity`
- 위험: `mdd`
- 운용성: `trades`
- 시장 대비 성과: `final_equity_bh`와의 차이
- 검증 구간 차이: `validation`, `test`, `full` 단계별 성능 차이

단일 수익률만으로 전략을 고르지 않는다. `test`에서 수익이 높아도 `mdd`가 크거나 `trades`가 지나치게 많으면 운영 후보 우선순위를 낮춘다.

## 의사결정 규칙

1. `test` 구간에서 손실이 과도한 전략은 제외한다.
2. `mdd`가 비슷하면 `cagr`와 `final_equity`가 높은 전략을 우선한다.
3. 성과가 비슷하면 `trades`가 적은 전략을 우선한다.
4. v3는 `targetPositionRatio`가 0.0과 1.0 사이 또는 1.0을 넘는 값을 낼 수 있으므로, Java 주문 계층이 목표 비중 실행을 지원하기 전에는 관찰 후보로 둔다.
5. Java 운영 후보는 `javaEngineReady=true`인 버전을 우선한다.

## 결과 기록 절차

1. 노트북에서 `실행할_버전`과 `데이터_소스`를 정한다.
2. 전체 셀을 실행한다.
3. `요약` 표를 기준으로 `validation`, `test`, `full`을 비교한다.
4. `strategy_contracts.json`에서 `lastEvaluation.decision`과 선택 파라미터를 확인한다.
5. `equity_test.png`로 테스트 구간 자산곡선이 급격히 훼손되는지 확인한다.

## 주의사항

- 공식 Upbit SDK는 데이터 조회에 사용한다. RSI, EMA, ATR 같은 지표 계산은 SDK가 제공하지 않아 전략 코드에서 계산한다.
- `targetPositionRatio`는 실행 계약의 일부다. Java에서 `action`만 보고 주문하면 v3 같은 비중 조절 전략을 잘못 실행할 수 있다. 현재 Java 주문 계층은 0.0/1.0 목표 비중만 실제 주문하고, 중간 비중은 스킵한다.
- `synthetic` 결과는 실행 구조 확인용이다. 운영 후보 판단은 실제 Upbit 데이터로 다시 실행한 결과를 기준으로 한다.
