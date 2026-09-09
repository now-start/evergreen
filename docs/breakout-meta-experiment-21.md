# 실험 21 — 돌파·청산 맥락 입력

## 실행 전 고정 계획

20번의 저복잡도 대조까지 안정적 개선에 실패했다. 다음은 입력 정보가 부족한지 검증한다.
계획을 먼저 고정한 뒤 구현·실행한다. 결과는 별도 기록하며 수익성 개선을 전제하지 않는다.

- 대상: 19번의 손익 가중 MLP/CNN, 시드 17/29/43. 같은 32×5 크기와 파라미터 수 유지.
- 대조 입력 `short-200`: 기존 단기 5개 채널을 그대로 쓰되 준비 구간을 200시간으로 맞춘다.
- 후보 입력 `breakout-context`: 각 확정 봉 i에 아래 5개 채널을 계산해 최근 32개 시점을 사용한다.
  1. `log(close_i / max(high[i-168:i]))` — 현재 봉 이전 168시간 돌파 상단 대비 위치.
  2. `log(close_i / min(low[i-48:i]))` — 현재 봉 이전 48시간 청산 하단 대비 위치.
  3. `log(SMA48_i / SMA168_i)` — 현재까지의 단기/장기 추세 비율.
  4. `log1p(volume_i) - log1p(mean(volume[i-24:i]))` — 이전 24시간 대비 거래량.
  5. `std(log(close_t / close_(t-1)), t=i-23..i)` — 최근 24시간 수익 변동성(모집단 표준편차).
- 입력을 추가하는 것이 아니라 5개 채널을 교체하는 비교다. 단기 봉 모양 정보 일부를 잃는 것도
  효과에 포함한다. 모델 차원 증가나 미래 봉 참조는 허용하지 않는다.
- 첫 유효 입력은 이전 168봉 + 현재 봉을 시작으로 한 32시점 창, 총 200개 확정 봉이 필요하다.
  대조군도 동일한 준비 구간·사건 시각으로 제한하고, 원래 돌파·현금·상수도 그 평가 범위를 사용한다.
- 원자료·6분기 학습·2분기 검증·다음 분기 평가·레이블 완료 시각 경계 제거·비용·위험 규칙 유지.
- 학습은 절대 순수익 가중 BCE, 점수 > 0.50, 최대 100 epoch·patience 10·최적 검증 복원.
  모델과 상수 점수를 승률로 해석하지 않는다.
- 19번의 169시간 준비 결과와 직접 수치만 비교하지 않는다. `short-200`과 후보의 사건/시간
  해시·원래 돌파/현금 결과 일치가 필수다. 줄어드는 자료 범위도 명시한다.
- 모든 시드·4개 비용/지연의 기존 기준 유지. 이미 관찰한 자료의 탐색이며 신규 홀드아웃이 아니다.
- 실거래·Config Server·계좌·DB·배포·커밋·푸시 변경 없음.

## 재현 명령

```bash
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-context-21-control \
  --validation-quarters 2 --loss-weighting absolute-return --feature-set short-200
.venv/bin/python -m evergreen.research.experiments.breakout_meta \
  --source data/regime-08-2020-2026-public --output outputs/breakout-context-21-candidate \
  --validation-quarters 2 --loss-weighting absolute-return --feature-set breakout-context
```

출력 경로가 이미 존재하면 덮어쓰지 않고 실패한다.

실행 결과: [실험 21 결과](breakout-meta-results-21.md).
