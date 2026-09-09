# 실험 20 — 저복잡도 선형 대조군

## 실행 전 고정 계획

실험 19에서 같은 입력의 가중 MLP/CNN이 기존 돌파를 안정적으로 개선하지 못했다.
유효 비중첩 거래 표본이 작은 상황에서 비선형 모델의 복잡도를 줄여도 같은 문제가 남는지 확인한다.
이 문서 작성 시 미구현·미실행이며 효과가 있다는 주장이 아니다.

- 입력: 기존 32시간 × 5개 특성을 펼친 160개 값. 추가 지표·기간·필터 없음.
- 모델: `Linear(160, 1)` 뒤 sigmoid, 총 파라미터 161개. 연구 전용이며 운영 전략 등록 없음.
- 손실: 비가중 BCE와 실험 19의 절대 순수익 가중 BCE를 각각 비교한다.
  비가중 진입은 기존 0.50 및 학습 손익 기준 두 방식, 가중 진입은 고정 `점수 > 0.50`.
- 훈련: 같은 시드 17/29/43, Adam 0.001, weight decay 0.0001, batch 128, gradient clip 1,
  최대 100 epoch와 patience 10, 최적 검증 체크포인트 복원. 기존 평균/표준편차 정규화 유지.
- 자료·분할: 16~19와 같은 원자료, 완결 사건, 6분기 학습·2분기 검증·다음 분기 평가.
  실제 청산 봉 종료가 경계를 넘는 레이블 제외. 겹치는 사건을 독립 표본으로 취급하지 않는다.
- 비교: 같은 블록·비용·위험 조건의 원래 돌파·현금·각 목적에 맞는 상수 예측 및 기존 MLP/CNN.
- 모든 시드·4개 비용/지연의 기존 사전 기준 유지. 거래 없는 구간도 포함한다.
  블록별 중앙값, 대응 차이, 독립 계좌 손익 합계, 낙폭·위험 중단·자연 청산·오류 분해를 함께 보고한다.
- 모델 변경 외 추가 재최적화·실거래 승격 없음. 기존에 관찰한 자료의 탐색이며 새로운 홀드아웃이 아니다.

학습 100회 제한이 일부 모델을 제한할 수 있다는 사실도 기록한다. 결과를 보고 같은 평가 기간에서
최대 epoch나 임계값을 계속 바꾸는 것은 이 실험 범위에서 하지 않는다.

## 재현 명령

```bash
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-linear-20-fixed \
  --validation-quarters 2 --model-family linear
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-linear-20-payoff \
  --validation-quarters 2 --model-family linear --threshold-mode payoff
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-linear-20-weighted \
  --validation-quarters 2 --model-family linear --loss-weighting absolute-return
```

각 조건의 모델 57개를 학습한다. fixed/payoff는 학습이 같으므로 두 조건의 체크포인트가
정확히 같은지 확인해 임계값 변경과 모델 변경을 분리한다. 모델 이름은 연구 아키텍처에만
추가하며 운영 `STRATEGY_PARAMETERS`에는 등록하지 않는다.

실행 결과: [실험 20 결과](breakout-meta-results-20.md).
