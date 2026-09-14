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
         close_price DECIMAL(30,10) PATH '$.close',
         entry_threshold DECIMAL(30,10) PATH '$.entry_threshold',
         initial_stop DECIMAL(30,10) PATH '$.initial_stop',
         trailing_stop DECIMAL(30,10) PATH '$.trailing_stop',
         channel_exit_threshold DECIMAL(30,10) PATH '$.channel_exit_threshold',
         breach_count INT PATH '$.breach_count',
         rising_grace VARCHAR(5) PATH '$.rising_grace',
         awaiting_reset VARCHAR(5) PATH '$.awaiting_reset',
         exit_due VARCHAR(5) PATH '$.exit_due',
         halted VARCHAR(5) PATH '$.halted',
         position VARCHAR(8) PATH '$.position',
         candidate_signal VARCHAR(8) PATH '$.candidate_signal',
         pre_order_signal VARCHAR(8) PATH '$.pre_order_signal'
       )) AS j
  WHERE e.event = 'strategy-evaluated'
    AND j.schema_version = 1 AND j.strategy = 'breakout-buffer-early3-v1'
    AND j.mode = 'live'
    AND j.event_time BETWEEN $__unixEpochFrom() AND $__unixEpochTo()
)
SELECT event_time AS time, position AS '보유 상태',
       candidate_signal AS '봉 신호', pre_order_signal AS '최종 주문 전 신호',
       breach_count AS '연속 이탈', rising_grace AS '상승 유예',
       awaiting_reset AS '재진입 대기', exit_due AS '예방 청산',
       halted AS '거래 중단', observed_at AS '관측 시각 UTC'
FROM evidence WHERE latest = 1 AND kind = 'strategy_bar'
ORDER BY time DESC;
