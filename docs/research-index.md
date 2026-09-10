# 연구 목차 — 실험01–72

2026-09-10 기준. 이전 실험 문서의 ‘현재’, ‘후속 계획’은 작성 당시 상태다.
최신 후보와 미완료 작업은 [승격 준비서](strategy-promotion.md)를 기준으로 읽는다.
실험은 수익 보장이 아니며, 이미 관찰한 기간의 반복 비교를 새 최종 검증으로 세지 않는다.

## 주제별 탐색

| 실험 | 주제 | 대표 문서 |
|---|---|---|
| 01–02 | 추세·평균회귀·RSI 기준선 | [01](strategy-experiment-01.md), [02](strategy-experiment-02.md) |
| 03–04 | MLP/CNN 학습·검증 기반 조기 종료 | [03](deep-learning-experiment-03.md), [04](early-stopping-experiment-04.md) |
| 05–07 | 규칙·딥러닝 혼합, 비용 차감 목표, 돌파 | [05](hybrid-experiment-05.md), [06](meta-experiment-06.md), [돌파](breakout-strategy.md) |
| 08–14 | 장세 전환·시드·목표 기간·장기 입력 | [08](regime-experiment-08.md), [09–11](regime-experiment-09-11.md), [12](regime-experiment-12.md), [13](regime-experiment-13.md), [14](regime-experiment-14.md) |
| 15–23 | 돌파 거래 수익성 학습·가중치·선형·MLP 앙상블 | [15–17](breakout-meta-results-15-17.md), [18](breakout-errors-18.md), [19](breakout-meta-results-19.md), [20](breakout-meta-results-20.md), [21](breakout-meta-results-21.md), [22](breakout-meta-results-22.md), [23](breakout-meta-results-23.md) |
| 24–29 | 청산 채널·재진입·돌파 확인·거래량·윗꼬리·예산 | [24](breakout-exit-results-24.md), [25](breakout-exit-results-25.md), [26](breakout-confirmation-26.md), [27](breakout-volume-27.md), [28](breakout-wick-28.md), [29](breakout-budget-29.md) |
| 30–39 | 실패 돌파·추적 손절·TR·청산 예약 | [30](breakout-failure-30.md), [31](breakout-failure-confirmation-31.md), [32](breakout-failure-buffer-32.md), [33](breakout-entry-cap-33.md), [34](breakout-trailing-34.md), [35](breakout-profit-trailing-35.md), [36](breakout-adaptive-trailing-36.md), [37](breakout-tr-expansion-37.md), [38](breakout-tr-compression-38.md), [39](breakout-rejection-latch-39.md) |
| 40–48 | 진입 손절·2봉 확인·채널 리셋·적응 추적 | [40](breakout-entry-stop-40.md), [41](breakout-entry-stop-floor-41.md), [42](breakout-entry-stop-expansion-42.md), [43](breakout-entry-stop-confirmation-43.md), [44](breakout-entry-stop-channel-reset-44.md), [45](breakout-entry-stop-profit-trail-reset-45.md), [46](breakout-entry-stop-adaptive-trail-reset-46.md), [47](breakout-entry-stop-loss-reset-47.md), [48](breakout-entry-stop-confirmed-profit-reentry-48.md) |
| 49–51 | 추세 청산 유예·진입 예산·긴 추세 | [49](breakout-entry-stop-trend-confirmation-49.md), [50](breakout-entry-stop-budget-50.md), [51](breakout-entry-stop-trend-context-51.md) |
| 52–58 | 청산 시점 관찰·연장·위험 원인·선제 청산 | [52](breakout-exit-extension-data-52.md), [53](breakout-exit-checkpoint-53.md), [54](breakout-exit-higher-low-54.md), [55](breakout-extension-floor-55.md), [56](breakout-risk-attribution-56.md), [57](breakout-liquidation-buffer-57.md), [58](breakout-liquidation-buffer-long-58.md) |
| 59–63 | 진입 맥락·반사실 비교·전략 계열·비용 분해 | [59](breakout-entry-context-59.md), [60](breakout-entry-path-context-60.md), [61](breakout-entry-counterfactual-61.md), [62](strategy-family-62.md), [63](cost-path-63.md) |
| 64–67 | 진입 창·추세·기울기·매수 빈도 | [64](breakout-entry-window-64.md), [65](breakout-entry-trend-filter-65.md), [66](breakout-entry-slope-filter-66.md), [67](breakout-entry-cadence-67.md) |
| 68–70 | 계좌 편중·체결 지연·2018–2019 확장 | [68](account-sensitivity-68.md), [69](delay-path-69.md), [70](historical-window-70.md) |
| 71–72 | 청산 완화·한 요소씩 변경·조기 추적 | [71](relaxed-exits-71.md), [72](buffer-improvement-72.md) |

## 코드와 결과의 구분

- `research/backtest.py`: 공통 체결·위험·청산 정책과 연구용 상태 관찰. 주문 API 접근 없음.
- `research/experiments/`: 실험 조율·동결 대조 재현·손익 대사. 하나의 실행기가 여러 후속 실험을 지원하기도 한다.
- `research/learning/`: 실제 딥러닝 학습. 실험72 후보는 이 계층을 사용하지 않는 규칙 전략이다.
- `tests/research/`: 실험별 회귀·경계·손상 실패 테스트. 소프트웨어 통과와 수익성 통과는 다르다.
- `docs/`: 가설·고정 조건·결과·기각 이유를 Git으로 관리한다. 실패 결과도 삭제하지 않는다.
- `outputs/`, `data/`: 로컬 대용량 실험·시장 자료로 Git 제외. 소스 커밋만으로 데이터까지 복제되지 않는다.
  재실행하려면 각 문서에 명시된 입력·선행 산출물과 해시가 필요하다.

## 이번 커밋 분리 기준

공유 백테스트에 여러 실험의 옵션이 누적돼 있어 파일을 임의로 되돌려 실험별 역사를
재작성하지 않는다. 공통 기반을 먼저 커밋하고, 독립 실행기·테스트·문서를 다음 의존 순서로 묶는다.

1. 공통 연구 엔진과43–51 확인·추적 실험.
2. 52–60 청산 관찰·연장·선제 청산·진입 맥락.
3. 61–63 반사실·전략 계열·비용 비교.
4. 64–67 진입 창·필터·빈도.
5. 68–70 계좌 민감도·지연·과거 확장.
6. 71 청산 완화 비교.
7. 72 조기 추적 개선 비교.
8. 최신 연구 목차와 단일 후보 승격 준비.

기존01–42 커밋은 보존한다. 버전·운영 설정·배포 파일 변경과 push는 이번 범위에 포함하지 않는다.
