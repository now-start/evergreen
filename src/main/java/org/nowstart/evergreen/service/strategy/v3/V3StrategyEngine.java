package org.nowstart.evergreen.service.strategy.v3;

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
public class V3StrategyEngine implements TradingStrategyEngine<V3StrategyOverrides> {

    public static final String VERSION = "v3";

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V3StrategyOverrides> parameterType() {
        return V3StrategyOverrides.class;
    }

    @Override
    public int requiredWarmupCandles(V3StrategyOverrides params) {
        return Math.max(params.regimeEmaLen(), params.atrPeriod());
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V3StrategyOverrides> input) {
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();
        V3StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();
        double currentRatio = StrategyMath.currentPositionRatio(position);

        double[] close = StrategyMath.close(candles);
        double[] high = StrategyMath.high(candles);
        double[] low = StrategyMath.low(candles);
        double[] regimeAnchor = StrategyMath.exponentialMovingAverage(close, params.regimeEmaLen());
        double[] atr = StrategyMath.wilderAtr(high, low, close, params.atrPeriod());
        double regimeBand = params.regimeBand().doubleValue();
        MarketRegime[] regimes = StrategyMath.resolveRegimes(close, regimeAnchor, regimeBand);

        MarketRegime previousRegime = regimes[signalIndex - 1];
        MarketRegime currentRegime = regimes[signalIndex];
        boolean buySetup = previousRegime == MarketRegime.BEAR && currentRegime == MarketRegime.BULL;
        boolean sellSetup = hasPosition
                && previousRegime == MarketRegime.BULL
                && currentRegime == MarketRegime.BEAR;
        StrategyMath.TrailStopEvaluation trailStop = StrategyMath.evaluateTrailStop(
                candles,
                signalIndex,
                atr,
                params.atrTrailMultiplier().doubleValue(),
                position
        );

        double atrPriceRatio = Double.NaN;
        if (Double.isFinite(atr[signalIndex]) && Double.isFinite(close[signalIndex]) && close[signalIndex] > 0.0) {
            atrPriceRatio = atr[signalIndex] / close[signalIndex];
        }

        boolean shouldZero = currentRegime != MarketRegime.BULL || sellSetup || trailStop.triggered();
        double targetRatio = 0.0;
        if (!shouldZero && (buySetup || hasPosition)) {
            targetRatio = targetExposure(atrPriceRatio, params);
        }

        SignalAction action = StrategyMath.resolveAction(
                targetRatio > currentRatio,
                targetRatio < currentRatio
        );
        BigDecimal targetPositionRatio = BigDecimal.valueOf(targetRatio);

        double anchorValue = regimeAnchor[signalIndex];
        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number("regime.anchor", "Regime Anchor", anchorValue),
                StrategyDiagnostic.number("regime.upper", "Regime Upper Band", upper(anchorValue, regimeBand)),
                StrategyDiagnostic.number("regime.lower", "Regime Lower Band", lower(anchorValue, regimeBand)),
                StrategyDiagnostic.number("atr.value", "ATR", atr[signalIndex]),
                StrategyDiagnostic.number("vol.proxy", "Volatility Proxy", atrPriceRatio),
                StrategyDiagnostic.number("current.exposure", "Current Exposure", currentRatio),
                StrategyDiagnostic.number("target.exposure", "Target Exposure", targetRatio),
                StrategyDiagnostic.number("atr.trail_stop", "ATR Trail Stop", trailStop.stopPrice())
        );
        return new StrategyEvaluation(
                new StrategySignalDecision(
                        action,
                        resolveSignalReason(action, buySetup, sellSetup, trailStop.triggered()),
                        targetPositionRatio
                ),
                diagnostics
        );
    }

    private double targetExposure(double atrPriceRatio, V3StrategyOverrides params) {
        double floor = params.minExposure().doubleValue();
        if (!Double.isFinite(atrPriceRatio) || atrPriceRatio <= 0.0) {
            return floor;
        }
        double raw = params.volTarget().doubleValue() / atrPriceRatio;
        return Math.max(floor, Math.min(params.maxLeverage().doubleValue(), raw));
    }

    private double upper(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 + regimeBand) : Double.NaN;
    }

    private double lower(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 - regimeBand) : Double.NaN;
    }

    private String resolveSignalReason(
            SignalAction action,
            boolean buySetup,
            boolean sellSetup,
            boolean trailStopTriggered
    ) {
        if (action == SignalAction.BUY) {
            return "BUY_TARGET_EXPOSURE";
        }
        if (action == SignalAction.SELL && sellSetup && trailStopTriggered) {
            return "SELL_REGIME_AND_TRAIL_STOP";
        }
        if (action == SignalAction.SELL && trailStopTriggered) {
            return "SELL_TRAIL_STOP";
        }
        if (action == SignalAction.SELL && sellSetup) {
            return "SELL_REGIME_TRANSITION";
        }
        if (action == SignalAction.SELL) {
            return "SELL_TARGET_EXPOSURE";
        }
        if (buySetup) {
            return "SETUP_BUY";
        }
        if (sellSetup) {
            return "SETUP_SELL";
        }
        return "NONE";
    }
}
