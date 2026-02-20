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
TRADING_SCHEDULER_REGIME_EMA_LEN=200
TRADING_SCHEDULER_ATR_PERIOD=14
TRADING_SCHEDULER_ATR_MULT_LOW_VOL=2.0
TRADING_SCHEDULER_ATR_MULT_HIGH_VOL=4.0
TRADING_SCHEDULER_VOL_REGIME_LOOKBACK=30
TRADING_SCHEDULER_VOL_REGIME_THRESHOLD=0.7
TRADING_SCHEDULER_REGIME_BAND=0.02
TRADING_SCHEDULER_SIGNAL_ORDER_NOTIONAL=100000
```

## Notes
- `requestId` is injected into MDC and response header `X-Request-Id`.
- Health checks (`/actuator/health`) are excluded from request-complete logs.
- Keep Loki labels minimal; parse business fields from log body (`logfmt`) in panel queries.
