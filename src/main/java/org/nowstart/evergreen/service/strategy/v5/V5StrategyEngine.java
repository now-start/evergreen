package org.nowstart.evergreen.service.strategy.v5;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.List;
import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;
import org.nowstart.evergreen.service.strategy.core.StrategySignalDecision;
import org.nowstart.evergreen.service.strategy.core.TradingStrategyEngine;
import org.springframework.stereotype.Component;

@Component
public class V5StrategyEngine implements TradingStrategyEngine<V5StrategyOverrides> {

    public static final String VERSION = "v5";

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V5StrategyOverrides> parameterType() {
        return V5StrategyOverrides.class;
    }

    @Override
    public int requiredWarmupCandles(V5StrategyOverrides params) {
        return Math.max(
                Math.max(params.regimeEmaLen(), params.atrPeriod()),
                params.volRegimeLookback()
        );
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V5StrategyOverrides> input) {
        if (input == null || input.candles() == null || input.params() == null) {
            throw new IllegalArgumentException("input, candles, and params are required");
        }

        List<OhlcvCandle> candles = input.candles();
        int n = candles.size();
        int signalIndex = input.signalIndex();
        if (signalIndex < 1 || signalIndex >= n) {
            throw new IllegalArgumentException("signalIndex must be in [1, candles.size()-1]");
        }

        V5StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();
        double regimeBand = params.regimeBand().doubleValue();
        double volRegimeThreshold = params.volRegimeThreshold().doubleValue();
        double atrMultLowVol = params.atrMultLowVol().doubleValue();
        double atrMultHighVol = params.atrMultHighVol().doubleValue();

        double[] close = candles.stream().mapToDouble(OhlcvCandle::close).toArray();
        double[] high = candles.stream().mapToDouble(OhlcvCandle::high).toArray();
        double[] low = candles.stream().mapToDouble(OhlcvCandle::low).toArray();

        double[] regimeAnchor = exponentialMovingAverage(close, params.regimeEmaLen());
        double[] atr = wilderAtr(high, low, close, params.atrPeriod());
        MarketRegime[] regimes = resolveRegimes(close, regimeAnchor, regimeBand);
        VolatilityState volatility = resolveVolatilityStates(
                atr,
                close,
                params.volRegimeLookback(),
                volRegimeThreshold
        );

        MarketRegime prevRegime = regimes[signalIndex - 1];
        MarketRegime currentRegime = regimes[signalIndex];

        boolean baseBuy = prevRegime == MarketRegime.BEAR
                && currentRegime == MarketRegime.BULL;
        boolean baseSell = hasPosition
                && prevRegime == MarketRegime.BULL
                && currentRegime == MarketRegime.BEAR;

        double atrMultiplier = volatility.isHigh()[signalIndex]
                ? atrMultHighVol
                : atrMultLowVol;

        TrailStopEvaluation trailStop = evaluateTrailStop(
                candles,
                signalIndex,
                atr,
                atrMultiplier,
                position,
                hasPosition
        );

        boolean buySignal = !hasPosition && baseBuy;
        boolean sellSignal = hasPosition && (baseSell || trailStop.triggered());
        String signalReason = resolveSignalReason(
                buySignal,
                sellSignal,
                baseBuy,
                baseSell,
                trailStop.triggered()
        );

        double anchorValue = regimeAnchor[signalIndex];
        double upperValue = Double.isFinite(anchorValue)
                ? anchorValue * (1.0 + regimeBand)
                : Double.NaN;
        double lowerValue = Double.isFinite(anchorValue)
                ? anchorValue * (1.0 - regimeBand)
                : Double.NaN;

        TradingSignalQualityStats signalQuality = resolveSignalQualityStats(close, regimes, signalIndex);

        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.text(
                        "regime.previous",
                        "Previous Regime",
                        "Previous regime state at signal index",
                        prevRegime.name()
                ),
                StrategyDiagnostic.text(
                        "regime.current",
                        "Current Regime",
                        "Current regime state at signal index",
                        currentRegime.name()
                ),
                StrategyDiagnostic.number(
                        "regime.anchor",
                        "Regime Anchor",
                        "price",
                        "EMA anchor used for regime decision",
                        anchorValue
                ),
                StrategyDiagnostic.number(
                        "regime.upper",
                        "Regime Upper Band",
                        "price",
                        "Upper regime boundary",
                        upperValue
                ),
                StrategyDiagnostic.number(
                        "regime.lower",
                        "Regime Lower Band",
                        "price",
                        "Lower regime boundary",
                        lowerValue
                ),
                StrategyDiagnostic.number(
                        "atr.value",
                        "ATR",
                        "price",
                        "Average true range at signal index",
                        atr[signalIndex]
                ),
                StrategyDiagnostic.number(
                        "atr.multiplier",
                        "ATR Multiplier",
                        "",
                        "Applied ATR multiplier based on volatility regime",
                        atrMultiplier
                ),
                StrategyDiagnostic.number(
                        "atr.trail_stop",
                        "ATR Trail Stop",
                        "price",
                        "Calculated ATR trailing stop",
                        trailStop.stopPrice()
                ),
                StrategyDiagnostic.bool(
                        "trail_stop.triggered",
                        "Trail Stop Triggered",
                        "Whether ATR trail stop triggered sell",
                        trailStop.triggered()
                ),
                StrategyDiagnostic.bool(
                        "volatility.is_high",
                        "High Volatility",
                        "Volatility regime flag",
                        volatility.isHigh()[signalIndex]
                ),
                StrategyDiagnostic.number(
                        "volatility.atr_price_ratio",
                        "ATR/Price Ratio",
                        "",
                        "ATR to close price ratio",
                        volatility.atrPriceRatio()[signalIndex]
                ),
                StrategyDiagnostic.number(
                        "volatility.percentile",
                        "Volatility Percentile",
                        "",
                        "Rolling ATR/price percentile",
                        volatility.percentile()[signalIndex]
                ),
                StrategyDiagnostic.number(
                        "signal_quality.avg_1d_pct",
                        "Signal Quality 1D",
                        "pct",
                        "Average post-signal return over 1 day",
                        signalQuality.avg1dPct()
                ),
                StrategyDiagnostic.number(
                        "signal_quality.avg_3d_pct",
                        "Signal Quality 3D",
                        "pct",
                        "Average post-signal return over 3 days",
                        signalQuality.avg3dPct()
                ),
                StrategyDiagnostic.number(
                        "signal_quality.avg_7d_pct",
                        "Signal Quality 7D",
                        "pct",
                        "Average post-signal return over 7 days",
                        signalQuality.avg7dPct()
                )
        );

