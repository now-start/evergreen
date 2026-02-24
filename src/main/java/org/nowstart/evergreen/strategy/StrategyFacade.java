package org.nowstart.evergreen.strategy;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.nowstart.evergreen.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.strategy.core.StrategyInput;
import org.nowstart.evergreen.strategy.v5.V5StrategyEngine;
import org.nowstart.evergreen.strategy.v5.V5StrategyOverrides;

/**
 * Static bridge for JPype callers.
 */
public final class StrategyFacade {

    private static final V5StrategyEngine V5_ENGINE = new V5StrategyEngine();

    private StrategyFacade() {
    }

    public static StrategyEvaluation runV5(
            long[] timestampEpochMillis,
            double[] open,
            double[] high,
            double[] low,
            double[] close,
            double[] volume,
            int signalIndex,
            double positionQty,
            double positionAvgPrice,
            long positionUpdatedAtEpochMillis,
            V5StrategyOverrides params
    ) {
        List<OhlcvCandle> candles = toCandles(timestampEpochMillis, open, high, low, close, volume);
        Instant updatedAt = positionUpdatedAtEpochMillis > 0L
                ? Instant.ofEpochMilli(positionUpdatedAtEpochMillis)
                : null;
        PositionSnapshot position = new PositionSnapshot(positionQty, positionAvgPrice, updatedAt);
        return V5_ENGINE.evaluate(new StrategyInput<>(candles, signalIndex, position, params));
    }

    public static StrategyEvaluation runV5(
            long[] timestampEpochMillis,
            double[] open,
            double[] high,
            double[] low,
            double[] close,
            double[] volume,
            int signalIndex,
            V5StrategyOverrides params
    ) {
        return runV5(
                timestampEpochMillis,
                open,
                high,
                low,
                close,
                volume,
                signalIndex,
                0.0,
                0.0,
                0L,
                params
        );
    }

    private static List<OhlcvCandle> toCandles(
            long[] timestampEpochMillis,
            double[] open,
            double[] high,
            double[] low,
            double[] close,
            double[] volume
    ) {
        if (timestampEpochMillis == null || high == null || low == null || close == null) {
            throw new IllegalArgumentException("timestamp/high/low/close arrays are required");
        }

        int n = close.length;
        if (n == 0) {
            return List.of();
        }

        if (timestampEpochMillis.length != n || high.length != n || low.length != n) {
            throw new IllegalArgumentException("all required arrays must have identical lengths");
        }

        double[] resolvedOpen = open == null ? close : open;
        double[] resolvedVolume = volume == null ? new double[n] : volume;
        if (resolvedOpen.length != n || resolvedVolume.length != n) {
            throw new IllegalArgumentException("open and volume arrays must match close length");
        }

        List<OhlcvCandle> candles = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            candles.add(new OhlcvCandle(
                    Instant.ofEpochMilli(timestampEpochMillis[i]),
                    resolvedOpen[i],
                    high[i],
                    low[i],
                    close[i],
                    resolvedVolume[i]
            ));
        }
        return candles;
    }
}
