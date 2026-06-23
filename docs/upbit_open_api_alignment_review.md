# Upbit Open API Alignment Review

검토일: 2026-06-23

## 1. 결론

Evergreen의 핵심 Upbit 연동 방향은 맞다. 잔고 조회, 주문 가능 정보, 현재가, 일 캔들, 주문 생성, 주문 조회, 주문 취소는 공식 API의 기본 흐름과 일치한다.

이번 검토에서 API 계약과 어긋난 부분은 코드에 반영했다.

- `market.max_total` 응답을 문자열로 처리하도록 DTO를 수정했다.
- 체결 대기 주문 조회 endpoint를 `/v1/orders/open`으로 수정했다.
- JWT `query_hash` 생성 시 요청 필드 순서를 보존하도록 수정했다.
- 주문 생성 DTO에서 null 필드를 JSON body에서 제외하도록 수정했다.

남은 리스크는 API 연결 자체보다 운영 안전장치 쪽이다. 특히 LIVE 자동 매수 금액 상한, rate limit 대응, 429/5xx retry/backoff, DB migration 정책은 후속 작업으로 남아 있다.

## 2. 공식 문서 확인 범위

| 주제 | 공식 문서 |
| --- | --- |
| REST API 기본 정책 | https://docs.upbit.com/kr/reference/rest-api-guide |
| 인증/JWT/query_hash | https://docs.upbit.com/kr/reference/auth |
| 요청 수 제한 | https://docs.upbit.com/kr/reference/rate-limits |
| 잔고 조회 | https://docs.upbit.com/kr/reference/get-balance |
| 주문 가능 정보 | https://docs.upbit.com/kr/reference/available-order-information |
| 주문 생성 | https://docs.upbit.com/kr/reference/new-order |
| 체결 대기 주문 목록 | https://docs.upbit.com/kr/reference/list-open-orders |
| 개별 주문 조회 | https://docs.upbit.com/kr/reference/get-order |
| 개별 주문 취소 | https://docs.upbit.com/kr/reference/cancel-order |
| 일 캔들 조회 | https://docs.upbit.com/kr/reference/list-candles-days |
| 현재가 조회 | https://docs.upbit.com/kr/reference/list-tickers |

## 3. Endpoint 대조

| 기능 | 공식 계약 | 기존 상태 | 검토 결과 |
| --- | --- | --- | --- |
| 잔고 조회 | `GET /v1/accounts` | 일치 | OK |
| 주문 가능 정보 | `GET /v1/orders/chance?market=KRW-BTC` | 일치 | OK |
| 현재가 조회 | `GET /v1/ticker?markets=KRW-BTC` | 일치 | OK |
| 일 캔들 조회 | `GET /v1/candles/days?market=KRW-BTC&count=N` | 일치 | OK |
| 주문 생성 | `POST /v1/orders` JSON body | 일치 | OK |
| 개별 주문 조회 | `GET /v1/order?uuid=...` | 일치 | OK |
| 개별 주문 취소 | `DELETE /v1/order?uuid=...` | 일치 | OK |
| 체결 대기 주문 조회 | `GET /v1/orders/open` | `/v1/orders` | 수정 완료 |

## 4. 주문 파라미터 대조

| Evergreen 주문 | 공식 Upbit 파라미터 | 구현 상태 |
| --- | --- | --- |
| 지정가 매수 | `side=bid`, `ord_type=limit`, `volume`, `price` | 일치 |
| 지정가 매도 | `side=ask`, `ord_type=limit`, `volume`, `price` | 일치 |
| 시장가 매수 | `side=bid`, `ord_type=price`, `price`만 사용 | 일치 |
| 시장가 매도 | `side=ask`, `ord_type=market`, `volume`만 사용 | 일치 |
| 주문 식별자 | `identifier` | `clientOrderId` 전달 |

주문 생성 문서에는 market buy에서 `volume`을 입력하지 않고, market sell에서 `price`를 입력하지 않는 패턴이 명시되어 있다. 이에 맞춰 `UpbitCreateOrderRequest`는 null 필드를 직렬화하지 않는다.

## 5. 인증 대조

공식 인증 요구사항:

- Exchange API는 JWT Bearer 토큰을 사용한다.
- payload에는 `access_key`와 매 요청마다 새로운 `nonce`가 필요하다.
- query parameter 또는 body가 있는 요청은 `query_hash`와 `query_hash_alg=SHA512`가 필요하다.
- query_hash는 실제 요청 query string 또는 body를 query string 형태로 만든 문자열과 순서가 일치해야 한다.
- Secret Key는 Base64 decode하지 않고 원문을 서명 키로 사용한다.

