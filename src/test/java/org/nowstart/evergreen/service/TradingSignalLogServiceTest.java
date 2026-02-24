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
                        StrategyDiagnostic.number("atr.multiplier", "ATR Multiplier", "", "", 2.5),
                        StrategyDiagnostic.bool("trail_stop.triggered", "Trail Stop Triggered", "", true),
                        StrategyDiagnostic.text("regime.current", "Current Regime", "", "BULL")
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
                "event=strategy_diagnostic[^\\n]*key=atr\\.multiplier[^\\n]*label=\\\"ATR Multiplier\\\""
        );
        assertThat(output).containsPattern(
                "event=strategy_diagnostic[^\\n]*key=trail_stop\\.triggered[^\\n]*label=\\\"Trail Stop Triggered\\\""
        );
    }
}
