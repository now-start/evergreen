# Evergreen

차트 데이터만을 사용해 비트코인을 자동 매매하는 Python 프로젝트입니다.

현재 단계에서는 거래 전략을 구현하지 않고, 재현 가능한 개발 환경과 검증 가능한
패키지 구조만 구성합니다. 거래소, 데이터 저장소, 모델 구조, 자동 승격 기준은 설계
문서에서 결정한 뒤 추가합니다.

## 개발 환경

Python 3.12와 [uv](https://docs.astral.sh/uv/)를 사용합니다.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pip-audit
```

`uv.lock`을 기준으로 동일한 의존성 버전을 설치합니다.

애플리케이션 패키지는 `src/main/evergreen`에 두며, import 경로는
`evergreen`을 사용합니다.

```text
src/main/evergreen/
├── api.py          # FastAPI 앱 팩토리와 lifespan
├── main.py         # 실행 진입점과 Uvicorn 포트 바인딩
└── platform/       # Config Server, Eureka, Actuator, OpenTelemetry 연동
```

비즈니스 기능은 `evergreen` 아래에 기능 단위로 추가하고, 공통 Platform 연동은
`platform` 패키지에 한정합니다. 사용하지 않는 계층이나 빈 패키지는 미리 만들지
않습니다.

로컬에서는 다음 명령으로 실행합니다.

```bash
cp .env.example .env
uv run evergreen
```

`local` 프로필에서는 Config Server, Eureka, OpenTelemetry를 비활성화합니다.

## Docker

이미지는 uv lockfile을 사용해 빌드하며, 런타임에서는 UID `10001`의 비루트
사용자로 실행합니다. 빌드 컨텍스트에는 패키징에 필요한 파일만 포함됩니다.

```bash
docker build -t evergreen:local .
docker run --rm \
  -p 8080:8080 \
  -p 8081:8081 \
  -e SPRING_PROFILES_ACTIVE=local \
  evergreen:local
```

컨테이너 healthcheck는 관리 포트의 `/actuator/health`를 확인합니다.
PyTorch는 범용 Swarm 노드에서 불필요한 CUDA 라이브러리를 설치하지 않도록 CPU
전용 wheel을 사용합니다. GPU 실행 환경은 별도 이미지 정책을 결정한 뒤 추가합니다.

## 버전

프로젝트 버전은 `pyproject.toml`에서 SemVer 표기인 `2.0.0-alpha.4`로 관리합니다.
Python 패키지 메타데이터와 `uv.lock`, OpenAPI에는 PEP 440 정규화 결과인
`2.0.0a4`이 표시됩니다. Git 태그와 Docker 이미지 태그는 원래 SemVer 표기를
사용합니다.

## CI

GitHub Actions는 `now-start/workflow`의 `reusable-python-app.yaml`을 호출합니다.
`main`/`develop` push, 두 브랜치를 대상으로 하는 PR, 수동 실행에서 포맷, lint,
타입 검사, 테스트, 의존성 취약점 감사를 실행합니다.

`main` push에서만 검증 후 `linux/amd64`, `linux/arm64` 이미지를 버전 태그로
발행하고 GitHub Release를 생성합니다. 알파/베타/RC 버전은 prerelease로 표시합니다.

예: `ghcr.io/now-start/evergreen:2.0.0-alpha.4`, Git 태그 `2.0.0-alpha.4`.
발행된 버전은 덮어쓰지 않으므로 새 릴리스에는 버전을 올려야 합니다.
`latest` 같은 가변 태그와 서비스 배포는 이 파이프라인에서 관리하지 않습니다.

## Spring Platform 연동

- 서비스 이름: `evergreen`
- Config Server: Spring과 같은 `SPRING_CONFIG_IMPORT` 설정 사용
- 서비스 등록: Eureka 등록, heartbeat, 종료 시 deregistration
- 애플리케이션 포트: `server.port` (`8080`)
- 관리 포트: `management.server.port` (`8081`)
- Admin Swagger 문서: Gateway가 제공하는 `/evergreen/v3/api-docs`
- Actuator: 관리 포트에서 Pyctuator의 전체 `/actuator/**` 엔드포인트 제공
- 관측 데이터: OpenTelemetry Collector를 통한 OTLP traces, metrics, logs

비로컬 프로필의 시작 순서는 다음과 같습니다.

1. `.env`와 프로세스 환경변수 로드
2. Config Server의 `{application}-{profile}.json?resolvePlaceholders=true` 조회
3. 원격 설정을 환경변수보다 낮은 우선순위로 병합하고 Pydantic으로 재검증
4. 원격 OTLP 설정으로 OpenTelemetry 자동 계측 초기화
5. 애플리케이션 포트와 관리 포트 시작
6. Eureka 등록 및 heartbeat 시작
7. 종료 시 Eureka deregistration

애플리케이션은 Eureka에만 등록합니다. Gateway는 discovery locator로
`/evergreen/**` 경로를 자동 생성하고, Spring Boot Admin은 Eureka에서 서비스를
발견합니다. Admin의 Swagger 수집기도 Eureka 서비스 목록을 읽어
`/evergreen/v3/api-docs`를 자동 등록하므로 별도의 Gateway/Admin 직접 등록 설정은
없습니다. Swagger UI는 Admin에서만 제공하며, 서비스 자체는 OpenAPI JSON만
제공합니다. Eureka에는 `management.port=8081`, `/actuator/info`,
`/actuator/health`를 함께 광고합니다.

비로컬 환경에서는 애플리케이션 이름, 활성 프로필, Config Server 주소 같은
부트스트랩 값만 실행 환경에 둡니다. Eureka 주소, 관리 포트, OTLP 주소를 포함한
운영 설정은 Config Server에서 가져옵니다. 인스턴스 호스트명을 명시해야 하는
환경만 Spring 표준 키인 `eureka.instance.hostname`을 사용합니다.

`opentelemetry-distro`가 API와 SDK 초기화를 통합하고, 설치된 FastAPI, HTTPX,
SQLAlchemy, logging, system-metrics instrumentation이 OTLP exporter를 통해
Collector로 전송됩니다. 초기화는 Config Server 설정을 읽은 뒤 애플리케이션
엔트리포인트에서 수행하므로 별도의 `opentelemetry-instrument` 래퍼는 사용하지
않습니다.

OTLP 프로토콜의 기본값은 Spring Boot starter와 동일한 `http/protobuf`입니다.
Config Server의 `otel.exporter.otlp.protocol` 또는 환경변수
`OTEL_EXPORTER_OTLP_PROTOCOL`로 지정한 값과 신호별 설정은 그대로 우선합니다.
