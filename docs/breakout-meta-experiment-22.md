# 실험 22 — 겹치는 돌파 사건의 학습 가중 기여

## 실행 전 고정 계획

21번에서도 일관된 개선이 없었다. 같은 추세의 연속 돌파 사건들이 비슷한 가격 경로와
큰 수익을 여러 번 학습시키는 것이 원인인지 다음 가설로 검증한다. 원인으로 확정하지 않는다.

- 대조: 실험 21 `short-200` 손익 가중 MLP/CNN. 맥락 입력에서 좋은 시드를 골라 쓰지 않는다.
- 후보: 같은 입력·사건·레이블·시각·모델 크기에서 손실 가중치만 변경한다.
- 완결 사건 j의 시간 구간은 `[signal_time_j, label_time_j)`로 정한다.
  한 시간 격자의 시각 t마다 현재 분할 내 완결 사건의 겹침 수 `c_t`를 계산한다.
- 사건별 계수 `u_j = mean(1/c_t, t in [signal_j, label_j))`로 정하고
  후보 가중치는 `abs(net_return_j) * u_j`로 정한다. 겹치지 않은 사건은 계수 1이다.
  계수가 작은 사건도 삭제하지 않으며 추가 임계값이나 지수를 탐색하지 않는다.
- 학습용 겹침 수는 시간 경계 제거가 끝난 학습 표본만으로 계산한다.
  검증용 겹침 수는 검증 표본만으로 별도 계산한다. 평가 구간·중도 관측 종료 사건은
  학습/검증 가중치 계산에 넣지 않는다. 각 분할에서 평균 가중치 1로 정규화한다.
- 상수 대조 점수도 그 학습 가중치의 양수 클래스 비중으로 계산한다.
  두 조건의 상수 결과가 달라질 수 있으나 원래 돌파·현금과 평가 범위는 같아야 한다.
- 6분기 학습·2분기 검증·다음 분기 평가, 시드 17/29/43, 최대 100 epoch·patience 10,
  최적 검증 복원, 점수 > 0.50, 비용·지연 4조건·전액 매매·위험 중단과 기존 채택 기준 유지.
- 사건·학습/검증 시각 해시는 대조와 같아야 한다. 가중치/겹침 계수 해시는 별도 저장한다.
  비중첩 사건·경계 맞닿음·부분 중첩·순열·분할 밖 사건 제외를 회귀 테스트한다.
- 이것은 상관을 제거하거나 독립 표본을 만들어내는 방법이 아니다. 효과를 실험으로 확인한다.
  이미 본 역사 자료의 탐색이며 신규 홀드아웃 검증·수익 보장이 아니다.
- 실거래·Config Server·계좌·DB·배포·커밋·푸시는 변경하지 않는다.

## 재현 명령

```bash
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-overlap-22-control \
  --validation-quarters 2 --loss-weighting absolute-return --feature-set short-200
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-overlap-22-candidate \
  --validation-quarters 2 --loss-weighting absolute-return --feature-set short-200 --overlap-adjusted
```

대조의 프로토콜 번호는 원래 조건인 21, 후보는 22다. 대조를 재실행하여 21번의 저장 모델과
체크포인트가 같은지 확인한다. 출력 경로는 덮어쓰지 않는다.

실행 결과: [실험 22 결과](breakout-meta-results-22.md).
