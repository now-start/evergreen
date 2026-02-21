package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.UUID;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.springframework.stereotype.Service;

@Service
public class TradingOrderFactory {

    public TradingOrder build(CreateOrderRequest request) {
        BigDecimal requestedNotional;
        if (request.orderType() == TradeOrderType.MARKET_BUY) {
            requestedNotional = request.price();
        } else if (request.orderType() == TradeOrderType.MARKET_SELL) {
            requestedNotional = safe(request.quantity()).multiply(safe(request.price()));
        } else {
            requestedNotional = safe(request.quantity()).multiply(safe(request.price()));
        }

        return TradingOrder.builder()
                .clientOrderId(UUID.randomUUID().toString())
                .symbol(request.market())
                .side(request.side())
                .orderType(request.orderType())
                .mode(request.mode())
                .quantity(request.quantity())
                .price(request.price())
                .status(OrderStatus.CREATED)
                .reason(request.reason())
                .requestedNotional(requestedNotional)
                .build();
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
