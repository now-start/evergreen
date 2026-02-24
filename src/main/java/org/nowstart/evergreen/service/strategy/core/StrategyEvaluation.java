package org.nowstart.evergreen.service.strategy.core;

import java.util.List;

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
