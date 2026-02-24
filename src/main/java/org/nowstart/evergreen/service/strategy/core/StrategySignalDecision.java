package org.nowstart.evergreen.service.strategy.core;

public record StrategySignalDecision(
        boolean buySignal,
        boolean sellSignal,
        String signalReason
) {
}
