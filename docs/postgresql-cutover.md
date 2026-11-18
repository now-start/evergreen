# Evergreen 3.0.0 전환 준비

3.0은 PostgreSQL 전용으로 신규 구축한다. 2.x DB 호환 코드와 롤백 경로는 제공하지 않는다.
원본의 상태 1행과 핵심 이벤트 16행을 보존하고 나머지 평가·진단 이력은 신규 DB에 적재하지 않는다.
이 문서는 실행 절차이며 운영 전환을 수행했다는 기록은 아니다.

## 준비된 도구

- `scripts/export_v2.py`: **2.2.7 환경**에서 실행하는 일회성 추출기. 3.0 이미지에는 asyncmy가 없다.
- `python -m evergreen.database.transfer`: 3.0 환경에서 스냅샷 검증 및 빈 PostgreSQL 적재.
- 추출은 원본 거래 잠금을 획득해야 하며 활성 워커가 있으면 실패한다. 원본 revision 0002만 허용하고 READ ONLY consistent snapshot을 읽는다.
- 상태는 id=1 한 행이어야 하며 pending 주문이 있으면 중단한다. 예상 이벤트 수는 기본 16이다. 이 값이 달라지면 원인을 확인한 뒤 `--expected-events N`으로 명시한다.
- 보존 종류: initialization-approved, initialized, cashflow-reconciled, order-intent, order-accepted, order-terminal, strategy-transition.
- 생략 종류: valuation, unexpected-balance, strategy-evaluated, no-signal. 미분류 종류가 있으면 추출을 중단한다.
- 파일에는 SHA-256 체크섬, 원본 전체 최대 ID/다음 ID, 상태·선택 이벤트 원문이 들어간다. 체크섬은 손상 검사용이며 발신자 인증은 아니다.
- 적재 전 손익 장부와 마지막 재무 이벤트의 잔액·주문 순번을 검증한다. 전체 적재와 원문 재조회 비교, sequence RESTART를 하나의 트랜잭션으로 처리한다.
- 대상이 비어 있지 않으면 중단한다. 반복 실행은 기존 데이터 덮어쓰기를 하지 않는다.
- 자동 증가 ID는 생략한 원본 이벤트까지 포함한 최대 ID보다 크게 시작하여 과거 ID를 재사용하지 않는다.

## 설정·전환 순서

1. 3.0.0 이미지를 빌드·검증한다. Config Server의 evergreen 전용 DB와 소유 계정을 PostgreSQL에 준비한다.
   기존 MariaDB 계정을 다른 DB에 자동 재사용하지 않는다. 새 계정의 비밀번호는 Config 암호화 절차로 저장한다.
2. GitOps 동기화가 작업 중 coin을 재기동하지 않도록 조정하고 `evergreen_coin` 및 별도 워커를 중지한다.
   lotto는 변경하지 않는다. 실제 거래소 미체결 주문과 DB pending을 대조한다.
3. 원본 2.2.7 이미지로 **앱 서버 대신 추출 명령만 실행하는 일회성 컨테이너**를 만든다.
   `platform_default` 네트워크와 기존 Config를 사용하고, 스크립트는 읽기 전용 마운트, 출력 폴더는 보호된 호스트 경로를 사용한다.
   Config 전환 전에 수행해야 한다. 기존 컨테이너가 scale 0으로 사라지므로 그 컨테이너에 docker exec하지 않는다.

   ```sh
   python /tools/export_v2.py /transfer/evergreen-v2.json
   ```

   출력 파일에는 계좌·주문 데이터가 있다. 생성 모드는 0600이며 Git/로그에 올리지 않는다. 비root 컨테이너 UID가 출력 폴더에 쓸 수 있어야 한다.
   스냅샷 파일을 읽을 수 있는 사용자·컨테이너만 대상 단계에 접근시킨다.
4. 오프라인 검증을 3.0 환경에서 먼저 실행한다. 이 명령은 DB·Config·거래소에 접근하지 않는다.

   ```sh
   python -m evergreen.database.transfer /transfer/evergreen-v2.json --check-only
   ```

5. Config의 datasource를 `jdbc:postgresql://postgres_haproxy:5432/evergreen?sslmode=require&connectTimeout=3`으로 변경한다.
   계정도 PostgreSQL 전용 값으로 변경하고 `evergreen.execution.live-enabled=false`로 유지한다.
   아직 서버를 띄우지 않고 3.0 이미지의 일회성 명령으로 적재한다.

   ```sh
   python -m evergreen.database.transfer /transfer/evergreen-v2.json
   python -m evergreen.database current
   ```

   적재기가 빈 DB에 `0001_postgresql` 스키마를 만들고 검증된 행을 넣는다. 2.x Alembic 이력은 복사하지 않는다.
   이미 다른 앱 테이블이 있는 DB가 아닌 전용 evergreen DB를 사용한다.
6. GitOps coin 이미지를 `ghcr.io/now-start/evergreen:3.0.0`으로 바꾸고 replica 1로 기동한다.
   readiness, `0001_postgresql`, 상태/이벤트 수, 잠금, DB TLS, 손익과 잔액 기준선을 확인한다.
7. 구 워커가 없는 상태에서 실거래 설정을 활성화하고 앱을 재시작한다. 주문 순번, pending 대사, 새 이벤트 ID를 확인한다.
   기존 상태를 이관하므로 `--approve-initialization`은 실행하지 않는다.

원본 삭제는 이 전환의 필수 작업이 아니다. 데이터 이관 실패 시 원인을 수정하고 빈 대상에서 다시 실행한다.
이관 없이 기존 상태를 초기화하거나 구 버전으로 되돌리는 절차는 포함하지 않는다.

## 이번 준비의 검증 기록 (2026-11-18)

- 보안 패치 및 PostgreSQL 18 통합 테스트 포함 전체 1,063개 통과, 커버리지 90.20%.
- Ruff / format / mypy 통과. 3.0.0 wheel·sdist 빌드 및 신규 revision 단독 포함 확인.
- 격리 MariaDB의 합성 데이터로 추출기를 실행하고 PostgreSQL로 가져오는 리허설 완료.
  활성 원본 잠금 거부, 선택 이벤트 추출, 파일 덮어쓰기 거부, 원본 전체 sequence 보존을 검증했다.
- 운영 DB 읽기 전용 사전검증에서 현재 상태 1행과 핵심 이벤트 16행의 손익 장부·잔액 기준선 일치 확인.
  원본과 운영 배포는 변경하지 않았다.
- 최초 검사에서 발견한 기존 의존성 취약점은 aiohttp, certifi, dnspython, filelock, msgpack,
  OpenTelemetry 계열, setuptools 갱신으로 해결했다. GitHub 교차 검사에서 추가 확인된
  Mako 취약점도 1.4.3으로 갱신하고 migration·이관 테스트 38개를 재검증했다. `pip-audit` 재검사에서 알려진 취약점 없음.
  자체 패키지 evergreen과 전용 인덱스의 torch+cpu는 감사 도구의 PyPI 검사 대상에서 제외된다.
