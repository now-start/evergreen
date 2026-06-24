package org.nowstart.evergreen.service.strategy.v2;

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
public class V2StrategyEngine implements TradingStrategyEngine<V2StrategyOverrides> {

    public static final String VERSION = "v2";

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V2StrategyOverrides> parameterType() {
        return V2StrategyOverrides.class;
    }

    @Override
    public int requiredWarmupCandles(V2StrategyOverrides params) {
        return Math.max(params.regimeEmaLen(), params.atrPeriod());
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V2StrategyOverrides> input) {
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();
        V2StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();

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

        boolean shouldBuy = !hasPosition && buySetup;
        boolean shouldSell = hasPosition && (sellSetup || trailStop.triggered());
        SignalAction action = StrategyMath.resolveAction(shouldBuy, shouldSell);
        BigDecimal targetPositionRatio = BigDecimal.valueOf(StrategyMath.targetRatio(
                action,
                StrategyMath.currentPositionRatio(position)
        ));

        double anchorValue = regimeAnchor[signalIndex];
        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number("regime.anchor", "Regime Anchor", anchorValue),
                StrategyDiagnostic.number("regime.upper", "Regime Upper Band", upper(anchorValue, regimeBand)),
                StrategyDiagnostic.number("regime.lower", "Regime Lower Band", lower(anchorValue, regimeBand)),
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

    static double upper(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 + regimeBand) : Double.NaN;
    }

    static double lower(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 - regimeBand) : Double.NaN;
    }

    static String resolveSignalReason(
            SignalAction action,
            boolean buySetup,
            boolean sellSetup,
            boolean trailStopTriggered
    ) {
        if (action == SignalAction.BUY) {
            return "BUY_REGIME_TRANSITION";
        }
        if (action == SignalAction.SELL && sellSetup && trailStopTriggered) {
            return "SELL_REGIME_AND_TRAIL_STOP";
        }
        if (action == SignalAction.SELL && trailStopTriggered) {
            return "SELL_TRAIL_STOP";
        }
        if (action == SignalAction.SELL) {
            return "SELL_REGIME_TRANSITION";
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
