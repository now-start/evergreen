package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.Candle1d;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface Candle1dRepository extends JpaRepository<Candle1d, Candle1d.Candle1dKey> {

    List<Candle1d> findByIdSymbolAndIdTsBetweenOrderByIdTsAsc(String symbol, Instant from, Instant to);

    Optional<Candle1d> findTopByIdSymbolOrderByIdTsDesc(String symbol);
}
