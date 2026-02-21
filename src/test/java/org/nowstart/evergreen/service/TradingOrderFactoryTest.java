package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;

class TradingOrderFactoryTest {

    private final TradingOrderFactory tradingOrderFactory = new TradingOrderFactory();

    @Test
    void build_marketBuyUsesPriceAsRequestedNotional() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                null,
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        TradingOrder order = tradingOrderFactory.build(request);

        assertThat(order.getRequestedNotional()).isEqualByComparingTo("50000");
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CREATED);
    }

    @Test
    void build_marketSellComputesRequestedNotionalFromQuantityAndPrice() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.2"),
                new BigDecimal("100000000"),
                ExecutionMode.LIVE,
                "test"
        );

        TradingOrder order = tradingOrderFactory.build(request);

        assertThat(order.getRequestedNotional()).isEqualByComparingTo("20000000");
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CREATED);
    }

    @Test
    void build_marketSellWithNullPriceFallsBackToZeroRequestedNotional() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.2"),
                null,
                ExecutionMode.LIVE,
                "test"
        );

        TradingOrder order = tradingOrderFactory.build(request);

        assertThat(order.getRequestedNotional()).isEqualByComparingTo("0");
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CREATED);
    }
}
