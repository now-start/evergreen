package org.nowstart.evergreen.strategy.core;

import java.util.List;

public record StrategyInput<P extends StrategyParams>(
        List<OhlcvCandle> candles,
        int signalIndex,
        PositionSnapshot position,
        P params
) {
}
