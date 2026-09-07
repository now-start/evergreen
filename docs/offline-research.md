# 오프라인 리서치 실행

공개 업비트 `KRW-BTC` 1시간봉을 수집하고 현금 유지·매수 후 보유·SMA 20/60을
같은 조건에서 비교한다. API 키, 계좌 조회, 주문 권한을 사용하지 않으며 서버의
Config Server·Eureka·OTel 초기화도 실행하지 않는다. `uv sync` 후 프로젝트 루트에서 실행한다.

## 1. 데이터 수집

```bash
uv run python -m evergreen.research fetch \
  --start 2024-01-02T12:00:00Z \
  --end 2024-02-01T00:00:00Z \
  --output data/upbit-btc-2024-january
```

- 시작 포함·종료 제외, 시간대가 있는 정각을 입력한다. 미래 구간은 허용하지 않는다.
- 평가 시작 전 60시간 이상을 워밍업으로 포함한다. 현재 수집·평가는 완료된 봉만 사용한다.
- 업비트 공개 GET API만 호출한다. 요청 간 0.2초 대기, `Remaining-Req`가 `sec=0`이면
  추가 대기하며, 429·418과 다른 HTTP 오류는 기록 후 종료한다. 무한 재시도하지 않는다.
- 기존 출력 디렉터리는 거부한다. 재실행할 때는 새로운 이름을 사용한다.

생성 파일:

```text
data/upbit-btc-2024-january/
├── raw/000001.json ...  # 성공·오류를 포함한 원본 응답 바이트
├── quality.json        # 요청 경계/수집 시각/응답 해시/결측·충돌·상태
└── candles.jsonl       # 품질 통과 시에만 생성하는 정제 캔들
```

품질 실패 시 종료 코드는 1이다. 미완성 봉·범위 밖 봉은 따로 집계하고, 요청 기간의
공백·가격 충돌은 평가를 차단한다. 수집 시각이 다른 동일 캔들은 충돌이 아니다.
누락이 거래소 무체결인지 수집 문제인지 확인하지 않고 채워 넣지 않는다.

## 2. 기준선과 비용 스트레스 비교

다음 금액·비용·최소 주문·수량 정밀도는 **실행 예시용 가정**이다. 실제 계좌 원금,
현재 계정 수수료, 역사적 수수료·호가·유동성을 검증한 값이 아니다. 비용은 소수 비율이다
(`0.0005 = 0.05%`). 생략할 수 없고, 고의로 비용 없는 실험을 할 때만 `0`을 지정한다.

```bash
uv run python -m evergreen.research backtest \
  --dataset data/upbit-btc-2024-january \
  --start 2024-01-05T00:00:00Z \
  --capital 1000000 \
  --buy-fee 0.0005 --sell-fee 0.0005 \
  --buy-slippage 0.001 --sell-slippage 0.001 \
  --min-notional 5000 --quantity-step 0.00000001 \
  --output outputs/baseline-2024-january
```

평가 종료는 데이터셋 종료이며 모든 비교에 공통이다. 데이터 해시와 품질을 다시
검증하고, 60시간 워밍업이 없으면 현금·매수 후 보유 비교를 포함한 전체 실행을 거부한다.

| 시나리오 | 수수료 | 슬리피지 | 추가 체결 지연 |
|---|---|---|---|
| `base` | 입력값 | 입력값 | 없음 |
| `slippage-x2` | 입력값 | 2배 | 없음 |
| `costs-x2` | 2배 | 2배 | 없음 |
| `delay-1h` | 입력값 | 입력값 | 진입·전략/위험 청산 모두 한 봉 |

3개 전략 × 4개 시나리오의 JSON 결과와 `summary.md`, `manifest.json`을 저장한다.
JSON에는 체결 내역·거부 사유·시가/종가 평가금 곡선·비용·잔량·중단 여부가 포함된다.
manifest는 데이터 해시, 연구 코드 파일별 해시, Git 커밋과 dirty 여부를 기록한다.
미커밋 코드가 있다면 커밋만으로 재현할 수 없으므로 파일 해시도 확인해야 한다.
Git이 없는 설치 환경에서는 커밋 대신 소스 해시를 사용한다.

