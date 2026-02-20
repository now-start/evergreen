package org.nowstart.evergreen.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.entity.Position;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.FillRepository;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderReconciliationServiceTest {

    @Mock
    private TradingOrderRepository tradingOrderRepository;
    @Mock
    private FillRepository fillRepository;
    @Mock
    private PositionRepository positionRepository;

    private OrderReconciliationService orderReconciliationService;

    @BeforeEach
    void setUp() {
        orderReconciliationService = new OrderReconciliationService(tradingOrderRepository, fillRepository, positionRepository);

        lenient().when(tradingOrderRepository.save(any(TradingOrder.class))).thenAnswer(invocation -> invocation.getArgument(0));
        lenient().when(positionRepository.save(any(Position.class))).thenAnswer(invocation -> invocation.getArgument(0));
        lenient().when(fillRepository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void reconcile_appliesOnlyDeltaExecutedVolumeOnRepeatedPolling() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-1")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(new BigDecimal("1.0"))
                .avgExecutedPrice(new BigDecimal("100"))
                .build();

        Position position = Position.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("1.0"))
                .avgPrice(new BigDecimal("100"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(position));
        when(fillRepository.existsById(any())).thenReturn(false, true);

        UpbitOrderResponse response = orderResponse(
                "upbit-1",
                "wait",
                "2.0",
                List.of(trade("120", "1.0", "120.0", "2026-02-20T00:00:00+00:00"))
        );

        orderReconciliationService.reconcile(order, response);
        assertThat(position.getQty()).isEqualByComparingTo("2.0");

        orderReconciliationService.reconcile(order, response);
        assertThat(position.getQty()).isEqualByComparingTo("2.0");

        verify(fillRepository, times(1)).save(any());
    }

    @Test
    void reconcile_sellOrderReducesPositionByDeltaAndMarksFilled() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-2")
                .symbol("KRW-BTC")
                .side(OrderSide.SELL)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();

        Position position = Position.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("1.0"))
                .avgPrice(new BigDecimal("100"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(position));
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-2",
                "done",
                "0.4",
                List.of(trade("100", "0.4", "40.0", "2026-02-20T00:00:01+00:00"))
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getStatus()).isEqualTo(OrderStatus.FILLED);
        assertThat(position.getQty()).isEqualByComparingTo("0.6");
        assertThat(position.getState()).isEqualTo(PositionState.LONG);
    }

    @Test
    void reconcile_twoTradesSameTimestampDifferentUuid_areBothRecorded() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-3")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-3",
                "wait",
                "0.3",
                List.of(
                        trade("trade-a", "100", "0.15", "15.0", "2026-02-20T00:00:02+00:00"),
                        trade("trade-b", "101", "0.15", "15.15", "2026-02-20T00:00:02+00:00")
                )
        );

        orderReconciliationService.reconcile(order, response);

        verify(fillRepository, times(2)).save(any());
        ArgumentCaptor<Position> positionCaptor = ArgumentCaptor.forClass(Position.class);
        verify(positionRepository).save(positionCaptor.capture());
        assertThat(positionCaptor.getValue().getQty()).isEqualByComparingTo("0.3");
    }

    @Test
    void reconcile_cancelStateAlwaysMappedToCanceled() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-4")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(new BigDecimal("0.1"))
                .avgExecutedPrice(new BigDecimal("100"))
                .build();

        UpbitOrderResponse response = orderResponse(
                "upbit-4",
                "cancel",
                "0.1",
                List.of()
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getStatus()).isEqualTo(OrderStatus.CANCELED);
    }

    private UpbitOrderResponse orderResponse(String uuid, String state, String executedVolume, List<UpbitOrderResponse.UpbitTrade> trades) {
        return new UpbitOrderResponse(
                uuid,
                "ask",
                "market",
                null,
                state,
                "KRW-BTC",
                "2026-02-20T00:00:00+00:00",
                "1.0",
                "0.0",
                "0",
                "0",
                "0",
                "0",
                executedVolume,
                trades.size(),
                trades
        );
    }

    private UpbitOrderResponse.UpbitTrade trade(String price, String volume, String funds, String createdAt) {
        return trade("trade-1", price, volume, funds, createdAt);
    }

    private UpbitOrderResponse.UpbitTrade trade(String uuid, String price, String volume, String funds, String createdAt) {
        return new UpbitOrderResponse.UpbitTrade(
                "KRW-BTC",
                uuid,
                price,
                volume,
                funds,
                "ask",
                createdAt
        );
    }
}
