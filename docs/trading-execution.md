# 주문 실행기

현재는 **기본 비활성화된 코드와 격리 테스트**를 제공한다. 이 통합 변경으로 실계좌 조회·주문·운영 DB 초기화는 수행하지 않았다.
연구 후보의 수익성은 아직 입증되지 않았으며 자동 학습·승격은 구현하지 않았다.

## 실행 경계

웹 서비스 `uv run evergreen`는 FastAPI lifespan 안에서 매매 루프를 함께 관리한다.
`local` 프로파일 또는 `evergreen.execution.live-enabled=false`이면 루프를 시작하지 않는다.
비로컬 프로파일에서 true이면 기존 설정으로 DB 잠금·계좌 바인딩을 검증한 뒤 실행한다.
Docker 기본 명령은 그대로이며 별도 워커 서비스나 실행 명령이 필요하지 않다.
연구 CLI와 아래 기본 명령은 주문 실행기를 시작하지 않는다.
아래 기본 명령은 Config Server·DB·업비트에도 접속하지 않는다.

```bash
uv run python -m evergreen.trading
```

수동 초기화·진단 CLI는 기존 Config Server 부트스트랩을 사용한다.
`--approve-initialization`은 빈 MariaDB에 최초 설치 승인을 기록하며 업비트 API는 호출하지 않는다.
기존 `--initialize-state`도 같은 승인 명령의 별칭이다. 더 이상 미검증 상태 행을 직접 만들지 않는다.
`--execute`는 `evergreen.execution.live-enabled=true`도 함께 있어야 동작하며,
`--once`는 한 사이클 후 종료한다. 이 저장소가 제공하는 Config 설정은 **false**다.
수동 `--execute`와 웹 실행을 병행하지 않는다. 같은 DB 세션 잠금으로 중복 실행을 거부한다.

최초 설치 승인은 실거래 비활성화 상태에서 한 번만 수행한다.
활성화 상태에서 승인 명령을 실행하면 `initialization_requires_live_disabled`로 거부한다.
승인만으로 거래가 활성화되거나 이미 중단된 워커가 재시작되지는 않는다.
다음 워커 기동의 첫 사이클에서 계좌·미체결 주문·호가를 검증한 뒤 상태를 자동 생성한다.
이 사이클은 주문하지 않으며, 이후 사이클부터 기존 실거래 설정과 전략에 따라 판단한다.
기존 상태가 있으면 잔고·미확정 주문을 대사하며 이어서 실행하고 상태를 덮어쓰지 않는다.
설정·DB 잠금·계좌 상태 검증 실패 또는 루프 오류 시 주문 루프는 중단하고 API는 진단용으로 유지한다.
프로세스 안에서 자동 재시작하지 않는다. 원인을 해결한 뒤 애플리케이션을 재시작한다.
Config 변경도 실행 중 자동 반영하지 않으며 비활성화하려면 앱을 중지하거나 false 설정 후 재시작한다.

`/actuator/info`의 `trading`은 `disabled`, `starting`, `running`, `halted`, `failed`,
`stopping`, `stopped`와 마지막 완료 사이클 시각·결과, 고정 원인 코드·오류 타입만 표시한다.
`running`은 마지막 사이클 완료를 의미하며 체결 성공이나 현재 네트워크 정상 여부를 보장하지 않는다.
`/actuator/health`는 서버 생존 확인으로 유지한다. 거래 오류를 health 실패로 연결해
Docker가 주문 루프를 반복 재시작하는 것을 피한다. 거래 장애 감시는 `trading.status`와
`last_cycle_at`을 함께 확인해야 한다.

앱 종료 시 루프를 취소하고 SDK·DB 연결 및 잠금 정리를 기다린 뒤 Eureka 등록을 해제한다.
진행 중인 HTTP 요청은 중단될 수 있으므로 체결 여부가 불명확한 주문은 기존 DB pending을 유지한다.
종료가 거래소 주문 취소를 의미하지 않는다. 다음 실행에서 조회·대사하며 재전송하지 않는다.

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

