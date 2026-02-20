package org.nowstart.evergreen.data.dto;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.TradeOrderType;

import java.math.BigDecimal;

public record CreateOrderRequest(
        @NotBlank(message = "market is required")
        String market,
        @NotNull(message = "side is required")
        OrderSide side,
        @NotNull(message = "orderType is required")
        TradeOrderType orderType,
        @DecimalMin(value = "0", inclusive = false, message = "quantity must be greater than zero")
        BigDecimal quantity,
        @DecimalMin(value = "0", inclusive = false, message = "price must be greater than zero")
        BigDecimal price,
        @NotNull(message = "mode is required")
        ExecutionMode mode,
        String reason
) {
}
