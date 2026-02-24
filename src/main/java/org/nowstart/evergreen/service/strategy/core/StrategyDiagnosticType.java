package org.nowstart.evergreen.service.strategy.core;

/**
 * Value type contract for {@link StrategyDiagnostic}.
 *
 * <p>The type is validated at diagnostic creation time and drives downstream serialization behavior
 * (for example numeric/boolean diagnostics can be emitted as time-series values).
 */
public enum StrategyDiagnosticType {
    NUMBER(Number.class),
    BOOLEAN(Boolean.class),
    STRING(String.class);

    private final Class<?> valueType;

    StrategyDiagnosticType(Class<?> valueType) {
        this.valueType = valueType;
    }

    /**
     * Checks whether the given value matches this diagnostic type.
     *
     * @param value candidate diagnostic value
     * @return true when the value is compatible
     */
    public boolean supports(Object value) {
        return valueType.isInstance(value);
    }

    /**
     * Returns the short Java type name used in validation error messages.
     *
     * @return simple class name of the expected type
     */
    public String typeName() {
        return valueType.getSimpleName();
    }
}
