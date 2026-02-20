package org.nowstart.evergreen.data.dto;

import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.TradeOrderType;

import java.math.BigDecimal;

public record OrderDto(
        String clientOrderId,
        String exchangeOrderId,
        String market,
        OrderSide side,
        TradeOrderType orderType,
        ExecutionMode mode,
        BigDecimal quantity,
        BigDecimal price,
        BigDecimal requestedNotional,
        BigDecimal executedVolume,
        BigDecimal avgExecutedPrice,
        BigDecimal feeAmount,
        OrderStatus status,
        String reason
) {
}