출력은 덮어쓰지 않는다. 일부 결과 생성 후 실패하면 `failure.json`을 남기고
성공 manifest는 만들지 않는다. 부분 결과를 전체 비교 성공으로 사용하면 안 된다.

## 3. 모의 체결 범위

- 확정 봉 신호를 다음 봉 시가에 비용을 더해 체결하는 근사이며 실제 시장가 체결이 아니다.
- 수량 step 단위로 내림하고 매수 비용을 원화에서, 매도 비용을 매도대금에서 차감한다.
- 주문이 가능하면 전량 즉시 체결하는 오프라인 엔진이다. 실제 주문 미체결·부분 체결·
  응답 불명·호가 깊이·실시간 잔고 대사는 연구 엔진이 재현하지 않는다.
  별도 [주문 실행기](trading-execution.md)는 이 항목들을 처리하지만 연구 결과를 실체결 검증으로 대체할 수 없다.
- 10% 고점 대비 낙폭 조건은 SMA·돌파 거래 전략을 중단한다. 매수 후 보유는 위험 제한 없는 비교 대상이다.
- 낙폭은 시가·종가·정산 시점만 관측한다. 봉 내부의 저점과 실시간 손실 상한을 재현하지 않는다.
- 중단은 고정되며 이후 가격이 회복해도 자동 재진입하지 않는다. 지연 시나리오에서도
  한번 정한 위험 청산 시각을 매 봉 뒤로 미루지 않는다.
- 마지막 봉 신호는 실행하지 않는다. 종료 정산만 최종 종가에서 비용을 적용한다.
- 매도할 수 없는 BTC 잔량은 원장에 보존하고 최종 종가로 평가한다. 잔량 평가액을
  바로 인출 가능한 원화와 혼동하면 안 된다.

## 4. 실제 데이터 실행 확인 — 2026-09-07

먼저 2024년 전체와 워밍업 60시간을 요청했다. 8,827개 봉이 확보됐지만 요청 기간에
17개 시간대가 없어 품질 검사에서 실패했다. 연간 성과는 계산하지 않았다.

2024-03-31 17:00~22:00 UTC를 별도로 재조회해도 18:00·19:00·20:00 봉은 응답에
없었다. 거래소 점검 등 원인은 확인되지 않았으며, 임의 합성하지 않았다.

이후 **연결·계산 검증용** 별도 구간을 사용했다. 2024-01-02 12:00~02-01 00:00 UTC의
708개 봉은 품질 검사를 통과했고, 01-05 00:00부터의 648개 평가 봉으로 12개 비교 결과를
생성했다. 이는 결측 없는 구간의 실행 확인이지 기간 전체 전략 검증·독립 테스트가 아니다.
이 구간은 이미 관찰한 연구 데이터로 취급하며 향후 미관측 테스트로 재사용하지 않는다.

원본과 실행 결과는 Git에서 제외되는 `data/`, `outputs/`에 남아 있다.

- 연간 품질 실패: `data/upbit-btc-2024-v0/quality.json`
- 누락 재확인: `data/upbit-gap-recheck-20240331-v0/quality.json`
- 정상 구간: `data/upbit-btc-2024-january-smoke-v0/quality.json`
- 실행 결과: `outputs/baseline-2024-january-smoke-v0/summary.md`

## 5. 사전 지정 후보 개선 실험

CLI 안내와 사람이 읽는 `summary.md`는 한글이다. JSON 필드와 전략 ID는 프로그램
호환성을 위해 영문을 유지한다. 이전 결과는 덮어쓰거나 선택적으로 삭제하지 않는다.

[실험 01 계획과 결과](strategy-experiment-01.md)의 고정 구간을 수집한다.

```bash
uv run python -m evergreen.research fetch \
  --start 2024-01-01T00:00:00Z --end 2024-03-01T00:00:00Z \
  --output data/experiment-01-development
uv run python -m evergreen.research fetch \
  --start 2024-07-01T00:00:00Z --end 2024-09-01T00:00:00Z \
  --output data/experiment-01-validation-a
uv run python -m evergreen.research fetch \
  --start 2024-11-01T00:00:00Z --end 2024-12-15T00:00:00Z \
  --output data/experiment-01-validation-b

uv run python -m evergreen.research study \
  --development data/experiment-01-development \
  --validation-a data/experiment-01-validation-a \
  --validation-b data/experiment-01-validation-b \
  --output outputs/strategy-experiment-01
```

