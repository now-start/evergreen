package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.StrategyState;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface StrategyStateRepository extends JpaRepository<StrategyState, String> {

    Optional<StrategyState> findBySymbol(String symbol);
}
