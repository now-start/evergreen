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
        boolean setupBuy,
        boolean setupSell,
        boolean trailStopTriggered,
        String regime,
        double regimeAnchor,
        double regimeUpper,
        double regimeLower,
        double atrTrailStop,
        double posOpen,
        double retOo,
        double equity,
        double equityBh,
        double trade
) {}