        return new StrategyEvaluation(new StrategySignalDecision(buySignal, sellSignal, signalReason), diagnostics);
    }

    private double[] exponentialMovingAverage(double[] values, int length) {
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

    private double[] wilderAtr(double[] high, double[] low, double[] close, int period) {
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

    private MarketRegime[] resolveRegimes(double[] close, double[] anchor, double regimeBand) {
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

    private VolatilityState resolveVolatilityStates(double[] atr, double[] close, int lookback, double threshold) {
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

    private TrailStopEvaluation evaluateTrailStop(
            List<OhlcvCandle> candles,
            int signalIndex,
            double[] atr,
            double atrMultiplier,
            PositionSnapshot position,
            boolean hasPosition
    ) {
        if (!hasPosition || atrMultiplier <= 0.0 || !Double.isFinite(atr[signalIndex])) {
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

    private String resolveSignalReason(
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

    private TradingSignalQualityStats resolveSignalQualityStats(double[] close, MarketRegime[] regimes, int signalIndex) {
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

    private double resolveHighestCloseSinceEntry(List<OhlcvCandle> candles, int signalIndex, PositionSnapshot position) {
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

    private double[] fillNaN(int size) {
        double[] values = new double[size];
        Arrays.fill(values, Double.NaN);
        return values;
    }

    private record VolatilityState(
            double[] atrPriceRatio,
            double[] percentile,
            boolean[] isHigh
    ) {
    }

    private record TrailStopEvaluation(
            double stopPrice,
            boolean triggered
    ) {
    }
}
