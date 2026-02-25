package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.repository.TradingOrderRepository;

@ExtendWith(MockitoExtension.class)
class TradingSignalMetricsServiceTest {

    private static final String MARKET = "KRW-BTC";

    @Mock
    private TradingOrderRepository tradingOrderRepository;

    private TradingSignalMetricsService service;

    @BeforeEach
    void setUp() {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.PAPER,
                List.of(MARKET),
                400,
                true,
                new BigDecimal("100000"),
                "v5"
        );
        service = new TradingSignalMetricsService(tradingOrderRepository, properties);
    }

    @Test
    void resolveExecutionMetrics_countsRoundTripTradeWhenSellIsSplit() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of(
                filledOrder("b1", OrderSide.BUY, "1.0", "100"),
                filledOrder("s1", OrderSide.SELL, "0.4", "120"),
                filledOrder("s2", OrderSide.SELL, "0.6", "110")
        ));

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isEqualTo(1);
        assertClose(metrics.realizedPnlKrw(), 14.0);
        assertClose(metrics.realizedReturnPct(), 14.0);
        assertClose(metrics.winRatePct(), 100.0);
        assertClose(metrics.avgWinPct(), 14.0);
        assertThat(metrics.avgLossPct()).isNaN();
        assertThat(metrics.rrRatio()).isNaN();
        assertClose(metrics.expectancyPct(), 14.0);
        assertClose(metrics.maxDrawdownPct(), 0.0);
    }

    @Test
    void resolveExecutionMetrics_usesClosedTradesForWinRateRrAndExpectancy() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of(
                filledOrder("b1", OrderSide.BUY, "1.0", "100"),
                filledOrder("s1", OrderSide.SELL, "1.0", "110"),
                filledOrder("b2", OrderSide.BUY, "1.0", "100"),
                filledOrder("s2", OrderSide.SELL, "1.0", "90")
        ));

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isEqualTo(2);
        assertClose(metrics.realizedPnlKrw(), 0.0);
        assertClose(metrics.realizedReturnPct(), 0.0);
        assertClose(metrics.winRatePct(), 50.0);
        assertClose(metrics.avgWinPct(), 10.0);
        assertClose(metrics.avgLossPct(), 10.0);
        assertClose(metrics.rrRatio(), 1.0);
        assertClose(metrics.expectancyPct(), 0.0);
        assertClose(metrics.maxDrawdownPct(), -10.0);
    }

    @Test
    void resolveExecutionMetrics_ignoresOpenPositionFromTradeStats() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of(
                filledOrder("b1", OrderSide.BUY, "1.0", "100")
        ));

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isZero();
        assertClose(metrics.realizedPnlKrw(), 0.0);
        assertThat(metrics.realizedReturnPct()).isNaN();
        assertThat(metrics.winRatePct()).isNaN();
        assertThat(metrics.avgWinPct()).isNaN();
        assertThat(metrics.avgLossPct()).isNaN();
        assertThat(metrics.rrRatio()).isNaN();
        assertThat(metrics.expectancyPct()).isNaN();
        assertThat(metrics.maxDrawdownPct()).isNaN();
    }

    private TradingOrder filledOrder(String clientOrderId, OrderSide side, String qty, String price) {
        return TradingOrder.builder()
                .clientOrderId(clientOrderId)
                .symbol(MARKET)
                .side(side)
                .mode(ExecutionMode.PAPER)
                .status(OrderStatus.FILLED)
                .executedVolume(new BigDecimal(qty))
                .avgExecutedPrice(new BigDecimal(price))
                .feeAmount(new BigDecimal("0"))
                .build();
    }

    private void assertClose(double actual, double expected) {
        assertThat(Math.abs(actual - expected)).isLessThan(1.0e-9);
    }

    @Test
    void resolveUnrealizedReturnPct_returnsNaNForInvalidInputs() {
        assertThat(service.resolveUnrealizedReturnPct(false, 120.0, 100.0)).isNaN();
        assertThat(service.resolveUnrealizedReturnPct(true, Double.NaN, 100.0)).isNaN();
        assertThat(service.resolveUnrealizedReturnPct(true, 120.0, 0.0)).isNaN();
    }

    @Test
    void resolveUnrealizedReturnPct_returnsPercentForValidInputs() {
        assertThat(service.resolveUnrealizedReturnPct(true, 120.0, 100.0)).isCloseTo(20.0, within(1.0e-9));
    }

    @Test
    void resolveExecutionMetrics_returnsEmptyWhenNoFilledOrders() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of());

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isZero();
        assertThat(metrics.realizedPnlKrw()).isNaN();
        assertThat(metrics.realizedReturnPct()).isNaN();
        assertThat(metrics.maxDrawdownPct()).isNaN();
        assertThat(metrics.winRatePct()).isNaN();
        assertThat(metrics.avgWinPct()).isNaN();
        assertThat(metrics.avgLossPct()).isNaN();
        assertThat(metrics.rrRatio()).isNaN();
        assertThat(metrics.expectancyPct()).isNaN();
    }

    @Test
    void resolveExecutionMetrics_skipsMalformedOrders() {
        ArrayList<TradingOrder> orders = new ArrayList<>();
        orders.add(null);
        orders.add(TradingOrder.builder()
                .clientOrderId("null-side")
                .symbol(MARKET)
                .mode(ExecutionMode.PAPER)
                .status(OrderStatus.FILLED)
                .side(null)
                .executedVolume(BigDecimal.ONE)
                .avgExecutedPrice(BigDecimal.valueOf(100.0))
                .feeAmount(null)
                .build());
        orders.add(TradingOrder.builder()
                .clientOrderId("zero-qty")
                .symbol(MARKET)
                .mode(ExecutionMode.PAPER)
                .status(OrderStatus.FILLED)
                .side(OrderSide.SELL)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(BigDecimal.valueOf(100.0))
                .feeAmount(BigDecimal.ZERO)
                .build());
        orders.add(TradingOrder.builder()
                .clientOrderId("null-price")
                .symbol(MARKET)
                .mode(ExecutionMode.PAPER)
                .status(OrderStatus.FILLED)
                .side(OrderSide.BUY)
                .executedVolume(BigDecimal.ONE)
                .avgExecutedPrice(null)
                .feeAmount(null)
                .build());
        orders.add(TradingOrder.builder()
                .clientOrderId("valid-buy")
                .symbol(MARKET)
                .mode(ExecutionMode.PAPER)
                .status(OrderStatus.FILLED)
                .side(OrderSide.BUY)
                .executedVolume(new BigDecimal("0.1"))
                .avgExecutedPrice(new BigDecimal("150"))
                .feeAmount(null)
                .build());

        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(orders);

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isZero();
        assertThat(metrics.realizedPnlKrw()).isEqualTo(0.0);
    }

    @Test
    void resolveExecutionMetrics_skipsSellWhenNoOpenPositionExists() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of(
                filledOrder("s-only", OrderSide.SELL, "0.2", "120")
        ));

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isZero();
        assertThat(metrics.realizedPnlKrw()).isEqualTo(0.0);
        assertThat(metrics.realizedReturnPct()).isNaN();
    }

    @Test
    void resolveExecutionMetrics_skipsOrderWhenQuantityOrPriceIsNonPositive() {
        when(tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                MARKET,
                ExecutionMode.PAPER,
                OrderStatus.FILLED
        )).thenReturn(List.of(
                TradingOrder.builder()
                        .clientOrderId("bad-buy")
                        .symbol(MARKET)
                        .mode(ExecutionMode.PAPER)
                        .status(OrderStatus.FILLED)
                        .side(OrderSide.BUY)
                        .executedVolume(BigDecimal.ZERO)
                        .avgExecutedPrice(new BigDecimal("100"))
                        .feeAmount(BigDecimal.ZERO)
                        .build()
        ));

        TradingExecutionMetrics metrics = service.resolveExecutionMetrics(MARKET);

        assertThat(metrics.tradeCount()).isZero();
        assertThat(metrics.realizedPnlKrw()).isEqualTo(0.0);
        assertThat(metrics.realizedReturnPct()).isNaN();
        assertThat(metrics.maxDrawdownPct()).isNaN();
        assertThat(metrics.winRatePct()).isNaN();
        assertThat(metrics.avgWinPct()).isNaN();
        assertThat(metrics.avgLossPct()).isNaN();
        assertThat(metrics.rrRatio()).isNaN();
        assertThat(metrics.expectancyPct()).isNaN();
    }

}
