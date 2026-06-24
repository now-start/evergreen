package org.nowstart.evergreen.service.strategy.core;

import java.math.BigDecimal;

public record StrategySignalDecision(
        SignalAction action,
        String signalReason,
        BigDecimal targetPositionRatio
) {

    public StrategySignalDecision {
        if (action == null) {
            throw new IllegalArgumentException("action is required");
        }
        if (targetPositionRatio != null && targetPositionRatio.signum() < 0) {
            throw new IllegalArgumentException("targetPositionRatio must be >= 0");
        }
    }

    public StrategySignalDecision(SignalAction action, String signalReason) {
        this(action, signalReason, defaultTargetPositionRatio(action));
    }

    public StrategySignalDecision(boolean buySignal, boolean sellSignal, String signalReason) {
        this(resolveAction(buySignal, sellSignal), signalReason);
    }

    public StrategySignalDecision(
            boolean buySignal,
            boolean sellSignal,
            String signalReason,
            BigDecimal targetPositionRatio
    ) {
        this(resolveAction(buySignal, sellSignal), signalReason, targetPositionRatio);
    }

    public boolean buySignal() {
        return action == SignalAction.BUY;
    }

    public boolean sellSignal() {
        return action == SignalAction.SELL;
    }

    private static SignalAction resolveAction(boolean buySignal, boolean sellSignal) {
        if (buySignal && sellSignal) {
            throw new IllegalArgumentException("buySignal and sellSignal cannot both be true");
        }
        if (buySignal) {
            return SignalAction.BUY;
        }
        if (sellSignal) {
            return SignalAction.SELL;
        }
        return SignalAction.HOLD;
    }

    private static BigDecimal defaultTargetPositionRatio(SignalAction action) {
        if (action == SignalAction.BUY) {
            return BigDecimal.ONE;
        }
        if (action == SignalAction.SELL) {
            return BigDecimal.ZERO;
        }
        return null;
    }
}
