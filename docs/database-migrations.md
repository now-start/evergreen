# PostgreSQL 스키마 관리

Evergreen은 PostgreSQL 18 전용이며 asyncpg와 SQLAlchemy를 사용한다.

## 연결 설정

Config Server의 기존 키를 사용한다.

```yaml
spring:
  datasource:
    url: jdbc:postgresql://postgres_haproxy:5432/evergreen?sslmode=require&connectTimeout=3
    username: <evergreen DB 소유 계정>
    password: <암호화된 Config 값>
```

비밀번호를 URL에 넣지 않는다. URL 옵션은 `sslmode`와 `connectTimeout`만 허용한다.
TLS 기본값은 `require`, 연결 제한은 3초다. `verify-ca`/`verify-full`도 지원하며 CA 설정은 asyncpg 환경에 준비한다.
`disable`은 격리 로컬 테스트에만 사용한다. 드라이버의 `target_session_attrs=read-write`로 읽기 전용 서버 연결을 거부한다.
JDBC 전용 `targetServerType` 옵션은 사용하지 않는다.

## 기동과 migration

비로컬 앱은 Config를 읽고 로그 전송기를 초기화한 뒤 PostgreSQL 세션 advisory lock `(0x45564752, 1)`을 획득하고 Alembic `upgrade head`를 실행한다.
실패하면 HTTP/Eureka 및 거래 워커를 시작하지 않는다. `local` 프로필은 자동 DB 접속을 생략한다.
수동 명령은 local 프로필에서도 명시된 DB로 접속한다.

```sh
uv run python -m evergreen.database history
uv run python -m evergreen.database current
uv run python -m evergreen.database upgrade
```

초기 revision은 `0001_postgresql`이며 이력 테이블은 `evergreen_alembic_version`이다.
상태 ID는 애플리케이션이 지정하는 1이고, 이벤트 ID는 PostgreSQL BIGINT IDENTITY다.
시간과 payload는 원문 보존을 위해 각각 VARCHAR(40), TEXT로 저장한다. Grafana 조회 시 JSON으로 해석한다.
스키마 생성은 계좌 초기화나 실거래 활성화를 수행하지 않는다.

거래 워커와 스키마 명령은 같은 전용 세션 잠금을 사용한다. migration은 최대 60초 대기하고 워커는 즉시 경합을 거부한다.
잠금은 commit/rollback 후에도 유지되며, 모든 상태 접근과 주문 직전에 backend PID와 pg_locks 소유권을 검사한다.
연결을 잃거나 새 primary로 연결되면 기존 소유권을 사용할 수 없다. 재시도는 새 세션에서 잠금을 다시 획득한다.
replica 1, stop-first를 유지한다. 잠금은 서로 다른 서버의 split-brain을 해결하지 않으므로 Patroni의 단일 primary 운영이 필요하다.

추가 스키마 변경은 새 전진 revision으로 작성한다. 적용된 PostgreSQL revision을 수정하지 않는다.

## 검증

```sh
EVERGREEN_TEST_POSTGRESQL_URL='postgresql+asyncpg://USER:PASSWORD@127.0.0.1:PORT/evergreen_test?ssl=disable' \
  uv run pytest tests/test_trading_postgresql.py --no-cov
```

fixture는 `127.0.0.1`의 `evergreen_test`만 허용하고 테이블을 재생성한다. CI는 격리 PostgreSQL 18 서비스로 이 테스트를 실행하며 성공한 뒤 빌드·릴리스를 진행한다.
