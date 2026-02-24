package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.entity.Fill;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.FillRepository;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;

@ExtendWith(MockitoExtension.class)
class PaperExecutionServiceTest {

    @Mock
    private TradingOrderRepository tradingOrderRepository;
    @Mock
    private FillRepository fillRepository;
    @Mock
    private PositionRepository positionRepository;

    private PaperExecutionService paperExecutionService;

    @BeforeEach
    void setUp() {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.001"),
                Duration.ofSeconds(30),
                ExecutionMode.LIVE,
                List.of("KRW-BTC"),
                400,
                true,
                new BigDecimal("100000"),
                "v5"
        );

        paperExecutionService = new PaperExecutionService(
                tradingOrderRepository,
                fillRepository,
                positionRepository,
                properties
        );

        when(tradingOrderRepository.save(any(TradingOrder.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(fillRepository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        when(positionRepository.save(any(TradingPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void execute_buyOrderUpdatesOrderFillAndPosition() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("paper-order-1")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .quantity(new BigDecimal("0.5"))
                .price(new BigDecimal("100"))
                .status(OrderStatus.CREATED)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());

        TradingOrder executed = paperExecutionService.execute(order);

        assertThat(executed.getStatus()).isEqualTo(OrderStatus.FILLED);
        assertThat(executed.getExecutedVolume()).isEqualByComparingTo("0.5");
        assertThat(executed.getFeeAmount()).isEqualByComparingTo("0.05");

        ArgumentCaptor<TradingPosition> positionCaptor = ArgumentCaptor.forClass(TradingPosition.class);
        verify(positionRepository).save(positionCaptor.capture());
        TradingPosition savedPosition = positionCaptor.getValue();
        assertThat(savedPosition.getQty()).isEqualByComparingTo("0.5");
        assertThat(savedPosition.getAvgPrice()).isEqualByComparingTo("100");
        assertThat(savedPosition.getState()).isEqualTo(PositionState.LONG);
    }

    @Test
    void execute_sellOrderCanFlattenPosition() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("paper-order-2")
                .symbol("KRW-BTC")
                .side(OrderSide.SELL)
                .quantity(new BigDecimal("0.5"))
                .price(new BigDecimal("100"))
                .status(OrderStatus.CREATED)
                .build();

        TradingPosition current = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("0.5"))
                .avgPrice(new BigDecimal("90"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(current));

        paperExecutionService.execute(order);

        ArgumentCaptor<TradingPosition> positionCaptor = ArgumentCaptor.forClass(TradingPosition.class);
        verify(positionRepository).save(positionCaptor.capture());
        TradingPosition savedPosition = positionCaptor.getValue();
        assertThat(savedPosition.getQty()).isEqualByComparingTo("0");
        assertThat(savedPosition.getAvgPrice()).isEqualByComparingTo("0");
        assertThat(savedPosition.getState()).isEqualTo(PositionState.FLAT);
    }

    @Test
    void execute_derivesQuantityFromRequestedNotionalWhenQuantityIsNull() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("paper-order-3")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .quantity(null)
                .price(new BigDecimal("100"))
                .requestedNotional(new BigDecimal("50"))
                .status(OrderStatus.CREATED)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());

        TradingOrder executed = paperExecutionService.execute(order);

        assertThat(executed.getExecutedVolume()).isEqualByComparingTo("0.5");
        assertThat(executed.getFeeAmount()).isEqualByComparingTo("0.05");

        ArgumentCaptor<Fill> fillCaptor = ArgumentCaptor.forClass(Fill.class);
        verify(fillRepository).save(fillCaptor.capture());
        assertThat(fillCaptor.getValue().getFillQty()).isEqualByComparingTo("0.5");
    }
}
