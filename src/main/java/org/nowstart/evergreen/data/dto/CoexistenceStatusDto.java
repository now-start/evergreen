package org.nowstart.evergreen.data.dto;

import java.math.BigDecimal;
import java.time.Instant;

public record CoexistenceStatusDto(
        String market,
        BigDecimal totalQty,
        boolean hasExternalOpenOrder,
        boolean guardBlocked,
        String guardBlockedReason,
        int externalOpenOrderCount,
        Instant lastPositionSyncAt
) {
}
