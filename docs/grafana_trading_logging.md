# Grafana Trading Dashboard Logging Guide

## Dashboard JSON
- File: `docs/grafana_trading_dashboard.json`
- Data source: Loki (`${DS_LOKI}`)
- Dashboard UID: `evergreen-trading-plot`

## Required Log Events
- `event=trade_marker`
  - fields: `mode`, `clientOrderId`, `symbol`, `side`, `tradePrice`, `tradeQty`
- `event=position_snapshot`
  - fields: `mode`, `symbol`, `qty`, `avgPrice`, `state`
- `event=candle_signal_v5`
  - fields: `market`, `ts`, `close`, `regime`, `prev_regime`, `regime_anchor`, `regime_upper`, `regime_lower`, `atr`, `atr_trail_multiplier`, `atr_trail_stop`, `has_position`, `position_qty`, `position_avg_price`, `unrealized_return_pct`, `realized_pnl_krw`, `realized_return_pct`, `max_drawdown_pct`, `trade_count`, `trade_win_rate_pct`, `trade_avg_win_pct`, `trade_avg_loss_pct`, `trade_rr_ratio`, `trade_expectancy_pct`, `signal_quality_1d_avg_pct`, `signal_quality_3d_avg_pct`, `signal_quality_7d_avg_pct`, `volatility_is_high`, `atr_price_ratio`, `vol_percentile`, `buy_signal`, `sell_signal`, `signal_reason`
- `event=trade_execution_v1`
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

## LogQL (V5 Plot)
- Price line
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap close [1m])
```
- Regime band upper/lower
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap regime_upper [1m])
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap regime_lower [1m])
```
- ATR trailing stop line
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap atr_trail_stop [1m])
```
- Buy/Sell markers (point series)
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" |= "buy_signal=true" | logfmt | label_format market="{{.market}}" | unwrap close [1m])
max_over_time({app="evergreen"} |= "event=candle_signal_v5" |= "sell_signal=true" | logfmt | label_format market="{{.market}}" | unwrap close [1m])
```
- Unrealized return (%)
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap unrealized_return_pct [1m])
```
- Volatility percentile
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap vol_percentile [1m])
```
- Signal reason count (1h)
```logql
sum(count_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | signal_reason="BUY_REGIME_TRANSITION" [1h]))
sum(count_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | signal_reason="SELL_REGIME_TRANSITION" [1h]))
sum(count_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | signal_reason="SELL_TRAIL_STOP" [1h]))
```
- Realized PnL / MDD / WinRate / Signal quality / Slippage
```logql
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap realized_pnl_krw [1m])
min_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | label_format market="{{.market}}" | unwrap max_drawdown_pct [30d])
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | unwrap trade_win_rate_pct [1m])
max_over_time({app="evergreen"} |= "event=candle_signal_v5" | logfmt | unwrap signal_quality_7d_avg_pct [1m])
max_over_time({app="evergreen"} |= "event=trade_execution_v1" | logfmt | unwrap slippage_bps [1m])
```

## Notes
- `requestId` is injected into MDC and response header `X-Request-Id`.
- Health checks (`/actuator/health`) are excluded from request-complete logs.
- Keep Loki labels minimal; parse business fields from log body (`logfmt`) in panel queries.
