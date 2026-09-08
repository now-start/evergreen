# DB 마이그레이션

Java/Spring의 Flyway 역할을 Python에서는 이미 의존성에 포함된 **Alembic**이 담당한다.
접속은 기존 Config Server의 `spring.datasource.url/username/password`와 asyncmy를 사용한다.
새 마이그레이션 서버나 별도 DB 비밀번호 설정은 없다.

## 배포 시 자동 적용

Docker 기본 명령 `evergreen`의 비로컬 시작 순서:

1. Config Server 설정을 읽는다.
2. MariaDB 세션 잠금을 획득하고 Alembic `upgrade head`를 실행한다.
3. 성공한 경우에만 OpenTelemetry, HTTP/관리 포트, Eureka 등록을 시작한다.
4. 비로컬·실거래 활성화 설정일 때 API lifespan에서 매매 루프를 함께 시작한다.

새 이미지가 시작되면 미적용 revision만 실행된다. 최신 버전이면 DDL을 반복하지 않는다.
마이그레이션 오류, 알 수 없는 DB revision, 잠금 시간 초과는 **기동 실패**로 처리한다.
기존 Docker healthcheck는 이 단계가 끝나기 전 성공하지 않는다.
동시 시작한 인스턴스는 동일 MariaDB 서버의 잠금에서 최대 60초 대기한 뒤 순서대로 검사한다.
활성 매매 루프는 같은 잠금을 계속 보유하므로 다른 인스턴스의 기동이 실패할 수 있다.
현재 통합 실행은 replica 1과 stop-first 배포를 전제로 한다.
`local` 프로필은 자동 DB 접속을 생략한다. 수동 명령으로는 로컬 프로필에서도 적용할 수 있다.

거래 워커도 같은 revision을 확인한 뒤 시작한다. 마이그레이션과 워커는 같은 세션 잠금을 사용한다.
활성 거래 워커가 있으면 먼저 정상 종료한 뒤 스키마를 적용해야 한다.
단일 writer를 전제로 하며 독립 서버/멀티 라이터 사이를 보호하는 분산 잠금은 아니다.

**스키마 생성은 계좌 상태 생성이나 실거래 활성화가 아니다.**
초기 revision은 두 테이블만 만들며 계좌 행을 넣거나 Upbit에 접속하지 않는다.
`--approve-initialization`(기존 `--initialize-state` 별칭)은 빈 DB에 최초 설치 승인만 기록한다.
실행 상태가 없어도 웹 서비스 기동은 가능하다. 거래 워커는 승인 기록이 있을 때만
계좌 검증 후 상태를 자동 생성한다. 기존 운영 이력이 있으면 초기화하지 않고 복구를 요구한다.
자세한 승인·복구 구분은 [주문 실행기](trading-execution.md)의 MariaDB 절차를 따른다.

## 파일과 이력

```text
src/main/evergreen/database/
├── config.py                 # 기존 Spring datasource
├── connection.py             # MariaDB 세션 잠금
├── bootstrap.py              # 배포 기동 전 실행, 실패 시 중단
├── migration.py              # Alembic 실행·검증된 기존 테이블 편입
├── __main__.py               # 수동 관리 명령
└── migrations/
    ├── env.py
    └── versions/v0001_execution.py
```

revision 파일은 wheel과 Docker 이미지에 포함된다. 외부 볼륨이나 컨테이너별 SQL 복사는 필요 없다.
DB의 `evergreen_alembic_version`이 현재 적용 revision을 저장한다.
다른 애플리케이션의 Flyway/Alembic 이력 테이블은 사용하거나 수정하지 않는다.

```bash
# 코드의 revision 목록: Config Server나 DB 접속 없음
uv run python -m evergreen.database history

# 현재 DB revision 조회 / 미적용 revision 수동 적용
uv run python -m evergreen.database current
uv run python -m evergreen.database upgrade
```

연결 정보는 Config Server에서 가져오며 명령줄에 비밀번호를 넣지 않는다.
현재 코드에는 별도 Alembic 활성화 스위치가 없으며 비로컬 배포에서는 반드시 적용한다.
운영 DB에 실제 적용하거나 배포한 것은 아니므로 최초 배포 전에 백업과 접속 권한을 확인한다.

## 기존 alpha.5 테이블이 있는 경우

`create_all`로 이미 두 실행 테이블을 만든 DB는 자동으로 신규 DB 취급하지 않는다.
워커 중지 및 DB 백업 후 한 번만 실행한다.

```bash
uv run python -m evergreen.database baseline
```

초기 revision에 고정된 테이블 구조와 비교하고, 두 테이블이 모두 InnoDB이며 컬럼·타입·기본 키·AUTO_INCREMENT·제약이
일치해야 `0001_execution`을 기록한다. 행은 수정하지 않는다.
구조 불일치·일부 테이블 누락·기존 revision 존재 시 거부한다.
baseline 후에는 다음 배포부터 일반 자동 upgrade를 사용한다.
서로 다른 계정·DB의 상태를 병합하거나 주문 상태를 초기화하는 기능은 아니다.

## 다음 스키마 변경과 장애

다음 변경은 `versions/v0002_설명.py`처럼 새 파일을 추가하고
`revision`, `down_revision="0001_execution"`, `upgrade()`, `downgrade()`를 정의한다.
이름보다 revision 연결 관계가 적용 순서를 결정한다.
운영에 적용한 파일은 수정하지 않는다. Alembic은 Flyway의 체크섬 검증을 그대로 제공하는 도구는 아니다.
현재 초기 revision의 downgrade는 주문 복구 데이터 삭제를 막기 위해 명시적으로 거부한다.
수정이 필요하면 새 전진 migration을 작성한다.

MariaDB DDL은 전체 트랜잭션 롤백을 보장하지 않는다. 중간 실패 시 일부 테이블이 남을 수 있다.
이력을 강제 stamp하거나 테이블을 삭제해 재시도하지 말고 실제 스키마와 실패 지점을 먼저 확인한다.
schema migration과 계좌 데이터 변경은 분리한다.
CREATE/ALTER/INDEX 등 해당 revision에 필요한 권한과 이력 테이블의 SELECT/INSERT/UPDATE 권한이 필요하다.
큰 테이블 변경은 운영 크기의 복제 데이터로 잠금·소요 시간을 별도 검증해야 한다.

구현 방식은 [Alembic의 연결 공유 및 비동기 실행 가이드](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)를 따른다.