`study`의 기본값은 실험 01이다. 기간·비용·원금·후보·선정/검증 조건을 고정하며
다른 기간의 데이터는 거부한다. 새 후보는 SMA 48/168과 168시간 고점 돌파다.
개발 20개 + 검증 A 16개 + 검증 B 16개, 총 52개 전략·비용 비교를 저장한다.
`selection.json`을 생성한 다음 검증 데이터를 읽어 미선정 후보의 검증 성과는 계산하지 않는다.

- `protocol.json`: 기간·가정·후보·기준·코드 해시.
- `selection.json`: 개발 데이터 해시·최악 수익률·최대 낙폭·선택 시각.
- `development/`, `validation-a/`, `validation-b/`: 상세 원장과 한글 비교 결과.
- `summary.md`, `verdict.json`: 한글 종합 결과와 연구 기준별 통과 여부.
- 실행 오류는 `failure.json`을 남기고 종료 코드 1, **실험 완료 후 기준 미달은 종료 코드 0**이다.
  실행 성공을 수익성 통과로 읽지 말고 `research_passed`를 확인한다.

이번 실행에서는 1,440 / 1,488 / 1,056개 봉이 각각 품질 검사를 통과했다.
검증 구간에서 일부 수익이 나도 개발 손실·위험 중단·낙폭·청산 표본 부족으로 연구 기준에 미달했다.
같은 검증 구간으로 다시 수정하면 더 이상 미관측 검증이 아니므로 별도 실험 계획이 필요하다.

### 평균회귀 실험 02r

[실험 02의 실패 기록과 수정 계획](strategy-experiment-02.md)을 따르는 별도 실행이다.
최초 개발 구간은 봉 누락으로 실패했고, 성과 계산 전에 수정한 구간만 `02r`로 지원한다.

```bash
uv run python -m evergreen.research fetch \
  --start 2025-01-03T00:00:00Z --end 2025-03-01T00:00:00Z \
  --output data/experiment-02r-development
uv run python -m evergreen.research fetch \
  --start 2025-04-01T00:00:00Z --end 2025-06-01T00:00:00Z \
  --output data/experiment-02r-validation-a
uv run python -m evergreen.research fetch \
  --start 2025-07-01T00:00:00Z --end 2025-09-01T00:00:00Z \
  --output data/experiment-02r-validation-b
uv run python -m evergreen.research study --experiment 02r \
  --development data/experiment-02r-development \
  --validation-a data/experiment-02r-validation-a \
  --validation-b data/experiment-02r-validation-b \
  --output outputs/strategy-experiment-02r
```

가격 밴드 평균회귀·추세 필터 RSI 반등 중 개발 구간에서 하나를 선정한다.
기준선은 현금·단순 보유·SMA 20/60·SMA 48/168, 결과는 총 64개다.
RSI는 Wilder 평활이 아닌 14기간 변화 합을 사용하는 단순 RSI다.
두 SMA 각각보다 좋은 결과인지 검사하며, 비용·낙폭 기준을 바꿔 통과시키지 않는다.
이것도 딥러닝 학습이 아니라 규칙 기반 비교다. 현재 02r 판정은 수익성 미검증이다.

## 6. 실제 딥러닝 학습·규칙 전략 비교

[실험 03 사전 계획과 결과](deep-learning-experiment-03.md)를 따른다.
`torch`는 이미 프로젝트 의존성에 있으며 서버 시작이 아니라 아래 명령에서만 학습한다.
표본은 최근 32시간 × 5개 특성이고, 학습 구간마다 미래 레이블 경계를 제거한다.
정규화는 2024년 학습 자료에만 맞춘다. MLP·CNN 두 개를 각각 20 epoch 학습하고
2025년에서 후보를 선택한 뒤 해당 가중치 그대로 2026년을 평가한다.

