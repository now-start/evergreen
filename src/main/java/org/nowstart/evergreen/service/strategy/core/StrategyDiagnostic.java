package org.nowstart.evergreen.service.strategy.core;

/**
 * One strategy-specific diagnostic value produced during {@link TradingStrategyEngine#evaluate(StrategyInput)}.
 *
 * <p>Diagnostics are for observability and dashboarding, not for execution flow control. A strategy engine
 * builds diagnostics and returns them via {@link StrategyEvaluation}. The logging layer then serializes:
 * <ul>
 *   <li>all diagnostics into {@code candle_signal.diagnostics}</li>
 *   <li>numeric/boolean diagnostics as {@code event=strategy_diagnostic} time series</li>
 * </ul>
 *
 * <p>Typical usage inside a strategy engine:
 * <pre>{@code
 * List<StrategyDiagnostic> diagnostics = List.of(
 *         StrategyDiagnostic.number("ema.fast", "Fast EMA", "KRW", "Current fast EMA value", emaFast),
 *         StrategyDiagnostic.bool("entry.ready", "Entry Ready", "Entry condition satisfied", entryReady),
 *         StrategyDiagnostic.text("regime.current", "Regime", "Current regime label", regimeLabel)
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
 *   <li>Use {@code unit} only for numeric values (for example {@code %}, {@code KRW}).</li>
 * </ul>
 *
 * @param key         stable diagnostic identifier used by logs and dashboard queries
 * @param label       human-readable name for UI legend/tooltips
 * @param type        expected value type
 * @param unit        value unit (usually for numeric diagnostics)
 * @param description optional short explanation
 * @param value       actual diagnostic value
 */
public record StrategyDiagnostic(
        String key,
        String label,
        StrategyDiagnosticType type,
        String unit,
        String description,
        Object value
) {

    public StrategyDiagnostic {
        if (key == null || key.isBlank()) {
            throw new IllegalArgumentException("diagnostic key is required");
        }
        if (type == null) {
            throw new IllegalArgumentException("diagnostic type is required");
        }
        if (value == null) {
            throw new IllegalArgumentException("diagnostic value is required");
        }
        label = (label == null || label.isBlank()) ? key : label;
        unit = unit == null ? "" : unit;
        description = description == null ? "" : description;
        validateType(type, value, key);
    }

    /**
     * Creates a numeric diagnostic.
     *
     * @param key stable machine-readable key
     * @param label display label
     * @param unit unit such as {@code %}, {@code KRW}, {@code ratio}
     * @param description optional explanation
     * @param value numeric value
     * @return number diagnostic
     */
    public static StrategyDiagnostic number(
            String key,
            String label,
            String unit,
            String description,
            double value
    ) {
        return new StrategyDiagnostic(key, label, StrategyDiagnosticType.NUMBER, unit, description, value);
    }

    /**
     * Creates a boolean diagnostic.
     *
     * @param key stable machine-readable key
     * @param label display label
     * @param description optional explanation
     * @param value boolean value
     * @return boolean diagnostic
     */
    public static StrategyDiagnostic bool(
            String key,
            String label,
            String description,
            boolean value
    ) {
        return new StrategyDiagnostic(key, label, StrategyDiagnosticType.BOOLEAN, "", description, value);
    }

    /**
     * Creates a string diagnostic.
     *
     * <p>String diagnostics are kept in structured log payloads but are not emitted as numeric time series.
     *
     * @param key stable machine-readable key
     * @param label display label
     * @param description optional explanation
     * @param value string value (null becomes empty string)
     * @return string diagnostic
     */
    public static StrategyDiagnostic text(
            String key,
            String label,
            String description,
            String value
    ) {
        return new StrategyDiagnostic(key, label, StrategyDiagnosticType.STRING, "", description, value == null ? "" : value);
    }

    private static void validateType(StrategyDiagnosticType type, Object value, String key) {
        if (!type.supports(value)) {
            throw new IllegalArgumentException("diagnostic " + key + " must be " + type.typeName());
        }
    }
}
