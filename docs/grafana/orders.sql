WITH evidence AS (
  SELECT e.id, j.*,
         ROW_NUMBER() OVER (PARTITION BY j.event_id ORDER BY e.id DESC) AS latest
  FROM evergreen_execution_event AS e,
       JSON_TABLE(e.payload, '$.detail.chart[*]' COLUMNS (
         event_id VARCHAR(160) PATH '$.event_id',
         kind VARCHAR(40) PATH '$.event',
         schema_version INT PATH '$.schema_version',
         strategy VARCHAR(64) PATH '$.strategy',
         mode VARCHAR(8) PATH '$.mode',
         event_time DOUBLE PATH '$.event_time',
         observed_at VARCHAR(40) PATH '$.observed_at',
         order_sequence INT PATH '$.order_sequence',
         side VARCHAR(8) PATH '$.side',
         fill_price DECIMAL(30,10) PATH '$.fill_price',
         quantity DECIMAL(30,10) PATH '$.quantity',
         fee_amount DECIMAL(30,10) PATH '$.fee_amount',
         terminal_state VARCHAR(8) PATH '$.terminal_state',
         signal_time VARCHAR(40) PATH '$.signal_time',
         filled_at VARCHAR(40) PATH '$.filled_at',
         remaining_btc DECIMAL(30,10) PATH '$.remaining_btc'
       )) AS j
  WHERE e.event = 'order-terminal'
    AND j.schema_version = 1 AND j.strategy = 'breakout-buffer-early3-v1'
    AND j.mode = 'live'
    AND j.event_time BETWEEN $__unixEpochFrom() AND $__unixEpochTo()
)
SELECT event_time AS time, order_sequence AS '주문 순번', side AS '방향',
       fill_price AS '주문 평균 체결가', quantity AS '체결 BTC',
       fee_amount AS '수수료 KRW', terminal_state AS '종료 상태',
       signal_time AS '신호 시각 UTC', filled_at AS '최종 체결 시각 UTC',
       remaining_btc AS '잔여 BTC'
FROM evidence WHERE latest = 1 AND kind = 'order_execution'
ORDER BY time DESC, order_sequence;
