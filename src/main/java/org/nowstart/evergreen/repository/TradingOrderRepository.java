package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface TradingOrderRepository extends JpaRepository<TradingOrder, String> {

    Optional<TradingOrder> findByClientOrderId(String clientOrderId);

    List<TradingOrder> findByStatusOrderByUpdatedAtAsc(OrderStatus status);
}
