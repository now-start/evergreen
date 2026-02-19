package org.nowstart.evergreen.backtest.model;

import java.time.Instant;

public record BacktestRow(
        Instant timestamp,
        double open,
        double close,
        double ma,
        double rsi,
        boolean buySignal,
        boolean sellSignal,
        int posOpen,
        double retOo,
        double equity,
        double equityBh,
        double trade
) {}
