package org.nowstart.evergreen.service.strategy.core;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.List;
import org.nowstart.evergreen.data.type.MarketRegime;

public final class StrategyMath {

    private StrategyMath() {
    }

    public static double[] close(List<OhlcvCandle> candles) {
        return candles.stream().mapToDouble(OhlcvCandle::close).toArray();
    }

    public static double[] high(List<OhlcvCandle> candles) {
        return candles.stream().mapToDouble(OhlcvCandle::high).toArray();
    }

    public static double[] low(List<OhlcvCandle> candles) {
        return candles.stream().mapToDouble(OhlcvCandle::low).toArray();
    }

    public static double[] movingAverage(double[] values, int length) {
        int n = values.length;
        double[] out = fillNaN(n);
        if (length <= 0 || n < length) {
            return out;
        }

        double windowSum = 0.0;
        for (int i = 0; i < n; i++) {
            windowSum += values[i];
            if (i >= length) {
                windowSum -= values[i - length];
            }
            if (i >= length - 1) {
                out[i] = windowSum / length;
            }
        }
        return out;
    }

    public static double[] exponentialMovingAverage(double[] values, int length) {
        int n = values.length;
        double[] ema = fillNaN(n);
        if (length <= 0 || n < length) {
            return ema;
        }

        double seed = 0.0;
        for (int i = 0; i < length; i++) {
            seed += values[i];
        }
        ema[length - 1] = seed / length;

        double alpha = 2.0 / (length + 1.0);
        for (int i = length; i < n; i++) {
            ema[i] = (alpha * values[i]) + ((1.0 - alpha) * ema[i - 1]);
        }
        return ema;
    }

    public static double[] wilderRsi(double[] close, int period) {
        int n = close.length;
        double[] rsi = fillNaN(n);
        if (period <= 0 || n <= period) {
            return rsi;
        }

        double gainSum = 0.0;
        double lossSum = 0.0;
        for (int i = 1; i <= period; i++) {
            double diff = close[i] - close[i - 1];
            if (diff > 0.0) {
                gainSum += diff;
            } else {
                lossSum += -diff;
            }
        }

        double avgGain = gainSum / period;
        double avgLoss = lossSum / period;
        rsi[period] = avgLoss == 0.0 ? Double.NaN : 100.0 - (100.0 / (1.0 + (avgGain / avgLoss)));

        for (int i = period + 1; i < n; i++) {
            double diff = close[i] - close[i - 1];
            double gain = Math.max(0.0, diff);
            double loss = Math.max(0.0, -diff);
            avgGain = ((avgGain * (period - 1)) + gain) / period;
            avgLoss = ((avgLoss * (period - 1)) + loss) / period;
            rsi[i] = avgLoss == 0.0 ? Double.NaN : 100.0 - (100.0 / (1.0 + (avgGain / avgLoss)));
        }
        return rsi;
    }

    public static double[] wilderAtr(double[] high, double[] low, double[] close, int period) {
        int n = close.length;
        double[] atr = fillNaN(n);
        if (period <= 0 || n < period) {
            return atr;
        }

        double[] tr = new double[n];
        tr[0] = high[0] - low[0];
        for (int i = 1; i < n; i++) {
            double highLow = high[i] - low[i];
            double highPrevClose = Math.abs(high[i] - close[i - 1]);
            double lowPrevClose = Math.abs(low[i] - close[i - 1]);
            tr[i] = Math.max(highLow, Math.max(highPrevClose, lowPrevClose));
        }

        double total = 0.0;
        for (int i = 0; i < period; i++) {
            total += tr[i];
        }

        int first = period - 1;
        atr[first] = total / period;
        for (int i = period; i < n; i++) {
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period;
        }
        return atr;
    }

    public static MarketRegime[] resolveRegimes(double[] close, double[] anchor, double regimeBand) {
        int n = close.length;
        MarketRegime[] regimes = new MarketRegime[n];

        for (int i = 0; i < n; i++) {
            if (!Double.isFinite(anchor[i])) {
                regimes[i] = MarketRegime.UNKNOWN;
                continue;
            }

            double upper = anchor[i] * (1.0 + regimeBand);
            double lower = anchor[i] * (1.0 - regimeBand);
            MarketRegime previous = i == 0 ? MarketRegime.UNKNOWN : regimes[i - 1];

            if (close[i] > upper) {
                regimes[i] = MarketRegime.BULL;
            } else if (close[i] < lower) {
                regimes[i] = MarketRegime.BEAR;
            } else if (previous != MarketRegime.UNKNOWN) {
                regimes[i] = previous;
            } else if (close[i] > anchor[i]) {
                regimes[i] = MarketRegime.BULL;
            } else if (close[i] < anchor[i]) {
                regimes[i] = MarketRegime.BEAR;
            } else {
                regimes[i] = MarketRegime.UNKNOWN;
            }
        }

        return regimes;
    }

