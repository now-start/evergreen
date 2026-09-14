# early3 차트 관측 준비

전략·실거래 활성화·주문 규칙은 변경하지 않는다. 신규 테이블·패키지·Config Server 설정도 없다.
이 디렉터리는 **가져오기용 템플릿**이며 운영 Grafana에 자동 배포하지 않는다.

## 추가 기록

| 이벤트 | 기록 시점 | 주요 값 |
| --- | --- | --- |
| `strategy_bar` | early3가 새 확정 1시간봉을 평가하고 DB 저장에 성공한 뒤 | OHLCV, 진입선, 초기 손절선, 추적 손절선·활성 여부, 96시간 청산선, 48시간 재진입 기준선, 보유 상태, 연속 이탈 수, 상승 유예, 재진입 대기, 예방 청산, 청산 예약, 거래 중단 |
| `order_execution` | 종료 주문의 체결 내역과 실제 잔고 대사 및 DB 저장이 완료된 뒤 | 주문 순번, 방향, 실제 평균 체결가, 체결 BTC, 총 수수료 KRW, 최초·최종 체결 시각, 신호 시각, 잔여 BTC |

공통 필드: `schema_version=1`, `event_id`, `strategy`, `market=KRW-BTC`, `mode=live`,
`event_time`(UTC Unix 초), `observed_at`(평가 사이클/대사 관측 시각 UTC).
가격·수량은 Decimal 정밀도를 보존하는 문자열이며 SQL에서 숫자로 변환한다.
초기 손절선·청산선은 미보유 시 `null`, 추적선은 미활성 시 `null`이다. 0으로 채우지 않는다.

- `candidate_signal`: 해당 봉의 전략 내부 신호. `null`은 신호 없음이다.
- `pre_order_signal`: 최신 평가 봉에만 기록하는 엔진의 위험 중단 반영 후 신호.
  이후 호가·수량·위험 예산·유효 시간 검사에서 거절될 수 있다. **매수/매도 체결이 아니다.**
- 따라잡기 구간의 이전 봉 신호는 당시 실시간 주문이 있었음을 의미하지 않는다.
- 실제 매수/매도 점은 `order_execution`만 사용한다. 주문 접수, 대기, 미체결 취소에는 점이 없다.
- 점 1개는 **개별 체결이 아니라 종료 주문 전체의 VWAP**이다. 마지막 실제 체결 시각에 표시한다.
  일부 체결 후 취소된 주문도 실제 체결분만 기록하며, 남은 포지션의 후속 매도는 별도 주문 순번이다.
  총 수수료를 개별 체결별 수수료로 임의 분배하지 않는다.
- 구형 전략 주문에서 체결 시각이 없으면 `event_time=null`로 남긴다. 관측 시각으로 대체하지 않으며
  가격 차트/주문 SQL에서 제외된다. 해당 기록은 Loki 또는 DB 원문에서 확인한다.

## 저장·전송

기존 `evergreen_execution_event`의 `payload.detail.chart` 배열에 스냅샷을 보관한다.
`event='strategy-evaluated'`는 시간봉 목록, `event='order-terminal'`은 체결 요약 목록이다.
주문 원본 detail의 기존 필드는 보존한다. 상태와 관측 자료는 기존 `Store.save` 트랜잭션으로 함께 저장된다.
저장이 실패하면 해당 차트 로그를 내보내지 않는다.

동일 JSON을 `evergreen.trading.chart` INFO 로그 본문으로 보낸다. 기존 OTel → Loki 경로를 사용한다.
DB 커밋 직후 프로세스가 죽거나 로그 전송이 실패하면 Loki에만 누락될 수 있으므로 **차트의 기준은 DB**다.
기존 워커 커서와 주문 상태로 재평가/재대사 중복을 억제하고, 조회는 `event_id` 기준 마지막 기록을 선택한다.
ID의 범위는 단일 실행 DB/계좌다. 여러 DB의 데이터를 병합할 때는 별도 배포 구분자가 필요하다.

로그에는 API 키·계좌 identity·거래소 주문 UUID·인증 헤더·원본 응답을 넣지 않는다.
체결 금액/수량 역시 민감한 거래 정보이므로 Grafana/Loki 접근 권한과 보존 기간을 제한한다.
`event_id`, 가격, 시각, 주문 순번은 **Loki 인덱스 라벨에 추가하지 않는다.**

## Grafana 연결 순서

1. 운영 DB에서 `SELECT VERSION();`으로 **MariaDB 10.11 이상**을 확인한다 (이번 SQL 검증 버전은 10.11).
   `JSON_TABLE` 자체는 10.6부터 지원하지만 이전 버전은 JSON null 처리까지 별도 검증이 필요하다.
