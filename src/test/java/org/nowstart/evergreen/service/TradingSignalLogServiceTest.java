package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategySignalDecision;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;

@ExtendWith(OutputCaptureExtension.class)
class TradingSignalLogServiceTest {

    private final TradingSignalLogService service = new TradingSignalLogService();

    @Test
    void logCandleSignal_includesDiagnosticLabelInStrategyDiagnosticLog(CapturedOutput output) {
        StrategyEvaluation evaluation = new StrategyEvaluation(
                new StrategySignalDecision(false, false, "HOLD"),
                List.of(
                        StrategyDiagnostic.number("atr.value", "ATR", 2.5),
                        StrategyDiagnostic.number("regime.lower", "Regime Lower Band", 95.0)
                )
        );

        service.logCandleSignal(new TradingSignalLogService.TradingSignalLogContext(
                "KRW-BTC",
                "v5-test",
                new TradingDayCandleDto(
                        Instant.parse("2026-02-24T00:00:00Z"),
                        new BigDecimal("100"),
                        new BigDecimal("110"),
                        new BigDecimal("90"),
                        new BigDecimal("105"),
                        new BigDecimal("1000")
                ),
                105.0,
                false,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                0.0,
                TradingExecutionMetrics.empty(),
                evaluation
        ));

        assertThat(output).containsPattern(
                "event=strategy_diagnostic[^\\n]*key=atr\\.value[^\\n]*label=\\\"ATR\\\""
        );
        assertThat(output).containsPattern(
                "event=strategy_diagnostic[^\\n]*key=regime\\.lower[^\\n]*label=\\\"Regime Lower Band\\\""
        );
    }
}
