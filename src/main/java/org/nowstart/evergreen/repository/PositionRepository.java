package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.TradingPosition;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface PositionRepository extends JpaRepository<TradingPosition, String> {

    Optional<TradingPosition> findBySymbol(String symbol);
}
