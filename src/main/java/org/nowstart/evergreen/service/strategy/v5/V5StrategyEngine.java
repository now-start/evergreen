package org.nowstart.evergreen.service.strategy.v5;

import java.math.BigDecimal;
import java.util.List;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.SignalAction;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;
import org.nowstart.evergreen.service.strategy.core.StrategyMath;
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
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();

        V5StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();
        double regimeBand = params.regimeBand().doubleValue();
        double volRegimeThreshold = params.volRegimeThreshold().doubleValue();
        double atrMultLowVol = params.atrMultLowVol().doubleValue();
        double atrMultHighVol = params.atrMultHighVol().doubleValue();

        double[] close = StrategyMath.close(candles);
        double[] high = StrategyMath.high(candles);
        double[] low = StrategyMath.low(candles);

        double[] regimeAnchor = StrategyMath.exponentialMovingAverage(close, params.regimeEmaLen());
        double[] atr = StrategyMath.wilderAtr(high, low, close, params.atrPeriod());
        MarketRegime[] regimes = StrategyMath.resolveRegimes(close, regimeAnchor, regimeBand);
        StrategyMath.VolatilityState volatility = StrategyMath.resolveVolatilityStates(
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

        StrategyMath.TrailStopEvaluation trailStop = StrategyMath.evaluateTrailStop(
                candles,
                signalIndex,
                atr,
                atrMultiplier,
                position
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

        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number(
                        "regime.anchor",
                        "Regime Anchor",
                        anchorValue
                ),
                StrategyDiagnostic.number(
                        "regime.upper",
                        "Regime Upper Band",
                        upperValue
                ),
                StrategyDiagnostic.number(
                        "regime.lower",
                        "Regime Lower Band",
                        lowerValue
                ),
                StrategyDiagnostic.number(
                        "atr.trail_stop",
                        "ATR Trail Stop",
                        trailStop.stopPrice()
                )
        );

        SignalAction action = StrategyMath.resolveAction(buySignal, sellSignal);
        BigDecimal targetPositionRatio = BigDecimal.valueOf(StrategyMath.targetRatio(
                action,
                StrategyMath.currentPositionRatio(position)
        ));
        return new StrategyEvaluation(
                new StrategySignalDecision(action, signalReason, targetPositionRatio),
                diagnostics
        );
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
}
