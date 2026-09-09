# 패키지 구조

전략은 신호만 반환한다. 실험과 실제 주문 실행은 같은 신호를 사용하되 서로 다른 엔진으로 분리한다.
전략 ID·조건·저장된 모델 형식·연구 CLI 명령은 유지한다.

```text
src/main/evergreen/
├── market.py                 # 공통 캔들 계약·공개 데이터 수집·품질 검사
├── database/                 # 공통 datasource·스키마 revision·배포 전 적용
├── strategies/               # 공통 순수 신호, 계좌·학습·서버 초기화 없음
│   ├── types.py, registry.py  # 전략 ID·파라미터·워밍업·라우팅
│   ├── breakout.py, trend.py  # 돌파·추세
│   ├── band.py, rsi.py        # 평균회귀·반등
│   ├── deep.py, hybrid.py     # 주어진 확률을 신호로 변환
│   └── regime.py             # 연구용 장세 전환·진입 전략의 청산 책임
├── research/
│   ├── backtest.py           # 공통 모의 체결·비용·위험 관리
│   ├── report.py             # 결과·비용 비교·전체 evergreen 소스 해시
│   ├── learning/
│   │   ├── models.py         # 특성·MLP/CNN/선형 대조군·학습·조기 종료·체크포인트
│   │   ├── meta.py           # 규칙 진입 후보의 비용 차감 거래 레이블
│   │   ├── breakout_meta.py  # 보유 제한 없는 원래 돌파 청산 사건 레이블
│   │   ├── regime.py         # 3장세 레이블·MLP·검증 기반 조기 종료
│   │   └── regime_features.py # 단기·장기 입력과 공정 비교용 대조군
│   ├── experiments/          # 실험별 조율; 엔진·모델 구현 복제 없음
│   │   ├── rules.py, deep.py, early_stopping.py
│   │   ├── hybrid.py, meta.py, breakout.py
│   │   ├── breakout_meta.py  # 장기 돌파 수익성 학습·상수 예측 대조군
│   │   ├── regime.py, regime_data.py  # 장기 수집·워크포워드·연속 블록 평가
│   │   ├── regime_followup.py   # 보합 제외·균형 학습·진입 필터 분리 실험
│   │   ├── regime_stability.py  # 시드 안정성·목표 기간·고정 모델 최근 평가
│   │   ├── regime_features.py   # 입력 확장 효과의 동일 표본 비교
│   │   └── breakout_errors.py   # 동결된 예측·체결의 오류와 손익 분해
│   └── __main__.py           # 기존 연구 CLI
├── trading/
│   ├── config.py             # 기존 Config Server 키·datasource 해석
│   ├── upbit.py              # 공식 비동기 Upbit SDK·응답 계약
│   ├── state.py              # MariaDB 상태·감사 이벤트·단일 세션 잠금
│   ├── engine.py             # 잔고 대사·주문 의도·복구·위험 중단
│   └── __main__.py           # 명시적 실행 워커, 기본 비활성화
└── platform/                 # 기존 웹 서비스의 Spring 연동
```

- `strategies`와 `market`은 `research`, PyTorch, 서버 초기화를 import하지 않는다.
- `research.learning`은 모델 학습 공통 계층이다. 전략마다 학습 파일을 복제하지 않는다.
- `research.experiments`는 평가 구간·고정 모델·비교군·평가 순서만 조율한다.
- `trading`은 연구 모듈을 import하지 않는다. 현재 실행 대상은 원래 `breakout-v1`뿐이다.
- 원래 돌파에는 12시간 보유 제한이나 딥러닝 필터가 없다. 해당 조건은 지정된 연구 후보에만 적용한다.

```python
from evergreen.strategies import strategy_target

signal = strategy_target(closed_candles, holding=False, strategy="breakout-v1")
```

반환값 자체는 주문이 아니다. [주문 실행기](trading-execution.md)는 별도 명령과 설정이 필요하다.
패키징 회귀 테스트는 기존 24개 전략 × 기본/1시간 지연 결과를 기존 해시와 비교한다.
추가 장세 전략 2개는 별도 시각·전환·비용·학습 경계 테스트로 검증한다.
연구 결과와 체크포인트는 이동하거나 덮어쓰지 않는다.
