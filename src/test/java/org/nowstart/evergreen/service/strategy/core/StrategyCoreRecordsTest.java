package org.nowstart.evergreen.service.strategy.core;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;

class StrategyCoreRecordsTest {

    @Test
    void strategyEvaluation_requiresDecision() {
        assertThatThrownBy(() -> new StrategyEvaluation(null, List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("decision is required");
    }

    @Test
    void strategyEvaluation_defaultsDiagnosticsWhenNull() {
        StrategyEvaluation evaluation = new StrategyEvaluation(
                new StrategySignalDecision(false, false, "NONE"),
                null
        );

        assertThat(evaluation.diagnostics()).isEmpty();
    }

    @Test
    void strategyDiagnostic_requiresNonBlankKey() {
        assertThatThrownBy(() -> new StrategyDiagnostic(" ", "x", 1.0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("diagnostic key is required");
    }

    @Test
    void strategyDiagnostic_numberFactoryUsesKeyAsFallbackLabel() {
        StrategyDiagnostic diagnostic = StrategyDiagnostic.number("atr.value", "", 2.5);

        assertThat(diagnostic.key()).isEqualTo("atr.value");
        assertThat(diagnostic.label()).isEqualTo("atr.value");
        assertThat(diagnostic.value()).isEqualTo(2.5);
    }
}
