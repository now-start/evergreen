package org.nowstart.evergreen.strategy.core;

public record StrategySignalDecision(
        boolean buySignal,
        boolean sellSignal,
        String signalReason
) {
}
