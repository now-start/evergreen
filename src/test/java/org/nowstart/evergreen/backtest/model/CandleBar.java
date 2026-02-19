package org.nowstart.evergreen.backtest.model;

import java.time.Instant;

public record CandleBar(
        Instant timestamp,
        double open,
        double high,
        double low,
        double close,
        double volume
) {}
