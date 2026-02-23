package org.nowstart.evergreen.repository;

import java.util.Optional;
import org.nowstart.evergreen.data.entity.PositionDriftSnapshot;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PositionDriftSnapshotRepository extends JpaRepository<PositionDriftSnapshot, Long> {

    Optional<PositionDriftSnapshot> findTopBySymbolOrderByCapturedAtDesc(String symbol);
}
