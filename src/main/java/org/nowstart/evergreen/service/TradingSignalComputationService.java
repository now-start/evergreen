package org.nowstart.evergreen.service;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.dto.TradingSignalTrailStopResult;
import org.nowstart.evergreen.data.dto.TradingSignalVolatilityResult;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.springframework.stereotype.Service;

@Service
public class TradingSignalComputationService {

    public double[] exponentialMovingAverage(double[] values, int length) {
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

    public double[] wilderAtr(double[] high, double[] low, double[] close, int period) {
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

    public MarketRegime[] resolveRegimes(double[] close, double[] anchor, double regimeBand) {
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

    public TradingSignalVolatilityResult resolveVolatilityStates(double[] atr, double[] close, int lookback, double threshold) {
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

        return new TradingSignalVolatilityResult(ratio, percentile, high);
    }

    public TradingSignalTrailStopResult evaluateTrailStop(
            List<TradingDayCandleDto> candles,
            int signalIndex,
            double[] atr,
            double atrMultiplier,
            TradingPosition position,
            boolean hasPosition
    ) {
        if (!hasPosition || atrMultiplier <= 0.0 || !Double.isFinite(atr[signalIndex])) {
            return new TradingSignalTrailStopResult(Double.NaN, false);
        }

        double highestCloseSinceEntry = resolveHighestCloseSinceEntry(candles, signalIndex, position);
        if (!Double.isFinite(highestCloseSinceEntry)) {
            return new TradingSignalTrailStopResult(Double.NaN, false);
        }

        double stop = highestCloseSinceEntry - (atrMultiplier * atr[signalIndex]);
        double currentClose = candles.get(signalIndex).close().doubleValue();
        return new TradingSignalTrailStopResult(stop, currentClose <= stop);
    }

    public String resolveSignalReason(
            boolean buySignal,
            boolean sellSignal,
            boolean baseBuy,
            boolean baseSell,
            boolean trailStopTriggered
    ) {
        if (buySignal) {
            return "BUY_REGIME_TRANSITION";
        }
        if (sellSignal && baseSell && trailStopTriggered) {
            return "SELL_REGIME_AND_TRAIL_STOP";
        }
        if (sellSignal && trailStopTriggered) {
            return "SELL_TRAIL_STOP";
        }
        if (sellSignal) {
            return "SELL_REGIME_TRANSITION";
        }
        if (baseBuy) {
            return "SETUP_BUY";
        }
        if (baseSell) {
            return "SETUP_SELL";
        }
        return "NONE";
    }

    public TradingSignalQualityStats resolveSignalQualityStats(double[] close, MarketRegime[] regimes, int signalIndex) {
        double sum1d = 0.0;
        double sum3d = 0.0;
        double sum7d = 0.0;
        int count1d = 0;
        int count3d = 0;
        int count7d = 0;

        for (int i = 1; i <= signalIndex; i++) {
            MarketRegime prev = regimes[i - 1];
            MarketRegime current = regimes[i];
            boolean buy = prev == MarketRegime.BEAR && current == MarketRegime.BULL;
            if (!buy || !Double.isFinite(close[i]) || close[i] <= 0.0) {
                continue;
            }

            if (i + 1 <= signalIndex && Double.isFinite(close[i + 1])) {
                sum1d += ((close[i + 1] / close[i]) - 1.0) * 100.0;
                count1d++;
            }
            if (i + 3 <= signalIndex && Double.isFinite(close[i + 3])) {
                sum3d += ((close[i + 3] / close[i]) - 1.0) * 100.0;
                count3d++;
            }
            if (i + 7 <= signalIndex && Double.isFinite(close[i + 7])) {
                sum7d += ((close[i + 7] / close[i]) - 1.0) * 100.0;
                count7d++;
            }
        }

        return new TradingSignalQualityStats(
                count1d == 0 ? Double.NaN : sum1d / count1d,
                count3d == 0 ? Double.NaN : sum3d / count3d,
                count7d == 0 ? Double.NaN : sum7d / count7d
        );
    }

    private double resolveHighestCloseSinceEntry(List<TradingDayCandleDto> candles, int signalIndex, TradingPosition position) {
        int startIndex = 0;

        if (position != null && position.getUpdatedAt() != null) {
            LocalDate positionDate = position.getUpdatedAt().atOffset(ZoneOffset.UTC).toLocalDate();
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
            double close = candles.get(i).close().doubleValue();
            if (!Double.isFinite(highest) || close > highest) {
                highest = close;
            }
        }

        return highest;
    }

    private double[] fillNaN(int size) {
        double[] values = new double[size];
        for (int i = 0; i < size; i++) {
            values[i] = Double.NaN;
        }
        return values;
    }

}
