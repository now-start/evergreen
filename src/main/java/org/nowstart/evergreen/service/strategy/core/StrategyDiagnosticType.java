package org.nowstart.evergreen.service.strategy.core;

public enum StrategyDiagnosticType {
    NUMBER(Number.class),
    BOOLEAN(Boolean.class),
    STRING(String.class);

    private final Class<?> valueType;

    StrategyDiagnosticType(Class<?> valueType) {
        this.valueType = valueType;
    }

    public boolean supports(Object value) {
        return valueType.isInstance(value);
    }

    public String typeName() {
        return valueType.getSimpleName();
    }
}
