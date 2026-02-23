package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
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
                120,
                18,
                new BigDecimal("2.0"),
                new BigDecimal("3.0"),
                40,
                new BigDecimal("0.6"),
                new BigDecimal("0.01"),
                new BigDecimal("100000")
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
}