[공식 Python SDK](https://github.com/upbit-official/upbit-sdk-python)의
`upbit-sdk` (`AsyncUpbit`)를 사용한다. 서명·HTTP 전송은 SDK가 담당하며,
한국 업비트 호스트와 BTC/KRW 마켓을 고정한다. 공개 연구용 대량 캔들 수집기는 기존 HTTPX 구현을 유지한다.
주문은 SDK의 `orders.create`로 원화 금액/BTC 수량의 Decimal 문자열과 고유 identifier를 보존한다.
응답은 `with_raw_response`로 원본 JSON을 받아 기존 Pydantic 계약으로 검증한다.
타임아웃은 10초, 자동 재시도는 0회이며 리다이렉트·환경변수 프록시를 사용하지 않는다.
429 요청 제한 오류도 즉시 실행을 중단한다. 재시작 시 DB의 미확정 주문을 조회하며 재전송하지 않는다.
SDK·HTTP 로그에는 요청 옵션과 주문 식별자가 포함될 수 있으므로
거래 경로에서는 `upbit`·`httpx`·`httpcore` 로거를 WARNING 이상으로 제한한다.
통합 프로세스의 API 계측은 유지하되 매매 루프의 HTTP·SQL 자동 계측은 task-local context로 억제한다.

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

테이블 생성·변경은 [Alembic 마이그레이션](database-migrations.md)이 관리한다.
비로컬 웹 배포와 거래 워커 시작 전에 미적용 revision을 반영한다.
최초 설치 승인은 실행 상태와 이벤트가 모두 비어 있을 때만 허용한다.
`evergreen_execution_event`에 계정 키 해시와 `initialization-approved` 이벤트를 기록한다.
운영 이력이 없음을 운영자가 확인한 후 앱 환경에서 한 번만 실행한다.

```bash
uv run python -m evergreen.trading --approve-initialization
```

Config Server의 키와 DB 연결을 그대로 사용한다. 상시 자동 초기화 설정은 추가하지 않는다.
승인 후 첫 사이클에서 실제 잔고, 잠금 잔고·미체결 주문, 시장 상태, 호가 신선도,
기존 BTC 포지션 제한을 확인한다. 검증 실패 시 승인만 남기고 워커는 중단한다.
성공하면 확인한 KRW/BTC 잔고와 최초 평가금액을 상태에 기록하고 `initialized` 이벤트를
**동일 트랜잭션**으로 저장한다. 승인 이벤트는 감사 이력으로 남지만 다시 사용할 수 없다.

| DB 상태 | 동작 |
| --- | --- |
| 상태·이벤트 모두 없음 | `initialization_approval_required`로 중단; 최초 설치인지 DB 유실인지 운영자가 확인 |
| 해당 키의 승인 이벤트 하나만 있음 | 계좌 검증 후 자동 상태 생성; 첫 사이클 주문 없음 |
| 정상 실행 상태 있음 | 기존 상태 재사용; 미확정 주문 우선 대사, 잔고 불일치 시 중단 |
| 실행 이력은 있는데 상태 없음 | `execution_state_recovery_required`로 중단; 재승인도 거부 |
| 승인에 기록된 키가 다름 | `initialization_account_mismatch`로 중단 |

DB 전체가 유실되면 승인도 없어지므로 자동으로 처음부터 거래하지 않는다.
반대로 DB 백업이 승인만 있던 시점으로 복원되면 이를 진짜 첫 설치와 자동 구분할 수 없다.
**모든 DB 복원 후에는 워커를 중지한 채 실제 주문 이력을 수동 대사해야 한다.**
현재 버전의 미검증 잔고(null) 상태 행은 유지하며 기존 첫 실행 검증을 적용한다.
마이그레이션 적용만으로 계좌를 초기화하거나 거래를 활성화하지 않는다.
이전 버전에서 이미 테이블을 만든 경우 문서의 baseline 절차를 먼저 수행한다.
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

통합 테스트는 해당 테스트 DB의 두 실행 테이블과 Alembic 이력을 삭제·재생성한다. 운영 DB 주소를 넣지 않는다.
운영 계정·운영 네트워크·거래소 최종 체결까지의 검증은 이 테스트에 포함되지 않는다.
