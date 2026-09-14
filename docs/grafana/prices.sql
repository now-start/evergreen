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
SELECT event_time AS time, close_price AS '종가', entry_threshold AS '진입선',
       initial_stop AS '초기 손절선', trailing_stop AS '추적 손절선',
       channel_exit_threshold AS '96시간 청산선'
FROM evidence WHERE latest = 1 AND kind = 'strategy_bar'
ORDER BY time;
