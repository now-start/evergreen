# Evergreen

Upbit 기반 자동매매(Spring Boot) 프로젝트입니다.  
일봉 전략 신호를 생성하고(`candle_signal`), `PAPER`/`LIVE` 모드로 주문을 실행하며, Loki/Grafana로 지표를 시각화합니다.

## 주요 기능
- 일봉 기반 v1~v5 전략 신호 계산 (`action`, `targetPositionRatio`, `diagnostics`)
- 자동 주문 실행 (`PAPER` / `LIVE`)
- 주문/체결/포지션 저장 (JPA)
- Grafana 대시보드용 구조화 로그 출력
- 수익/리스크 지표 로그 제공
  - `realized_pnl_krw`, `realized_return_pct`, `max_drawdown_pct`
  - `trade_win_rate_pct`, `trade_rr_ratio`, `trade_expectancy_pct`

## 실행 모드
- `PAPER`
  - 거래소 실제 주문 없이 가체결
  - 매수 금액은 `EVERGREEN_TRADING_SIGNAL_ORDER_NOTIONAL` 사용
- `LIVE`
  - 실제 거래소 주문
  - 현재 구현 기준:
    - 매수: 요청 금액이 없으면 `EVERGREEN_TRADING_SIGNAL_ORDER_NOTIONAL`, 수수료 차감 후 가용 KRW, Upbit `market.max_total` 중 작은 금액으로 시장가 매수하고 `market.bid.min_total` 미만은 사전 거절
    - 매도: 현재 포지션 수량 기준(사실상 전량 매도)

## 로컬 실행
### 1) 환경변수 준비

환경변수(`DB_URL`, `UPBIT_ACCESS_KEY` 등)를 실행 환경에 직접 설정하세요.

### 2) 테스트
```bash
./gradlew test
```

### 3) 로컬 앱 실행
```bash
./gradlew bootRun --args='--spring.profiles.active=local' --no-daemon
```

`local` 프로필은 Config Server, Eureka, OTEL export를 끄고 H2 메모리 DB와 `PAPER` 모드를 사용합니다.
기본 대상 마켓은 비워 두므로 자동 신호 실행은 외부 API 호출 없이 종료됩니다.

### 4) 운영 설정으로 앱 실행
```bash
./gradlew bootRun --no-daemon
```

Windows:
```bat
gradlew.bat bootRun --no-daemon
```

## 핵심 환경변수
- DB
  - `DB_URL`
  - `DB_USERNAME`
  - `DB_PASSWORD`
- Upbit
  - `UPBIT_ACCESS_KEY`
  - `UPBIT_SECRET_KEY`
  - `UPBIT_FEE_RATE`
- Trading Scheduler
  - `EVERGREEN_TRADING_EXECUTION_MODE` (`PAPER` or `LIVE`)
  - `EVERGREEN_TRADING_MARKETS` (예: `KRW-BTC,KRW-ETH`)
  - `EVERGREEN_TRADING_CANDLE_COUNT` (권장: `400`)
  - `EVERGREEN_TRADING_SIGNAL_ORDER_NOTIONAL` (`PAPER` 매수 금액 및 LIVE 자동 매수 상한)

전체 목록은 `src/main/resources/application.yaml` 참고.

## 대시보드
- 대시보드 JSON: `docs/grafana_trading_dashboard.json`

## 배포 준비
### 1) 사전 점검
- `PAPER` 모드로 충분한 검증
- 운영 DB 연결 정보/권한 확인
- Upbit API 키 권한 최소화
- 시간 동기화(NTP)와 JVM 타임존 확인

### 2) 빌드
```bash
./gradlew clean bootJar
```

### 3) 실행 (Jar)
```bash
java -jar build/libs/*.jar
```

### 4) 배포 시 권장 설정
- `EVERGREEN_TRADING_EXECUTION_MODE=PAPER`로 먼저 배포 후 모니터링
- 운영 전환 시 `LIVE`로 변경
- `JPA_DDL_AUTO=validate` 권장(운영)
- 로그 수집 파이프라인(Loki)과 알람 연동

## 문서
- PRD: `docs/product_requirements.md`
- 아키텍처 구조도: `docs/architecture.md`
- 기술 명세: `docs/technical_specification.md`
- Upbit Open API 정합성 검토: `docs/upbit_open_api_alignment_review.md`
- 전략/파라미터 의사결정: `docs/backtest_strategy_hyperparameter_decision_paper.md`
