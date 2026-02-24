# Grafana Trading Dashboard Logging Guide

## Dashboard JSON
- File: `docs/grafana_trading_dashboard.json`
- Data source: Loki (`${DS_LOKI}`)
- Dashboard UID: `evergreen-trading-plot`
- `docs/grafana_dashboard_sample_full_top6cards.svg` is a visual mock-up, not a rendered export.
- `docs/img.png`, `docs/img_1.png` are live Grafana captures from Loki data.

## Required Log Events
- `event=trade_marker`
  - fields: `mode`, `clientOrderId`, `symbol`, `side`, `tradePrice`, `tradeQty`
- `event=position_snapshot`
    - fields: `market`, `total_qty`, `managed_qty`, `external_qty`, `sellable_qty`, `drift_qty`, `drift_detected`
- `event=position_sync`
    - fields: `market`, `asset`, `qty`, `avg_price`, `state`
- `event=external_position_drift`
    - fields: `market`, `total_qty`, `managed_qty`, `external_qty`, `drift_qty`
- `event=external_order_guard`
    - fields: `market`, `blocked`, `reason`, `open_order_count`
- `event=candle_signal`
    - fields: `market`, `ts`, `close`, `live_price`, `has_position`, `position_qty`, `position_avg_price`,
      `total_qty`, `unrealized_return_pct`, `realized_pnl_krw`, `realized_return_pct`, `max_drawdown_pct`,
      `trade_count`, `trade_win_rate_pct`, `trade_avg_win_pct`, `trade_avg_loss_pct`, `trade_rr_ratio`,
      `trade_expectancy_pct`, `buy_signal`, `sell_signal`, `signal_reason`, `diagnostics`, `diagnostics_schema`
  - non-finite numeric metrics are normalized to `0.0` in logs for LogQL compatibility
- `event=strategy_diagnostic`
    - fields: `market`, `strategy_version`, `ts`, `key`, `label`, `type`, `unit`, `value`, `buy_signal`, `sell_signal`,
      `signal_reason`
    - strategy-specific numeric/boolean 보조지표가 key-value 시계열로 출력됨
- `event=trade_execution`
  - fields: `market`, `side`, `signal_ts`, `signal_close`, `client_order_id`, `order_status`, `mode`, `executed_price`, `executed_volume`, `fee_amount`, `slippage_pct`, `slippage_bps`
- `event=http_request_completed`
  - fields: `requestId`, `method`, `path`, `status`, `durationMs`
- `event=api_error`
  - fields: `type`, `status`, `code`, `method`, `path`, `message/detail`

## Trading Scheduler (auto order execution)
Scheduler is always enabled.

Enable with environment variables:

```bash
TRADING_SCHEDULER_MODE=LIVE
TRADING_SCHEDULER_MARKETS=KRW-BTC,KRW-ETH
TRADING_SCHEDULER_INTERVAL=30s
TRADING_SCHEDULER_STRATEGY_VERSION=v5-live
TRADING_SCHEDULER_CANDLE_COUNT=400
TRADING_SCHEDULER_CLOSED_CANDLE_ONLY=true
TRADING_SCHEDULER_REGIME_EMA_LEN=120
TRADING_SCHEDULER_ATR_PERIOD=18
TRADING_SCHEDULER_ATR_MULT_LOW_VOL=2.0
TRADING_SCHEDULER_ATR_MULT_HIGH_VOL=3.0
TRADING_SCHEDULER_VOL_REGIME_LOOKBACK=40
TRADING_SCHEDULER_VOL_REGIME_THRESHOLD=0.6
TRADING_SCHEDULER_REGIME_BAND=0.01
TRADING_SCHEDULER_SIGNAL_ORDER_NOTIONAL=100000
```

## LogQL (Plot)
- Price line (`live_price` preferred, fallback to daily `close`)
```logql
avg by (market) (avg_over_time({service_name="evergreen"} |= "event=ticker_price" | logfmt | label_format market="{{.market}}" | live_price!="NaN" | unwrap live_price | __error__="" [$__interval]))
or avg by (market) (avg_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | live_price!="NaN" | unwrap live_price | __error__="" [$__interval]))
or avg by (market) (avg_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | unwrap close | __error__="" [$__interval]))
```

