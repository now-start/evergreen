package org.nowstart.evergreen.service.strategy.core;

import java.util.List;

/**
 * Immutable output of one strategy evaluation.
 *
 * <p>{@link #decision()} contains the executable signal decision (buy/sell/hold reason),
 * and {@link #diagnostics()} contains strategy-specific explainability metrics for logging/dashboard use.
 *
 * @param decision    final signal decision for the evaluated candle
 * @param diagnostics optional diagnostics emitted by the strategy
 */
public record StrategyEvaluation(
        StrategySignalDecision decision,
        List<StrategyDiagnostic> diagnostics
) {

    public StrategyEvaluation {
        if (decision == null) {
            throw new IllegalArgumentException("decision is required");
        }
        diagnostics = diagnostics == null ? List.of() : List.copyOf(diagnostics);
    }
}