```bash
uv run python -m evergreen.research fetch \
  --start 2026-01-05T00:00:00Z --end 2026-03-01T00:00:00Z \
  --output data/experiment-03-test-a
uv run python -m evergreen.research fetch \
  --start 2026-04-05T00:00:00Z --end 2026-06-01T00:00:00Z \
  --output data/experiment-03-test-b
uv run python -m evergreen.research deep-study \
  --training data/experiment-01-development data/experiment-01-validation-a data/experiment-01-validation-b \
  --selection data/experiment-02r-development data/experiment-02r-validation-a data/experiment-02r-validation-b \
  --tests data/experiment-03-test-a data/experiment-03-test-b \
  --output outputs/deep-learning-experiment-03
```

현재 평가 B 수집은 누락 4개로 실패한다. 이 경우 전체 실행도 종료 코드 1이며,
성공 `verdict.json`을 만들지 않는다. 완료된 학습·평가 A 결과는 보존하고
`failure.json` 및 한글 `summary.md`에 검증 미완료를 표시한다.
평가 B를 임의 합성하거나 다른 기간으로 교체하지 않는다.

- `models/<모델>/checkpoint.pt`: 가중치와 optimizer 상태.
- `models/<모델>/model.json`: 정규화 값·학습 손실·표본 수·시드·환경·체크포인트 해시.
- `training-data.json`, `protocol.json`, `selection.json`: 학습 데이터·실험·고정 선택 기록.
- `predictions/`: 모델 해시에 연결된 시점별 확률, BCE와 고정 학습 비율 예측의 BCE.
- `selection-*/`, `test-a/`, `test-b/`: 실제 완료된 비교 원장·한글 보고서.

체크포인트는 로컬에서 생성한 신뢰 가능한 파일만 사용한다. 로딩 시 해시를 검사하고
`torch.load(..., weights_only=True)`로 제한한다. CPU 2스레드·시드 17로 동일 환경
재실행을 검증한다. GPU/다른 PyTorch 버전과 비트 단위 일치를 주장하지 않는다.
학습 재개·파인튜닝·주기 실행·모델 자동 승격 API는 아직 없다.

## 7. 검증 기반 조기 종료와 대조군 재비교

[실험 04 계획과 결과](early-stopping-experiment-04.md)를 따른다. 학습은 2024년 12월 이전
레이블만 사용하고, 12월 검증 BCE를 매 epoch 측정한다. 최대 100 epoch 중 10회 연속
개선이 없으면 종료하며 최저 BCE의 가중치와 optimizer 상태를 복원한다.
같은 축소 학습 자료로 고정 20 epoch 대조군도 새로 학습한다.

```bash
uv run python -m evergreen.research early-study \
  --training data/experiment-01-development data/experiment-01-validation-a data/experiment-01-validation-b \
  --evaluation data/experiment-02r-development data/experiment-02r-validation-a data/experiment-02r-validation-b data/experiment-03-test-a \
  --output outputs/early-stopping-experiment-04
```

출력 경로는 새 경로여야 한다. `models/<fixed20 또는 early>/<모델>/model.json`에
epoch별 학습·검증 손실, 실제 epoch, 최적 epoch, 저장 epoch와 `stopped_early`를 기록한다.
`split.json`에는 경계에서 제외한 표본 수, `training-complete.json`에는 평가 자료를 읽기
전에 고정한 모델 해시를 저장한다. 한글 `summary.md`는 네 구간의 순수익률·낙폭을 비교한다.
이는 이미 관찰한 구간의 재비교다. 성공 `status.json`도 수익성 검증이나 실거래 승격을 뜻하지 않는다.

## 8. 규칙·딥러닝 혼합 비교

[실험 05 계획과 결과](hybrid-experiment-05.md)를 따른다. 실험 04의 가중치를 그대로 사용해
진입은 규칙 AND 모델, 청산은 규칙 OR 모델로 비교한다. 선택 구간의 비용 스트레스·낙폭·
최소 거래 수로 6개 후보 중 하나를 고정한 뒤 후속 평가를 수행한다. 모두 미달이면 현금 유지다.

```bash
uv run python -m evergreen.research fetch \
  --start 2026-07-01T00:00:00Z --end 2026-09-01T00:00:00Z \
  --output data/experiment-05-test-new
uv run python -m evergreen.research hybrid-study \
  --trained-experiment outputs/early-stopping-experiment-04 \
  --selection data/experiment-02r-development data/experiment-02r-validation-a data/experiment-02r-validation-b \
  --tests data/experiment-03-test-a data/experiment-05-test-new \
  --output outputs/hybrid-experiment-05
```