구현 상태:

- `UpbitJwtSigner`가 HS512 JWT를 직접 생성한다.
- `UpbitAuthRequestInterceptor`가 Feign 요청에 `Authorization: Bearer ...`를 추가한다.
- query/body가 없으면 `query_hash`를 생략한다.
- query/body가 있으면 field order를 보존해 SHA-512 해시를 생성한다.
- null body field는 주문 DTO 직렬화 단계에서 제외한다.

## 6. DTO 대조

| DTO | 공식 응답/요청 특징 | 구현 상태 |
| --- | --- | --- |
| `UpbitAccountResponse` | 수량/금액 필드는 문자열 numeric | String으로 수신 후 BigDecimal 변환 |
| `UpbitOrderChanceResponse` | `market.max_total` 포함 | 문자열 `max_total` 처리로 수정 |
| `UpbitCreateOrderRequest` | numeric 값은 문자열, market buy/sell의 불필요 field 생략 | 문자열 DTO, null field 제외 |
| `UpbitOrderResponse` | 주문/체결 수량과 금액은 문자열 numeric | String으로 수신 후 BigDecimal 변환 |
| `UpbitDayCandleResponse` | 가격/거래량은 numeric | BigDecimal 수신 |
| `UpbitTickerResponse` | 현재가는 numeric | BigDecimal 수신 |

## 7. 운영 안전성 평가

### 양호

- 자동 주문 전 로컬 활성 주문과 거래소 미체결 주문을 확인한다.
- LIVE 모드에서 계좌 잔고를 기준으로 포지션을 동기화한다.
- PAPER 모드가 있어 거래소 주문 없이 전략 흐름을 검증할 수 있다.
- 주문 생성/조회/취소와 체결 reconciliation 테스트가 있다.
- 구조화 로그로 신호, 체결, 포지션 drift를 추적할 수 있다.

### 보완 필요

1. LIVE 자동 매수 금액 상한이 없다.
   - 현재 자동 신호 매수는 가격을 비워 가드 로직에서 가용 KRW 기준으로 주문 금액을 산정한다.
   - 운영 실수 방지를 위해 `liveOrderNotionalCap` 또는 allocation 비율 설정이 필요하다.

2. Upbit rate limit 대응이 없다.
   - 공식 문서 기준 quotation group은 IP 단위 10 rps, exchange default는 계정 단위 30 rps, order group은 계정 단위 8 rps로 제한된다.
   - client-side limiter와 429 처리 정책이 필요하다.

3. Upbit 오류 매핑이 제한적이다.
   - 현재 Feign 예외는 전역 handler에서 처리되지만, `insufficient_funds_bid`, `under_min_total_bid`, `invalid_query_payload` 같은 Upbit error.name을 도메인 코드로 정교하게 매핑하지 않는다.

4. 주문 가능 정보의 최소 주문 금액 정책을 적극적으로 검증하지 않는다.
   - 현재 주로 잔고 검증에 초점이 있다.
   - `market.bid`, `market.ask`, `market.max_total` 기반 최소/최대 금액 검증은 후속으로 추가할 수 있다.

5. DB migration 체계가 없다.
   - JPA entity는 있지만 Flyway/Liquibase migration 기준이 보이지 않는다.
   - 운영 DB에는 `ddl-auto=validate`와 별도 migration script가 필요하다.

6. 전략과 운영 주문 한도 사이의 제품 정책이 문서화되어야 한다.
   - 신호 자체와 자금 배분 정책을 분리해야 한다.

## 8. 권장 후속 작업

| 우선순위 | 작업 | 이유 |
| --- | --- | --- |
| P0 | LIVE 자동 매수 금액 상한 추가 | 가장 직접적인 금전 리스크 |
| P0 | Upbit error decoder 추가 | 운영 장애 원인 분류 개선 |
| P1 | rate limit/backoff 정책 추가 | API 차단과 반복 실패 방지 |
| P1 | 주문 가능 정보 기반 min/max validation | 거래소 reject 사전 차단 |
| P1 | `Test Order` API dry-run 검증 옵션 | LIVE 전 요청 형식 검증 |
| P2 | DB migration 도입 | 배포 재현성과 운영 안정성 |
| P2 | `time_in_force`, `smp_type` 지원 여부 결정 | 최신 주문 옵션 활용 여부 명확화 |

## 9. 검증 명령

```bash
./gradlew test
```

문서 변경만 있을 때도 기존 API 계약 테스트가 깨지지 않는지 전체 테스트를 유지한다.
