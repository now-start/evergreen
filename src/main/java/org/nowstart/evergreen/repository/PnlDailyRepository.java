package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.PnlDaily;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDate;
import java.util.Optional;

public interface PnlDailyRepository extends JpaRepository<PnlDaily, PnlDaily.PnlDailyKey> {

    Optional<PnlDaily> findByIdSymbolAndIdPnlDate(String symbol, LocalDate pnlDate);
}
