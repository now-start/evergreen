package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.Position;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface PositionRepository extends JpaRepository<Position, String> {

    Optional<Position> findBySymbol(String symbol);
}
