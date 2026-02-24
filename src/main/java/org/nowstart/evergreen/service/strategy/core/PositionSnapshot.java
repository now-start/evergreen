package org.nowstart.evergreen.service.strategy.core;

import java.time.Instant;

public record PositionSnapshot(
        double qty,
        double avgPrice,
        Instant updatedAt
) {
    public static final PositionSnapshot EMPTY = new PositionSnapshot(0.0, 0.0, null);

    public boolean hasPosition() {
        return Double.isFinite(qty) && qty > 0.0;
    }
}
