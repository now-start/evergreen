package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.time.Instant;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.entity.PositionDriftSnapshot;
import org.nowstart.evergreen.repository.PositionDriftSnapshotRepository;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
public class PositionDriftService {

    private final PositionDriftSnapshotRepository positionDriftSnapshotRepository;

    public void captureSnapshot(String market, BigDecimal totalQty, BigDecimal managedQty) {
        BigDecimal safeTotalQty = safe(totalQty);
        BigDecimal safeManagedQty = safe(managedQty);
        BigDecimal externalQty = safeTotalQty.subtract(safeManagedQty);
        if (externalQty.compareTo(BigDecimal.ZERO) < 0) {
            externalQty = BigDecimal.ZERO;
        }
        BigDecimal sellableQty = safeTotalQty.compareTo(BigDecimal.ZERO) < 0 ? BigDecimal.ZERO : safeTotalQty;
        BigDecimal driftQty = safeTotalQty.subtract(safeManagedQty).abs();
        boolean driftDetected = driftQty.compareTo(BigDecimal.ZERO) > 0;
        PositionDriftSnapshot previous = positionDriftSnapshotRepository
                .findTopBySymbolOrderByCapturedAtDesc(market)
                .orElse(null);

        PositionDriftSnapshot snapshot = positionDriftSnapshotRepository.save(PositionDriftSnapshot.builder()
                .symbol(market)
                .totalQty(safeTotalQty)
                .managedQty(safeManagedQty)
                .externalQty(externalQty)
                .driftQty(driftQty)
                .driftDetected(driftDetected)
                .capturedAt(Instant.now())
                .build());

        log.info(
                "event=position_snapshot market={} total_qty={} managed_qty={} external_qty={} sellable_qty={} drift_qty={} drift_detected={}",
                market,
                safeTotalQty,
                safeManagedQty,
                externalQty,
                sellableQty,
                driftQty,
                driftDetected
        );

        if (shouldEmitExternalDrift(previous, snapshot)) {
            log.warn(
                    "event=external_position_drift market={} total_qty={} managed_qty={} external_qty={} drift_qty={}",
                    market,
                    safeTotalQty,
                    safeManagedQty,
                    externalQty,
                    driftQty
            );
        }
    }

    private boolean shouldEmitExternalDrift(PositionDriftSnapshot previous, PositionDriftSnapshot current) {
        if (current == null || !current.isDriftDetected()) {
            return false;
        }
        if (previous == null || !previous.isDriftDetected()) {
            return true;
        }
        return compare(previous.getDriftQty(), current.getDriftQty()) != 0
                || compare(previous.getTotalQty(), current.getTotalQty()) != 0
                || compare(previous.getManagedQty(), current.getManagedQty()) != 0;
    }

    private int compare(BigDecimal left, BigDecimal right) {
        return safe(left).compareTo(safe(right));
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
