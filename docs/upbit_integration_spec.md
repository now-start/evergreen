# Upbit 연동 정의서 (Evergreen 전략 엔진)

## 1. 목적
본 문서는 Evergreen 전략 엔진(`v5` primary, `v4` shadow)의 데이터 수집/운영 연동을 위한 Upbit API 계약(Contract)을 정의한다.

## 2. 범위
## 2.1 1차 범위 (필수)
- 공개 시세 API 기반 일봉 OHLCV 수집
- 로컬 캐시/DB 저장
- 재시도/레이트리밋/장애 대응
- 인증 기반 Exchange API 연동
  - 잔액 조회
  - 주문 가능 정보 조회
  - 주문 생성(매수/매도)
  - 주문 조회/취소

## 2.2 2차 범위 (확장)
- WebSocket 기반 실시간 보조 데이터
- 다중 주문 일괄 취소/대체 주문(cancel-and-new) 자동화

## 3. 인터페이스 요약
## 3.1 REST Endpoint (공개)
- `GET https://api.upbit.com/v1/candles/days`
  - Query:
    - `market` (예: `KRW-BTC`)
    - `to` (ISO8601 UTC 시각)
    - `count` (최대 200)
  - 사용 목적:
    - 일봉 데이터 배치 수집(역방향 pagination)

## 3.2 REST Endpoint (인증 필요, Exchange)
- `GET https://api.upbit.com/v1/accounts` (계정 잔고 조회)
- `GET https://api.upbit.com/v1/orders/chance?market={market}` (주문 가능 정보)
- `POST https://api.upbit.com/v1/orders` (주문 생성)
- `GET https://api.upbit.com/v1/order?uuid={uuid}` (개별 주문 조회)
- `DELETE https://api.upbit.com/v1/order?uuid={uuid}` (주문 취소)

참고:
- 한국 리전 기준 base URL은 `https://api.upbit.com`
- 글로벌 리전은 `https://{region}-api.upbit.com` 형식 사용

## 3.3 응답 필드 매핑 (엔진 공통)
- `candle_date_time_utc` -> `timestamp`
- `opening_price` -> `open`
- `high_price` -> `high`
- `low_price` -> `low`
- `trade_price` -> `close`
- `candle_acc_trade_volume` -> `volume`

## 4. 수집 프로토콜
## 4.1 배치 수집 방식
1. `cursor = to_dt`
2. `count=200`으로 호출
3. batch의 가장 오래된 bar timestamp를 확인
4. 다음 `cursor = oldest - 1 second`
5. `from_dt` 이전까지 반복
6. 최종적으로 `[from_dt, to_dt]` 범위만 남기고 시간오름차순 정렬

## 4.2 중복/정렬/정합성
- 키: `timestamp` 기준 dedup
- 최종 정렬: `timestamp ASC`
- 무효 row 제거:
  - 필수 숫자 필드 파싱 실패
  - `high < low` 등 비정상 데이터

## 5. 캐시/저장 규격
## 5.1 파일 경로 규칙
- 기본 디렉터리: `outputs/data/upbit-cache`
- 파일명:
  - `{market}_{fromYYYYMMDD}_{toYYYYMMDD}_days.csv`
  - 예: `KRW-BTC_20200101_20260220_days.csv`

## 5.2 CSV 스키마
- header:
  - `candle_date_time_utc,opening_price,high_price,low_price,trade_price,candle_acc_trade_volume`
- UTC 기준 저장

## 6. 레이트리밋/재시도 정책
## 6.1 호출 간격
- 최소 호출 간격: 150ms

## 6.2 재시도
- 재시도 대상:
  - HTTP `429`, `5xx`
  - 네트워크 일시 오류
- 기본 정책:
  - 최대 6회
  - 지수 백오프(기본 400ms)
  - `Retry-After` 헤더 존재 시 우선 적용

## 6.3 실패 처리
- 최종 실패 시 run status를 `FAILED_INGESTION`으로 기록
- 장애 알림(슬랙/이메일) 전송
- 이전 캐시 fallback 가능 시 `DEGRADED` 상태로 실행 여부 결정

