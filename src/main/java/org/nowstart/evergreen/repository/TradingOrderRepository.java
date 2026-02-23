package org.nowstart.evergreen.repository;

import java.util.List;
import java.util.Optional;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradingOrderRepository extends JpaRepository<TradingOrder, String> {

    Optional<TradingOrder> findByClientOrderId(String clientOrderId);

    boolean existsByModeAndSymbolAndStatusIn(ExecutionMode mode, String symbol, List<OrderStatus> statuses);

    List<TradingOrder> findBySymbolAndModeAndStatusOrderByCreatedAtAsc(String symbol, ExecutionMode mode, OrderStatus status);

    List<TradingOrder> findBySymbolAndModeOrderByCreatedAtAsc(String symbol, ExecutionMode mode);
}
