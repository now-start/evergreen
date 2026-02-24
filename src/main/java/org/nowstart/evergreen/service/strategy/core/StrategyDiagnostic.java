package org.nowstart.evergreen.service.strategy.core;

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

    public static StrategyDiagnostic number(
            String key,
            String label,
            String unit,
            String description,
            double value
    ) {
        return new StrategyDiagnostic(key, label, StrategyDiagnosticType.NUMBER, unit, description, value);
    }

    public static StrategyDiagnostic bool(
            String key,
            String label,
            String description,
            boolean value
    ) {
        return new StrategyDiagnostic(key, label, StrategyDiagnosticType.BOOLEAN, "", description, value);
    }

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