2. Grafana의 MySQL 데이터 소스를 기존 Evergreen DB에 연결한다. 애플리케이션의 쓰기 계정을
   재사용하지 말고 `evergreen_execution_event`에만 SELECT 가능한 별도 계정을 사용한다.
   이 테이블에는 기존 계좌/주문 상태도 있으므로 Grafana 편집·Explore 권한 역시 신뢰된 운영자로 제한한다.
3. `early3-dashboard.json`을 Dashboards → Import로 가져와 MariaDB와 Loki 데이터 소스를 선택한다.
   데이터 소스 UID나 비밀번호를 JSON에 하드코딩하지 않는다. 시간대는 `Asia/Seoul`이다.
4. 애플리케이션 변경 배포 후 다음 확정 시간봉부터 데이터가 쌓이는지 확인한다.
   보유하지 않을 때 손절선이 비어 있는 것은 정상이며, 매수 신호만 있고 점이 없는 것도 가능하다.
5. 첫 검증은 짧은 조회 범위에서 실행하고 Query inspector로 행 수·응답 시간·오류를 확인한다.
   이 템플릿은 기존 Text JSON 이벤트를 해석하므로 이력이 커지면 전체 스캔 비용이 증가한다.
   운영 자동 새로고침을 켜기 전 실행 계획/부하를 확인하고, 필요하면 조회용 저장소나 인덱스를 별도 설계한다.
   템플릿의 자동 새로고침은 기본으로 꺼져 있다.

템플릿 패널:

- 종가·진입선·초기 손절선·추적 손절선·96시간 청산선 + 실제 매수(초록)/매도(빨강) 점
- 시간봉별 판단 근거 표
- 종료 주문 체결·수수료 표
- 워커 heartbeat·중단·주문 거절 로그

SQL 원본은 `prices.sql`, `executions.sql`, `decisions.sql`, `orders.sql`이며 대시보드에 동일하게 포함한다.
시간축은 DB 행의 저장 시각이 아니라 JSON의 `event_time`이다. SQL은 미래에 늦게 저장된 과거 봉도
그 봉의 시각으로 조회한다. 체결점을 시간 버킷 평균으로 뭉개거나 신호 시각으로 옮기지 않는다.
선은 누락 구간을 연결하지 않도록 설정하고, 미활성 값은 null 그대로 유지한다.

**관측 범위:** early3의 실제 평가 구간만 기록한다. 기존 전략 `breakout-v1`의 기준선,
최초 실행 이전 과거 차트, 워커 중단/신호 시간창 외에 평가하지 않은 봉을 소급 생성하지 않는다.
중단 중에도 끊김 없는 전체 시장 가격 차트가 필요하면 독립 시세 수집이 후속 작업이다.
계좌 수익률/MDD 그래프, 모델 국면 확률, 알림 규칙은 이번 템플릿에 포함하지 않는다.

## Loki 확인 쿼리

OTel 로그 본문이 JSON인 현재 경로:

```logql
{service_name="evergreen"} | json | __error__="" | event="strategy_bar"
```

```logql
{service_name="evergreen"} | json | __error__="" | event="order_execution"
```

컨테이너 stdout을 별도로 수집해 날짜/로거 접두사가 들어간 경로라면 먼저 JSON 부분을 꺼낸다.
동일 로그를 OTel과 stdout에서 중복 수집하지 않는지 확인한다.

```logql
{service_name="evergreen"} | pattern "<_> - <payload>" | line_format "{{.payload}}" | json | __error__="" | event="strategy_bar"
```

기존 `worker_heartbeat`, `worker_failed`, `worker_skipped`, `trading_rejected`는 유지한다.
`/actuator/health` 200이나 최근 차트 한 점만으로 워커가 현재 정상이라고 판단하지 않는다.
수집기 지연과 워커 장애를 구별하기 위해 DB 저장 시각, `observed_at`, Loki 수집 시각을 비교한다.

## 검증 기준

테스트는 가짜 거래소와 일회용 로컬 MariaDB만 사용한다. 신규/복구 봉, 중복 방지, 미체결 취소,
부분 체결, 저장 실패 시 로그 억제, OTel JSON 전달, 대시보드 SQL 계약을 확인한다.
운영 가져오기·실계좌 체결·운영 부하는 별도 검증이며 템플릿 파일 생성만으로 완료로 간주하지 않는다.

공식 문서:

- [Grafana MySQL 시간축/SQL 매크로](https://grafana.com/docs/grafana/latest/datasources/mysql/query-editor/)
- [MariaDB JSON_TABLE: 10.6부터 지원](https://mariadb.com/docs/server/reference/sql-functions/special-functions/json-functions/json_table)
- [Grafana Time series](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/time-series/)
- [Loki 로그 파서](https://grafana.com/docs/loki/latest/query/log_queries/)