- Strategy diagnostics (all indicators, auto)
```logql
avg by (market, label) (avg_over_time({service_name="evergreen"} |= "event=strategy_diagnostic" | logfmt | label_format market="{{.market}}" | label_format label="{{.label}}" | type=~"NUMBER|BOOLEAN" | unwrap value | __error__="" [$__interval]))
```
- Buy/Sell markers (point series)
```logql
max by (market) (max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | buy_signal="true" | unwrap close | __error__="" [$__interval]))
max by (market) (max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | sell_signal="true" | unwrap close | __error__="" [$__interval]))
```
- Unrealized return (%)
```logql
max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | unrealized_return_pct!="NaN" | unwrap unrealized_return_pct | __error__="" [$__interval])
```
- Signal reason count (1h)
```logql
sum by (signal_reason) (count_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | signal_reason!="" [1h]))
```

- Realized PnL / MDD / WinRate / Slippage
```logql
max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | realized_pnl_krw!="NaN" | unwrap realized_pnl_krw | __error__="" [$__interval]) or (max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | unwrap trade_count | __error__="" [$__interval]) * 0)
max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | max_drawdown_pct!="NaN" | unwrap max_drawdown_pct | __error__="" [$__interval]) or (max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | label_format market="{{.market}}" | unwrap trade_count | __error__="" [$__interval]) * 0)
max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | trade_win_rate_pct!="NaN" | unwrap trade_win_rate_pct | __error__="" [$__interval]) or (max_over_time({service_name="evergreen"} |= "event=candle_signal" | logfmt | unwrap trade_count | __error__="" [$__interval]) * 0)
max_over_time({service_name="evergreen"} |= "event=trade_execution" | logfmt | slippage_bps!="NaN" | unwrap slippage_bps | __error__="" [$__interval])
```

- Position composition (total / managed / external / sellable)

```logql
max by (market) (max_over_time({service_name="evergreen"} |= "event=position_snapshot" | logfmt | label_format market="{{.market}}" | unwrap total_qty | __error__="" [$__interval]))
max by (market) (max_over_time({service_name="evergreen"} |= "event=position_snapshot" | logfmt | label_format market="{{.market}}" | unwrap managed_qty | __error__="" [$__interval]))
max by (market) (max_over_time({service_name="evergreen"} |= "event=position_snapshot" | logfmt | label_format market="{{.market}}" | unwrap external_qty | __error__="" [$__interval]))
max by (market) (max_over_time({service_name="evergreen"} |= "event=position_snapshot" | logfmt | label_format market="{{.market}}" | unwrap sellable_qty | __error__="" [$__interval]))
```

- External fill detection / guard count

```logql
sum(count_over_time({service_name="evergreen"} |= "event=external_position_drift" [10m]))
sum(count_over_time({service_name="evergreen"} |= "event=external_order_guard" | logfmt | blocked="true" [10m]))
```

- Guard reason topN (10m)

```logql
topk(5, sum by (reason) (count_over_time({service_name="evergreen"} |= "event=external_order_guard" | logfmt [10m])))
```

## Notes
- `requestId` is injected into MDC and response header `X-Request-Id`.
- Health checks (`/actuator/health`) are excluded from request-complete logs.
- Keep Loki labels minimal; parse business fields from log body (`logfmt`) in panel queries.
- `event=candle_signal` is emitted every scheduler cycle per market.
- `event=position_snapshot` is emitted every position sync cycle per market.
- `event=external_position_drift` is emitted when account position drift is detected versus previously managed
  quantity (external fill detection signal in LIVE flow).
- `event=external_order_guard` is emitted when external open-order guard blocks scheduler execution in LIVE flow.
- Single-position integration mode: `managed_qty` mirrors `total_qty`, so `external_qty` is expected to be `0` in normal
  operation.
- Logs panel uses `line_format` summary output to keep payload compact.
- `close` is based on daily candle close, so short ranges can appear flat; use `live_price` for intraday movement in time-series panels.
- In panel `가격 + 모든 보조지표(자동) + 매수/매도 포인트`, price is on left Y-axis and diagnostics are on right Y-axis.
- For heavy traffic windows, keep metric queries aggregated by market (`... by (market)`) and add `| __error__=""` right after `unwrap` to avoid pipeline parse failures.