출력 경로는 새 경로여야 한다. 실제 네트워크 재시도 데이터 경로는 `data/experiment-05-test-new-retry`다.
해당 새 구간은 워밍업 시간봉 3개 누락으로 현재 품질 검사를 통과하지 못한다. 결과 디렉터리의
`selection.json`은 평가 이전 선택, `models/`는 복사한 가중치, `model-origin.json`은 원본 해시를 담는다.
`predictions/`의 단독 모델 확률을 혼합 후보가 그대로 사용하며 `manifest.json`의 후보별 예측 해시로 연결한다.
후속 품질 실패 시 종료 코드 1, `failure.json`과 한글 `summary.md`를 남기고 성공 `status.json`은 만들지 않는다.

## 9. 규칙 후보의 비용 차감 수익성 학습

[실험 06](meta-experiment-06.md)은 규칙이 만든 진입 후보별 가상 거래의 순손익을 레이블로
사용한다. 모델은 진입만 승인하고 청산은 규칙·최대 12시간·위험 중단으로 결정한다.
모델 없는 동일 청산 규칙 대조군, 기존 단독 모델·혼합 전략도 비교한다.

```bash
uv run python -m evergreen.research meta-study \
  --training data/experiment-01-development data/experiment-01-validation-a data/experiment-01-validation-b \
  --selection data/experiment-02r-development data/experiment-02r-validation-a data/experiment-02r-validation-b \
  --test data/experiment-03-test-a \
  --trained-experiment outputs/early-stopping-experiment-04 \
  --output outputs/meta-experiment-06
```

새 출력 경로를 지정해야 한다. `audit.json`에는 표본 수·클래스·제외 이유·시간 경계,
`events-*.json`에는 후보 시각·레이블, `models/<후보>/task.json`에는 거래 과제 정의를 저장한다.
학습/검증이 50/20개 미만이거나 한 클래스뿐이면 그 규칙의 모델을 제외한다.
`training-complete.json`을 기록한 뒤 선택 자료를 읽고, `selection.json` 이후 후속 평가를 읽는다.
성공 `status.json`은 연구 재비교 완료이며 새 홀드아웃 검증·수익성 입증·실거래 승격이 아니다.
실제로 두 모델만 학습했고 재현 확인 출력은 `outputs/meta-experiment-06-verified`에 보존했다.

## 10. 선택된 돌파 전략의 추가 검증

[주력 검증 전략](breakout-strategy.md)은 원래 `breakout-v1`이다.
12시간 제한이나 딥러닝 필터를 추가하지 않는다. 전략별 신호와 공통 체결·학습의
책임 경계는 [패키지 구조](research-packages.md)를 따른다.

```bash
uv run python -m evergreen.research breakout-study \
  --dataset data/experiment-07-august \
  --trained-experiment outputs/early-stopping-experiment-04-verified \
  --output outputs/breakout-experiment-07-new
```

데이터는 2026-07-24~09-01 UTC, 평가 시작은 08-01로 고정한다. 기존 7월 결측 실패를
수정한 실험이 아니라 별도로 사전 지정한 8월 평가다. 항상 새 출력 경로를 사용한다.
실험 04의 MLP는 고정 비교군이며 재학습하지 않는다. 연구 기준 통과 여부와 관계없이
`live_enabled`는 `false`이고, 품질·실행 실패 시 `failure.json`을 기록한다.

## 11. 다음 단계와 금지 사항

연속 연간 데이터가 있다는 가정은 실제 데이터에서 성립하지 않았다. 다음은 점검·
무체결 등 공백에 대한 명시적 데이터 정책을 정하고, 여러 시장 구간의 평가를 수행하는
단계다. 단순히 실패 검사를 끄거나 결측 봉을 합성해서 좋은 결과를 만들지 않는다.

주기적 재학습·자동 승격·상시 Paper는 후속 구현이다. 연구 CLI는 실거래 키를 넣어도 주문하지 않는다.
주문 코드는 별도 [실행기](trading-execution.md)로 분리되어 있으며 기본 비활성화다.
