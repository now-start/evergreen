# Evergreen PRD

검토일: 2026-06-23

## 1. 제품 개요

Evergreen은 Upbit Open API를 이용해 일봉 기반 매매 전략을 자동 실행하는 Spring Boot 서비스다. 사용자는 백테스트로 검증한 전략 파라미터를 운영 설정으로 주입하고, `PAPER` 모드에서 가상 체결을 관찰한 뒤 `LIVE` 모드로 실제 주문을 실행한다.

## 2. 문제 정의

수동 매매는 신호 해석, 주문 실행, 포지션 추적, 미체결 주문 확인, 성과 기록이 사람의 상태에 의존한다. Evergreen은 이 과정을 반복 가능하고 관측 가능한 서버 프로세스로 만들기 위해 존재한다.

현재 프로젝트는 V5 전략, Upbit 연동, 주문/체결/포지션 저장, 구조화 로그를 갖추고 있다. 다만 운영 안전성 관점에서는 API 계약 변경 대응, 주문 한도 정책, rate limit 대응, 문서화된 운영 절차가 제품 요구사항으로 명확해야 한다.

## 3. 목표

- KRW 마켓 중심의 일봉 전략 신호를 안정적으로 계산한다.
- `PAPER` 모드와 `LIVE` 모드가 동일한 주문 도메인 모델을 공유한다.
- Upbit Open API의 인증, 주문, 잔고, 시세 조회 계약을 준수한다.
- 자동 주문 실행 전후에 중복 주문, 외부 미체결 주문, 계좌 포지션 불일치를 감지한다.
- Loki/Grafana 기반 로그로 신호, 주문, 체결, 성과 지표를 추적한다.
- 운영자가 설정 변경과 배포 전 검증 기준을 문서만 보고 이해할 수 있게 한다.

## 4. 비목표

- 초단타, 분봉, 호가 기반 고빈도 매매.
- 다중 거래소 통합.
- 출금, 입금, Travel Rule 자동화.
- 사용자별 멀티테넌트 거래 서비스.
- 투자 수익 보장 또는 투자 추천 서비스.

## 5. 대상 사용자

- 개인 운영자: 단일 계좌와 제한된 마켓 목록으로 전략을 자동 실행한다.
- 개발 운영자: Spring Boot, DB, Grafana, Config Server를 관리하며 장애를 진단한다.
- 전략 연구자: Python 백테스트 결과와 Java 운영 전략의 파라미터 일관성을 확인한다.

## 6. 핵심 사용자 시나리오

1. 운영자는 `PAPER` 모드로 서비스 배포 후 매일 생성되는 `candle_signal` 로그와 대시보드를 확인한다.
2. 운영자는 전략 신호와 주문 결과가 기대 범위에 있음을 확인한 뒤 `LIVE` 모드로 전환한다.
3. 서비스는 스케줄마다 대상 마켓의 일봉 데이터를 조회하고, 닫힌 캔들 기준으로 전략을 평가한다.
4. 서비스는 계좌 잔고와 기존 포지션을 동기화하고 외부 미체결 주문이 있으면 자동 주문을 차단한다.
5. 매수/매도 신호가 발생하면 서비스는 주문 요청을 생성하고 Upbit에 주문을 제출하거나 PAPER 체결을 기록한다.
6. 운영자는 주문 조회/취소 API와 Grafana 대시보드로 상태를 확인한다.

## 7. 기능 요구사항

### 7.1 시장 데이터

- Upbit 일 캔들 API에서 대상 마켓의 OHLCV 데이터를 조회한다.
- 전략 warmup 요구량과 설정된 `candleCount` 중 더 큰 값을 사용한다.
- 기본 신호는 미완성 최신 캔들을 제외한 닫힌 캔들 기준으로 산출한다.
- 현재가 API는 live price 보정과 MARKET_SELL 추정 금액 계산에 사용한다.

### 7.2 전략 평가

- 활성 전략 버전은 설정값으로 선택한다.
- 현재 지원 전략은 V5이며, 전략별 파라미터 resolver를 통해 운영 파라미터를 구성한다.
- 전략 결과는 매수/매도 여부, 사유, 진단 지표를 포함해야 한다.

