# Evergreen

Upbit 기반 자동매매(Spring Boot) 프로젝트입니다.  
일봉 전략 신호를 생성하고(`candle_signal`), `PAPER`/`LIVE` 모드로 주문을 실행하며, Loki/Grafana로 지표를 시각화합니다.

## 주요 기능
- 일봉 기반 V5 전략 신호 계산 (`regime`, `ATR`, `trail stop`, `volatility percentile`)
- 자동 주문 실행 (`PAPER` / `LIVE`)
- 주문/체결/포지션 저장 (JPA)
- Grafana 대시보드용 구조화 로그 출력
- 수익/리스크 지표 로그 제공
  - `realized_pnl_krw`, `realized_return_pct`, `max_drawdown_pct`
  - `trade_win_rate_pct`, `trade_rr_ratio`, `trade_expectancy_pct`
  - `signal_quality_1d_avg_pct`, `signal_quality_3d_avg_pct`, `signal_quality_7d_avg_pct`

## 실행 모드
- `PAPER`
  - 거래소 실제 주문 없이 가체결
  - 매수 금액은 `TRADING_SCHEDULER_SIGNAL_ORDER_NOTIONAL` 사용
- `LIVE`
  - 실제 거래소 주문
  - 현재 구현 기준:
    - 매수: 요청 금액이 없으면 계좌 KRW 가용금액 기준(사실상 전액 매수)
    - 매도: 현재 포지션 수량 기준(사실상 전량 매도)

## 로컬 실행
### 1) 환경변수 준비
`.env.example`를 참고해 환경변수를 설정하세요.

### 2) 테스트
```bash
./gradlew test
```

### 3) 앱 실행
```bash
./gradlew bootRun --no-daemon
```

Windows:
```bat
gradlew.bat bootRun --no-daemon
```

### 4) 로컬 OTEL + Grafana(LGTM) 통합 테스트
Linux/macOS:
```bash
./ops/local/otel-lgtm/run-evergreen-with-otel.sh
```

Windows PowerShell:
```powershell
.\ops\local\otel-lgtm\run-evergreen-with-otel.ps1
```

- Grafana: `http://localhost:3000` (`admin/admin`)
- 중지:
```bash
./ops/local/otel-lgtm/stop-lgtm.sh
```
```powershell
.\ops\local\otel-lgtm\stop-lgtm.ps1
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
  - `TRADING_SCHEDULER_MODE` (`PAPER` or `LIVE`)
  - `TRADING_SCHEDULER_MARKETS` (예: `KRW-BTC,KRW-ETH`)
  - `TRADING_SCHEDULER_CANDLE_COUNT` (권장: `400`)
  - `TRADING_SCHEDULER_SIGNAL_ORDER_NOTIONAL` (`PAPER`에서 매수 금액)

전체 목록은 `src/main/resources/application.yaml` 참고.

## 대시보드
- 대시보드 JSON: `docs/grafana_trading_dashboard.json`
- 로그 가이드: `docs/grafana_trading_logging.md`
- 로컬 LGTM 테스트: `docs/local_otel_lgtm_test.md`
- 샘플 레이아웃: `docs/grafana_dashboard_sample_full_top6cards.svg`

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
java -jar build/libs/evergreen-0.0.0.jar
```

### 4) 배포 시 권장 설정
- `TRADING_SCHEDULER_MODE=PAPER`로 먼저 배포 후 모니터링
- 운영 전환 시 `LIVE`로 변경
- `JPA_DDL_AUTO=validate` 권장(운영)
- 로그 수집 파이프라인(Loki)과 알람 연동

## 문서
- 전략/파라미터 의사결정: `docs/backtest_strategy_hyperparameter_decision_paper.md`
- 기술 설계: `docs/backtest_v5_technical_design.md`
- PRD: `docs/backtest_v5_prd.md`
