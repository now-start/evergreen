# 백테스트 전략 의사결정 노트

이 문서는 루트의 `backtest_playground.ipynb`를 실행한 뒤 v1~v5 전략을 비교하는 기준을 정리한다. 노트북이 README이자 스모크 테스트이며, 현재 문서는 실행 결과를 해석하는 보조 문서다.

## 실행 기준

- 데이터: 공식 Upbit SDK 공개 일봉 데이터
- 기본 실행 버전: v1~v5 전체
- 모델 코드: `evergreen_backtest/models/v1.py`~`v5.py`
- 평가/선택 코드: `evergreen_backtest/backtest.py`, `evergreen_backtest/optimizer.py`, `evergreen_backtest/walk_forward.py`
- 전략 설명서: `backtest_playground.ipynb`의 "전략 설명서" 섹션
- 실행 결과표: 노트북 변수 `요약`
- 계약 파일: `outputs/backtests/latest/strategy_contracts.json`
- 차트 파일: `outputs/backtests/latest/equity_walk_forward.png`

```bash
uv sync
uv run jupyter lab backtest_playground.ipynb
```

## 공통 계약

모든 버전은 같은 연동정의를 따른다.

- 입력: `StrategyInput(candles, signalIndex, position, params)`
- 현재 비중: `position.positionRatio`는 `evergreen.trading.signal-order-notional` 기준 현재 포지션 평가 비중
- 출력: `StrategyEvaluation(decision, diagnostics)`
- 실행 액션: `decision.action`은 `BUY`, `SELL`, `HOLD` 중 하나
- 목표 비중: `decision.targetPositionRatio`
- 판단 이유: `decision.signalReason`
- Java 설정값: `javaParamsCamelCase`
- 백테스트 전체 선택값: `selectedParamsCamelCase`
- 워크포워드 선택값: `walkForwardByVersion.<version>.selectedVersion`, `walkForwardByVersion.<version>.javaParamsCamelCase`

`javaInteropReady=true`이면 해당 버전이 공통 Python/Java 연동정의를 만족한다는 뜻이다. 모든 버전은 `StrategyInput -> StrategyEvaluation` 계약으로 Java와 맞물린다.

## 후보 전략

| 버전 | 핵심 아이디어 | 포지션 방식 | 주요 조정값 |
|---|---|---|---|
| v1 | MA + RSI | 전량 진입/청산 | `rsi_buy`, `ma_len`, `ma_slope_days` |
| v2 | EMA 레짐 + ATR 손절 | 전량 진입/청산 | `regime_ema_len`, `atr_period`, `atr_trail_multiplier`, `regime_band` |
| v3 | 레짐 + 변동성 목표 비중 | 동적 비중 | `vol_target`, `max_leverage`, `min_exposure` |
| v4 | 레짐 + 주간 EMA 필터 | 전량 진입/청산 | `weekly_ema_len`, v2 계열 파라미터 |
| v5 | 변동성 국면별 ATR 손절 | 전량 진입/청산 | `atr_mult_low_vol`, `atr_mult_high_vol`, `vol_regime_lookback`, `vol_regime_threshold` |

## 비교 기준

노트북의 `요약` 표는 워크포워드 결과만 보여준다. 각 행은 `range_profile + version` 조합이며, 해당 버전을 고정하고 하이퍼파라미터만 창마다 다시 고른 결과다. 목적은 운용 중 버전을 계속 갈아타는 것이 아니라, 버전 로직과 하이퍼파라미터, 시계열 범위 후보를 함께 비교해 최종 적용할 단일 버전을 고르는 것이다.

- 수익성: `cagr`, `final_equity`
- 위험: `mdd`
- 운용성: `trades`
- 시장 대비 성과: `final_equity_bh`와의 차이
- 범위 민감도: `range_profile`, `train_window_days`, `test_window_days` 조합별 성능 차이

단일 수익률만으로 전략을 고르지 않는다. `final_equity`가 높아도 `mdd`가 크거나 `trades`가 지나치게 많으면 우선순위를 낮춘다. 워크포워드는 각 시점에서 과거 데이터만으로 해당 버전의 하이퍼파라미터를 다시 고른 뒤 다음 구간에 적용한 결과라서, 정적 전체 기간 재평가보다 Java 적용 후보 판단에 더 가깝다.

## 의사결정 규칙

1. 각 워크포워드 window의 학습 구간에서는 버전별 하이퍼파라미터 후보 중 Calmar 유사 점수(`cagr / abs(mdd)`)를 우선하고, 동률이면 `cagr`, `final_equity` 순으로 후보를 고른다.
2. 선택된 후보는 다음 out-of-sample 구간에만 적용한다. 미래 데이터를 보고 같은 구간의 파라미터를 고르지 않는다.
3. 노트북은 여러 `range_profile`을 합친 뒤 `calmar_like`, `cagr`, `final_equity` 순으로 정렬해 추천 후보를 고른다.
4. 실제 Java 적용 후보는 추천 행의 `range_profile`에 해당하는 실행 결과와 `walkForwardByVersion.<version>`의 마지막 window, `javaParamsCamelCase`를 기준으로 정한다.
5. v3는 `targetPositionRatio`가 0.0과 1.0 사이 또는 1.0을 넘는 값을 낼 수 있으므로, Java 주문 계층이 `signal-order-notional` 기준 목표 비중으로 부분 매도와 증액 매수를 실행한다. LIVE 현물 주문은 실제 가용 KRW 안에서만 증액된다.

## 결과 기록 절차

1. 노트북에서 `실행할_버전`을 정한다. 기본값은 `("all",)`이다.
2. 전체 셀을 실행한다.
3. `요약` 표에서 `range_profile + version` 조합을 비교한다.
4. `strategy_contracts.json`에서 추천 후보의 `walkForwardByVersion.<version>`, `javaParamsCamelCase`, `windows`를 확인한다.
5. 추천 범위의 워크포워드 자산곡선에서 후보 버전 결과가 급격히 훼손되는지 확인한다.

## 주의사항

- 공식 Upbit SDK는 데이터 조회에 사용한다. RSI, EMA, ATR 같은 지표 계산은 SDK가 제공하지 않아 모델 코드에서 계산한다.
- `evergreen_backtest/models/v*.py`에는 모델의 신호/목표비중 계산만 둔다. 수익률, MDD, CAGR, 후보 점수화, 워크포워드 선택은 공통 평가/선택 모듈에서 처리한다.
- `targetPositionRatio`는 실행 계약의 일부다. Java에서 `action`만 보고 주문하면 v3 같은 비중 조절 전략을 잘못 실행할 수 있다. Java 주문 계층은 `signal-order-notional` 기준 목표 비중으로 전량 진입/청산과 중간 비중 조정을 같은 계약으로 처리한다.
