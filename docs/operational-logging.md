# 운영 로그

API 서버·거래 워커·DB CLI는 Python 표준 로깅을 사용한다.
메시지는 `event=... status=... reason=... duration_ms=...` 형식이다.
기본 레벨은 INFO이며 Config Server의 `logging.level.evergreen` 또는
`LOGGING_LEVEL_EVERGREEN`으로 DEBUG/INFO/WARNING/ERROR/CRITICAL을 선택할 수 있다.
Config Server 조회 이전 로그는 시작 시 환경변수 또는 기본 레벨을 사용한다.
기존 로깅 핸들러는 제거하지 않는다.

| 영역 | 주요 이벤트 |
| --- | --- |
| 기동 | `bootstrap_started`, `bootstrap_completed`, `listeners_bound`, `listeners_closed` |
| 설정 | `config_load`, `config_fetch`, `config_loaded`, `config_unavailable` |
| 마이그레이션 | `database_migration`, `database_schema_apply`, `database_revision` |
| DB 잠금 | `database_lock_wait`, `database_lock_acquired`, `database_lock_unavailable`, `execution_lock_lost` |
| 플랫폼 연동 | `telemetry_initialize`, `eureka_client_initialize`, `eureka_client_stop` |
| 매매 루프 | `worker_skipped`, `worker_started`, `worker_heartbeat`, `worker_failed`, `worker_stopping`, `worker_stopped` |
| 매매 판단 | `trading_signal`, `trading_rejected`, `trading_halted`, `trading_cycle_result` |
| 주문 | `order_intent_committed`, `order_submit`, `order_accepted`, `order_reconcile_lookup`, `order_reconciled` |
| DB 기록 | `execution_state_committed`, `execution_state_initialized` |

`status=completed`는 해당 함수/단계의 정상 반환을 의미한다.
Eureka 클라이언트 초기화가 실제 레지스트리 등록·게이트웨이 통신 성공을 보장하지는 않는다.
주문 제출 완료도 체결 완료는 아니다. 체결·잔고 확인 후 `order_reconciled`가 기록된다.
`execution_state_committed`는 DB 트랜잭션 커밋 이후에만 기록한다.

반복 조회·대기(`pending`, `already-evaluated`, `outside-signal-window`, `halted`)와
계좌 평가 저장은 DEBUG로 제한한다. 워커는 정상 사이클을 완료하며 최소 60초 간격으로
`worker_heartbeat`를 남긴다. 이는 독립적인 헬스체크가 아니며, 외부 호출에서 멈추면 출력되지 않는다.
API와 함께 실행되는 루프 상태·마지막 완료 시각은 `/actuator/info`의 `trading`에서도 확인한다.
`worker_started`는 진입 로그이며 DB 검증·주문 성공 증거는 아니다. `worker_skipped`는
`local_profile` 또는 `live_disabled`로 루프를 시작하지 않았음을 의미한다.
루프 장애는 API health 실패로 전환하지 않으므로 health 200만으로 매매 정상 여부를 판단하지 않는다.

## 장애 확인

- `trading_rejected`: `stale_quote`, `invalid_candles`, `slippage_exceeded`,
  `unexpected_balance`, `settlement_mismatch` 등 고정 원인 코드로 구분한다.
- `status=failed`: 실패 단계와 `error_type`을 확인한다. 예외 원문·스택은 포함하지 않는다.
- `order_submit` 실패: DB의 미확정 주문을 확인한다. 타임아웃이나 404를 이유로 재주문하지 않는다.
- `order_sequence`: DB 실행 상태의 내부 순번과 대조한다. 거래소 주문 식별자는 로그에 출력하지 않는다.

추가한 운영 로그에는 API 키, 인증 헤더, 설정값 전체, DB URL·비밀번호,
계좌 잔고, 주문 금액·수량, 거래소 응답 원문을 넣지 않는다.
`upbit`, `httpx`, `httpcore`의 INFO/DEBUG 로그도 제한한다.
상세 거래 감사·복구 정보는 기존 MariaDB 실행 이력을 사용한다.

## Grafana와 수집 범위

API 서버는 기존 OpenTelemetry 설정을 유지한다. 초기 설정 로딩·마이그레이션 로그는
OTel 초기화 전이므로 컨테이너 표준 오류 로그에서 확인해야 한다.
API lifespan의 매매 루프는 금융 HTTP·SQL 자동 계측을 context 단위로 억제한다.
고정 이벤트 로그는 API와 같은 로깅 핸들러를 사용하므로 기존 OTel 로그 수집 설정을 따른다.
수동 거래 CLI는 OTel 초기화를 하지 않으므로 stdout/stderr 수집 경로를 사용한다.
이번 변경은 수집기 배포나 운영 환경의 실제 전달 여부를 검증하지 않는다.