### 7.3 주문 실행

- `PAPER` 모드는 거래소 주문을 만들지 않고 내부 주문/포지션 상태만 갱신한다.
- `LIVE` 모드는 Upbit 주문 API를 사용한다.
- 지정가 주문은 `ord_type=limit`, 시장가 매수는 `ord_type=price`, 시장가 매도는 `ord_type=market`으로 전송한다.
- 주문에는 Upbit `identifier`로 사용할 수 있는 고유 `clientOrderId`를 포함한다.
- 동일 신호 timestamp의 중복 주문은 차단한다.
- 로컬 활성 주문 또는 거래소 미체결 주문이 있으면 자동 신호 주문을 차단한다.

### 7.4 포지션과 체결

- LIVE 모드에서는 Upbit 계좌 잔고를 기준으로 총 보유 수량을 동기화한다.
- 내부 주문 이력 기준의 managed quantity와 거래소 총 수량 차이를 스냅샷으로 남긴다.
- 주문 조회/취소 응답에 포함된 체결 내역은 중복 저장되지 않아야 한다.

### 7.5 운영 API

- `/api/trading/balances`: 잔고 조회.
- `/api/trading/orders/chance`: 주문 가능 정보 조회.
- `/api/trading/orders`: 수동 주문 생성.
- `/api/trading/orders/{clientOrderId}`: 주문 조회.
- `/api/trading/orders/{clientOrderId}/cancel`: 주문 취소.
- `/api/trading/signal-execute`: 전략 신호 수동 실행.
- `/api/trading/coexistence/status`: 외부 주문/포지션 공존 상태 조회.

## 8. 안전 요구사항

- LIVE 모드 전환 전 PAPER 모드 검증은 필수 운영 절차로 둔다.
- API 키는 최소 권한으로 발급한다: 자산조회, 주문조회, 주문하기.
- Upbit API Key 허용 IP 등록을 운영 체크리스트에 포함한다.
- 시장가 주문은 잔고 부족, 수량 부족, 기준 가격 없음 상태를 명확히 거절해야 한다.
- 자동 매수 주문은 운영상 매우 큰 리스크가 있으므로 live notional cap 또는 포트폴리오 allocation 정책을 제품 요구사항으로 추가해야 한다.
- Upbit rate limit 초과, 429, 5xx 응답에 대한 재시도/백오프 정책을 정의해야 한다.

## 9. 비기능 요구사항

- 테스트: core service와 Upbit DTO 계약은 단위 테스트로 고정한다.
- 관측성: 핵심 이벤트는 `event=` prefix의 구조화 로그로 남긴다.
- 설정: 운영값은 Config Server 또는 환경변수로 주입한다.
- 보안: Secret Key는 로그, 문서, 테스트 fixture에 노출하지 않는다.
- 배포: 운영 DB에서는 `ddl-auto=validate` 또는 명시적 migration 전략을 사용한다.

## 10. 성공 지표

- 스케줄 실행 실패율.
- Upbit API decode/auth 오류 건수.
- 자동 주문 차단 사유별 건수.
- 신호 발생 수, 주문 제출 수, 체결 수.
- realized/unrealized return, max drawdown, win rate, expectancy.
- 외부 포지션 drift 발생 빈도.

## 11. 출시 기준

- 전체 테스트가 통과한다.
- Upbit 공식 문서와 Feign endpoint, 요청/응답 DTO, JWT query_hash 생성 방식이 일치한다.
- PAPER 모드에서 최소 2주 이상의 스케줄 실행 로그를 확인한다.
- LIVE 전환 전 API 키 권한, 허용 IP, 주문 금액 한도, 알람 경로를 확인한다.
- Grafana 대시보드에서 신호와 주문 이벤트를 추적할 수 있다.

## 12. 후속 요구사항

- LIVE 자동 매수 금액 상한 설정.
- Upbit `Test Order` API를 이용한 운영 전 dry-run 검증.
- rate limit group별 client-side limiter.
- Feign error decoder로 Upbit error.name 기반 도메인 예외 매핑.
- DB migration 체계 도입.
- 주문 생성 옵션 `time_in_force`, `smp_type` 지원 여부 결정.
