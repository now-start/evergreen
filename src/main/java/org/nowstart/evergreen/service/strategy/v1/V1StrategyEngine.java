package org.nowstart.evergreen.service.strategy.v1;

import java.math.BigDecimal;
import java.util.List;
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
public class V1StrategyEngine implements TradingStrategyEngine<V1StrategyOverrides> {

    public static final String VERSION = "v1";
    private static final int RSI_PERIOD = 14;

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V1StrategyOverrides> parameterType() {
        return V1StrategyOverrides.class;
    }

    @Override
    public int requiredWarmupCandles(V1StrategyOverrides params) {
        return Math.max(params.maLen() + params.maSlopeDays(), RSI_PERIOD + 1);
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V1StrategyOverrides> input) {
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();
        V1StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();

        double[] close = StrategyMath.close(candles);
        double[] ma = StrategyMath.movingAverage(close, params.maLen());
        double[] rsi = StrategyMath.wilderRsi(close, RSI_PERIOD);

        boolean slopeOk = signalIndex - params.maSlopeDays() >= 0
                && Double.isFinite(ma[signalIndex])
                && Double.isFinite(ma[signalIndex - params.maSlopeDays()])
                && ma[signalIndex] > ma[signalIndex - params.maSlopeDays()];
        boolean bull = Double.isFinite(ma[signalIndex]) && close[signalIndex] > ma[signalIndex] && slopeOk;
        boolean buySetup = bull
                && Double.isFinite(rsi[signalIndex])
                && rsi[signalIndex] < params.rsiBuy().doubleValue();
        boolean sellSetup = Double.isFinite(ma[signalIndex]) && close[signalIndex] < ma[signalIndex];

        boolean shouldBuy = !hasPosition && buySetup;
        boolean shouldSell = hasPosition && sellSetup;
        SignalAction action = StrategyMath.resolveAction(shouldBuy, shouldSell);
        BigDecimal targetPositionRatio = BigDecimal.valueOf(StrategyMath.targetRatio(
                action,
                StrategyMath.currentPositionRatio(position)
        ));

        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number("ma.value", "Moving Average", ma[signalIndex]),
                StrategyDiagnostic.number("rsi.value", "RSI", rsi[signalIndex])
        );
        return new StrategyEvaluation(
                new StrategySignalDecision(action, resolveSignalReason(action, buySetup, sellSetup), targetPositionRatio),
                diagnostics
        );
    }

    private String resolveSignalReason(SignalAction action, boolean buySetup, boolean sellSetup) {
        if (action == SignalAction.BUY) {
            return "BUY_MA_RSI";
        }
        if (action == SignalAction.SELL) {
            return "SELL_MA_BREAK";
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
