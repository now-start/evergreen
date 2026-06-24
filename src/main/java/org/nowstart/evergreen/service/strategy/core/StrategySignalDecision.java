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
}
