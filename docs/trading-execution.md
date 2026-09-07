# 주문 실행기

현재는 **비활성화된 코드와 격리 테스트**를 제공한다. 실계좌 조회·주문·운영 DB 초기화·배포는 수행하지 않았다.
연구 후보의 수익성은 아직 입증되지 않았으며 자동 학습·승격은 구현하지 않았다.

## 실행 경계

웹 서비스 `uv run evergreen`와 연구 CLI는 주문 실행기를 시작하지 않는다.
아래 기본 명령도 Config Server·DB·업비트에 접속하지 않는다.

```bash
uv run python -m evergreen.trading
```

별도 워커는 기존 Config Server 부트스트랩을 사용한다.
`--initialize-state`는 MariaDB에 최초 상태를 생성하지만 업비트 API는 호출하지 않는다.
`--execute`는 `evergreen.execution.live-enabled=true`도 함께 있어야 동작하며,
`--once`는 한 사이클 후 종료한다. 이 저장소가 제공하는 Config 설정은 **false**다.
컨테이너 기본 명령과 GitOps에는 거래 워커를 추가하지 않았다.

## Config Server

기존 Platform 저장소의 `config/src/main/resources/config/evergreen/evergreen.yaml`을 사용한다.
새로운 업비트 키나 DB 비밀번호를 별도로 만들지 않는다.

| 설정 | 용도 |
| --- | --- |
| `evergreen.trading.access-key / secret-key` | 기존 업비트 키, 미복호화 cipher 거부 |
| `spring.datasource.url / username / password` | 기존 MariaDB 연결 |
| `evergreen.execution.live-enabled` | false, 명시적 주문 허용 스위치 |
| `evergreen.execution.poll-seconds` | 30초, 주문 복구·위험 확인 |
| `evergreen.execution.max-quote-age-seconds` | 5초, 오래된 호가 거부 |
| `evergreen.execution.max-signal-age-seconds` | 120초, 시간봉 마감 후 신호 유효 구간 |
| `evergreen.execution.max-slippage` | 0.003, 보이는 호가의 예상 평균 체결 편차 |
| `evergreen.execution.max-drawdown` | 0.10, 관측한 계좌 고점 대비 중단 기준 |

단일 호스트 `jdbc:mariadb://host:3306/database` 또는 `jdbc:mysql://...`를
SQLAlchemy `mysql+asyncmy` URL로 변환한다. 비밀번호는 URL 문자열로 출력하지 않는다.
지원하지 않는 JDBC 옵션이나 TLS 활성화 URL은 무시하지 않고 시작을 거부한다.
TLS가 필요한 운영 환경은 CA·호스트 검증 정책을 확정해 드라이버 설정을 추가해야 한다.
현재 운영 DB의 복호화된 주소·접속 권한은 검증하지 않았다.

## SDK와 주문 안전장치

[업비트 통합 가이드](https://global-docs.upbit.com/docs/ccxt-library-integration-guide)에 소개된
CCXT 비동기 Upbit SDK를 사용한다. 서명·HTTP 전송·요청 제한은 SDK가 담당하며,
한국 업비트 호스트와 BTC/KRW 마켓을 고정한다. 공개 연구용 대량 캔들 수집기는 기존 HTTPX 구현을 유지한다.
주문은 SDK의 native 메서드로 원화 금액/BTC 수량의 Decimal 문자열과 고유 identifier를 보존한다.

1. 계정 잔고·주문 가능 조건·미체결 주문을 확인한다.
2. 연속된 확정 169개 시간봉으로 168시간 고점 돌파 / 48시간 저점 청산을 판단한다.
3. 실제 수수료·최소/최대 주문액·호가 깊이를 확인한다.
4. 고유 identifier와 주문 의도를 DB에 먼저 커밋한다.
5. 같은 DB 잠금 소유권을 재확인하고 주문을 **한 번만** 제출한다.
6. 종료 확인 전에는 같은 identifier로 조회만 한다. 타임아웃·404도 재주문의 근거가 아니다.

최초 원화 계정만 허용하며, 최소 주문액 미만 BTC 잔량은 허용한다.
기존 BTC 포지션을 자동 인수하지 않는다. 원화는 실제 수수료와 1원 여유를 제외하고,
매도는 사용 가능한 BTC를 8자리까지 내림한다. 외부 입출금·수동 거래는 중단 사유다.
전용 계정/포켓을 사용하고 다른 봇·수동 주문과 병행하지 않아야 한다.
API 키는 필요한 조회·주문 권한만 부여하고 출금 권한은 부여하지 않는다.

부분 체결·취소도 주문 전 잔고와 누적 체결금액·수량·수수료로 기대 잔고를 계산해 실제 잔고와 대사한다.
불일치하면 pending을 해제하지 않고 중단하므로, 주문 대기 중 외부 입금도 운용 원금에 자동 편입하지 않는다.
낙폭 중단 상태는 저장되며 재시작해도 신규 매수를 재개하지 않는다.
10%는 **관측 중단 기준이지 최대 손실 보장치가 아니다**. 급락·호가 변화·네트워크 장애·DB 장애로
청산이 지연되거나 실패할 수 있다. 시장가 주문의 슬리피지도 보장할 수 없다.

## MariaDB 상태와 복구

처음에만 별도 명령으로 `evergreen_execution_state`, `evergreen_execution_event`
InnoDB 테이블과 단일 상태 행을 생성한다. 기존 다른 테이블을 수정하지 않으며
이미 초기화된 상태를 덮어쓰거나 자동 초기화하지 않는다. 필요한 CREATE/SELECT/INSERT/UPDATE 권한을 확인한다.
상태와 감사 이벤트는 같은 트랜잭션으로 저장된다. 감사 이벤트 보관·아카이브 정책은 운영자가 별도로 정해야 한다.

동일 MariaDB 서버의 세션 잠금 `evergreen:execution:KRW-BTC`로 동시 워커를 차단한다.
전용 연결을 유지하고 모든 읽기·쓰기 및 제출 직전에 연결 ID와 소유권을 검사한다.
연결을 잃으면 중단한다. 독립 DB 서버나 멀티 라이터 클러스터 사이의 분산 잠금은 아니다.
모든 워커를 **동일한 단일 writer**로 연결해야 한다. DB와 거래소를 아우르는 원자적 exactly-once 보장은 없다.

장애 시 워커를 먼저 멈추고 DB pending identifier와 업비트 주문 내역을 대조한다.
응답 불명 주문을 해결하려고 상태 행 삭제·재초기화·새 identifier 재전송을 하지 않는다.
DB 복원으로 주문 이력이 과거로 돌아간 경우에도 거래소와 수동 대사하기 전 재개하지 않는다.
원인 미확인 상태에서는 중단을 유지한다. 자동 pending 삭제·수동 거래 API는 제공하지 않는다.

## 검증

일반 테스트는 외부 API 대신 SDK 전송부 모의 응답을 사용한다.
DB 통합 테스트만 명시적으로 제공된 격리 MariaDB를 사용한다.

```bash
uv run pytest
# disposable local DB only: 127.0.0.1, database=evergreen_test
EVERGREEN_TEST_MARIADB_URL='mysql+asyncmy://USER:PASSWORD@127.0.0.1:PORT/evergreen_test' \
  uv run pytest tests/test_trading_mariadb.py --no-cov
```

통합 테스트는 해당 테스트 DB의 두 실행 테이블을 삭제·재생성한다. 운영 DB 주소를 넣지 않는다.
운영 계정·운영 네트워크·거래소 최종 체결까지의 검증은 이 테스트에 포함되지 않는다.
