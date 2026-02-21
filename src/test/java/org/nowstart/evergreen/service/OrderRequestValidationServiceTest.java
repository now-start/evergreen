package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.TradeOrderType;

class OrderRequestValidationServiceTest {

    private final OrderRequestValidationService validationService = new OrderRequestValidationService();

    @Test
    void validate_acceptsValidPaperMarketBuyRequest() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                new BigDecimal("0.01"),
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        assertThatCode(() -> validationService.validate(request)).doesNotThrowAnyException();
    }

    @Test
    void validate_rejectsInvalidSideAndOrderTypeCombination() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_BUY,
                new BigDecimal("0.01"),
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("SELL side cannot use MARKET_BUY");
    }

    @Test
    void validate_rejectsPaperMarketSellWithoutPrice() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.01"),
                null,
                ExecutionMode.PAPER,
                "test"
        );

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("PAPER MARKET_SELL requires price");
    }

    @Test
    void validate_rejectsLimitWithoutQuantity() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.LIMIT,
                null,
                new BigDecimal("50000"),
                ExecutionMode.LIVE,
                "test"
        );

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("LIMIT order requires quantity and price");
    }
}