## 7. 보안/운영 정책
## 7.1 공개 API 구간
- 현재 1차 범위는 공개 캔들 API만 사용(인증키 미사용)
- User-Agent 명시: `evergreen-backtest/1.0`

## 7.2 인증 API (잔고/주문) 정책
- Access/Secret key는 시크릿 매니저(KMS/Vault) 보관
- 키 로테이션 정책 수립
- 최소권한 원칙:
  - 자산조회(잔액 조회)
  - 주문조회
  - 주문하기
- 허용 IP를 API 키 화이트리스트에 등록

## 7.3 JWT 인증 규격
- 알고리즘: `HS512`
- 공통 payload:
  - `access_key`
  - `nonce` (요청마다 UUID 신규 생성)
- 쿼리/바디 파라미터가 있는 경우:
  - query string canonicalization 후 `SHA512` 해시 생성
  - payload에 `query_hash`, `query_hash_alg=SHA512` 포함
- 헤더:
  - `Authorization: Bearer {jwt_token}`

## 7.4 주문 파라미터 규약
- 공통:
  - `market`: 예) `KRW-BTC`
  - `side`: `bid`(매수), `ask`(매도)
  - `ord_type`
    - `limit` (지정가)
    - `price` (시장가 매수)
    - `market` (시장가 매도)
- 지정가:
  - `volume`, `price` 필수
- 시장가 매수:
  - `ord_type=price`, `price` 필수, `volume` 생략 또는 null
- 시장가 매도:
  - `ord_type=market`, `volume` 필수, `price` 생략 또는 null

## 7.5 실주문 안전장치 (필수)
- 실행 모드:
  - `PAPER` / `LIVE` 명시
- `LIVE` 주문 전 검증:
  - 최소주문금액/수량 체크
  - 예상 수수료/슬리피지 반영 후 잔액 체크
  - 허용 손실/노출 한도 체크
- 재시도 정책:
  - 주문 생성 API는 멱등키(`identifier`) 사용 권장
  - 네트워크 장애 시 즉시 재주문 금지, 먼저 주문 조회로 체결 여부 확인

## 8. 데이터 품질 게이트
- DQ-1: 일봉 개수 최소치 미달 시 실행 차단
- DQ-2: 최신 bar timestamp 지연 임계치 초과 시 경고
- DQ-3: 결측률/중복률 임계치 초과 시 차단 또는 수동 승인
- DQ-4: 가격 이상치(전일 대비 급변 임계) 검출 시 플래그

## 9. 관측성(Observability)
- 로그 필드 표준:
  - `run_id`, `market`, `from_dt`, `to_dt`, `batch_count`, `http_status`, `retry_count`
- 메트릭:
  - ingest latency, API error rate, cache hit rate, dedup count

## 10. 운영 체크리스트
1. 배치 시작 전 캐시 경로 writable 확인
2. 데이터 수집 후 row count/최신 timestamp 검증
3. 전략 실행 전 DQ 게이트 통과 여부 확인
4. 주문 실행 전 `LIVE` 모드 2인 승인(또는 feature flag) 확인
5. 리포트 생성 후 산출물 저장 경로 확인

## 11. 버전 관리
- 본 정의서 버전: `v1.0`
- 변경 시 PR에서 아래 항목 필수:
  - 변경 endpoint/파라미터
  - backward compatibility 영향
  - 테스트/모니터링 변경사항

## 12. 참조
- 구현 레퍼런스:
  - `src/test/python/v2.py` (`UpbitDataServiceV2`)
  - `src/test/python/v3.py` (`UpbitDataServiceV3`)
  - `src/test/python/v4.py` (`UpbitDataServiceV4`)
  - `src/test/python/v5.py` (`UpbitDataServiceV5`)
- 운영 문서:
  - `docs/backtest_v5_prd.md`
  - `docs/backtest_v5_technical_design.md`
- 공식 문서:
  - 인증: `https://docs.upbit.com/kr/reference/auth`
  - 잔액 조회: `https://docs.upbit.com/kr/reference/get-balance`
  - 주문 생성: `https://docs.upbit.com/kr/reference/주문하기`
  - 개별 주문 조회: `https://docs.upbit.com/kr/reference/개별-주문-조회`