    public static VolatilityState resolveVolatilityStates(
            double[] atr,
            double[] close,
            int lookback,
            double threshold
    ) {
        int n = close.length;
        double[] ratio = fillNaN(n);
        double[] percentile = fillNaN(n);
        boolean[] high = new boolean[n];

        for (int i = 0; i < n; i++) {
            if (Double.isFinite(atr[i]) && Double.isFinite(close[i]) && close[i] > 0.0) {
                ratio[i] = atr[i] / close[i];
            }

            if (!Double.isFinite(ratio[i])) {
                continue;
            }

            int start = Math.max(0, i - lookback + 1);
            int count = 0;
            int belowOrEqual = 0;
            for (int j = start; j <= i; j++) {
                if (!Double.isFinite(ratio[j])) {
                    continue;
                }
                count++;
                if (ratio[j] <= ratio[i]) {
                    belowOrEqual++;
                }
            }

            if (count == 0) {
                continue;
            }

            percentile[i] = belowOrEqual / (double) count;
            high[i] = percentile[i] >= threshold;
        }

        return new VolatilityState(ratio, percentile, high);
    }

    public static TrailStopEvaluation evaluateTrailStop(
            List<OhlcvCandle> candles,
            int signalIndex,
            double[] atr,
            double atrMultiplier,
            PositionSnapshot position
    ) {
        if (position == null || !position.hasPosition() || atrMultiplier <= 0.0 || !Double.isFinite(atr[signalIndex])) {
            return new TrailStopEvaluation(Double.NaN, false);
        }

        double highestCloseSinceEntry = resolveHighestCloseSinceEntry(candles, signalIndex, position);
        if (!Double.isFinite(highestCloseSinceEntry)) {
            return new TrailStopEvaluation(Double.NaN, false);
        }

        double stop = highestCloseSinceEntry - (atrMultiplier * atr[signalIndex]);
        double currentClose = candles.get(signalIndex).close();
        return new TrailStopEvaluation(stop, currentClose <= stop);
    }

    public static SignalAction resolveAction(boolean shouldBuy, boolean shouldSell) {
        if (shouldBuy && shouldSell) {
            throw new IllegalArgumentException("shouldBuy and shouldSell cannot both be true");
        }
        if (shouldBuy) {
            return SignalAction.BUY;
        }
        if (shouldSell) {
            return SignalAction.SELL;
        }
        return SignalAction.HOLD;
    }

    public static double targetRatio(SignalAction action, double currentRatio) {
        if (action == SignalAction.BUY) {
            return 1.0;
        }
        if (action == SignalAction.SELL) {
            return 0.0;
        }
        return currentRatio;
    }

    public static double currentPositionRatio(PositionSnapshot position) {
        if (position == null || !Double.isFinite(position.positionRatio())) {
            return 0.0;
        }
        return Math.max(0.0, position.positionRatio());
    }

    public static void validateInput(StrategyInput<?> input) {
        if (input == null || input.candles() == null || input.params() == null) {
            throw new IllegalArgumentException("input, candles, and params are required");
        }
        int size = input.candles().size();
        if (input.signalIndex() < 1 || input.signalIndex() >= size) {
            throw new IllegalArgumentException("signalIndex must be in [1, candles.size()-1]");
        }
    }

    public static double[] fillNaN(int size) {
        double[] values = new double[size];
        Arrays.fill(values, Double.NaN);
        return values;
    }

    private static double resolveHighestCloseSinceEntry(
            List<OhlcvCandle> candles,
            int signalIndex,
            PositionSnapshot position
    ) {
        int startIndex = 0;

        if (position.updatedAt() != null) {
            LocalDate positionDate = position.updatedAt().atOffset(ZoneOffset.UTC).toLocalDate();
            boolean found = false;
            for (int i = 0; i <= signalIndex; i++) {
                LocalDate candleDate = candles.get(i).timestamp().atOffset(ZoneOffset.UTC).toLocalDate();
                if (!candleDate.isBefore(positionDate)) {
                    startIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                startIndex = signalIndex;
            }
        }

        double highest = Double.NaN;
        for (int i = startIndex; i <= signalIndex; i++) {
            double candleClose = candles.get(i).close();
            if (!Double.isFinite(highest) || candleClose > highest) {
                highest = candleClose;
            }
        }

        return highest;
    }

    public record VolatilityState(
            double[] atrPriceRatio,
            double[] percentile,
            boolean[] isHigh
    ) {
    }

    public record TrailStopEvaluation(
            double stopPrice,
            boolean triggered
    ) {
    }
}
