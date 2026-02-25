package org.nowstart.evergreen.service.strategy.core;

/**
 * One strategy-specific diagnostic value produced during {@link TradingStrategyEngine#evaluate(StrategyInput)}.
 *
 * <p>Diagnostics are for observability and dashboarding, not for execution flow control. A strategy engine
 * builds diagnostics and returns them via {@link StrategyEvaluation}. The logging layer then serializes:
 * <ul>
 *   <li>all diagnostics into {@code candle_signal.diagnostics}</li>
 *   <li>numeric diagnostics as {@code event=strategy_diagnostic} time series</li>
 * </ul>
 *
 * <p>Typical usage inside a strategy engine:
 * <pre>{@code
 * List<StrategyDiagnostic> diagnostics = List.of(
 *         StrategyDiagnostic.number("ema.fast", "Fast EMA", emaFast),
 *         StrategyDiagnostic.number("ema.slow", "Slow EMA", emaSlow)
 * );
 *
 * return new StrategyEvaluation(
 *         new StrategySignalDecision(buySignal, sellSignal, reason),
 *         diagnostics
 * );
 * }</pre>
 *
 * <p>Key conventions:
 * <ul>
 *   <li>Use stable machine-readable keys (for example {@code ema.fast}, {@code signal.confidence}).</li>
 *   <li>{@code label} is display-friendly text; {@code key} is the query/grouping identifier.</li>
 * </ul>
 *
 * @param key         stable diagnostic identifier used by logs and dashboard queries
 * @param label       human-readable name for UI legend/tooltips
 * @param value       numeric diagnostic value
 */
public record StrategyDiagnostic(
        String key,
        String label,
        double value
) {

    public StrategyDiagnostic {
        if (key == null || key.isBlank()) {
            throw new IllegalArgumentException("diagnostic key is required");
        }
        label = (label == null || label.isBlank()) ? key : label;
    }

    /**
     * Creates a numeric diagnostic.
     *
     * @param key stable machine-readable key
     * @param label display label
     * @param value numeric value
     * @return number diagnostic
     */
    public static StrategyDiagnostic number(
            String key,
            String label,
            double value
    ) {
        return new StrategyDiagnostic(key, label, value);
    }
}
