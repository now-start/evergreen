package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface TradingOrderRepository extends JpaRepository<TradingOrder, String> {

    Optional<TradingOrder> findByClientOrderId(String clientOrderId);

    boolean existsByModeAndSymbolAndStatusIn(ExecutionMode mode, String symbol, List<OrderStatus> statuses);
}
