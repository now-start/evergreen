package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.entity.Fill;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.FillRepository;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;

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
        orderReconciliationService = new OrderReconciliationService(
                tradingOrderRepository,
                fillRepository,
                positionRepository
        );

        lenient().when(tradingOrderRepository.save(any(TradingOrder.class))).thenAnswer(invocation -> invocation.getArgument(0));
        lenient().when(positionRepository.save(any(TradingPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
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

        TradingPosition totalPosition = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("1.0"))
                .avgPrice(new BigDecimal("100"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(totalPosition));
        when(fillRepository.existsById(any())).thenReturn(false, true);

        UpbitOrderResponse response = orderResponse(
                "upbit-1",
                "wait",
                "2.0",
                List.of(trade("120", "1.0", "120.0", "2026-02-20T00:00:00+00:00"))
        );

        orderReconciliationService.reconcile(order, response);
        assertThat(totalPosition.getQty()).isEqualByComparingTo("2.0");

        orderReconciliationService.reconcile(order, response);
        assertThat(totalPosition.getQty()).isEqualByComparingTo("2.0");

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

        TradingPosition totalPosition = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("1.0"))
                .avgPrice(new BigDecimal("100"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(totalPosition));
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-2",
                "done",
                "0.4",
                List.of(trade("100", "0.4", "40.0", "2026-02-20T00:00:01+00:00"))
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getStatus()).isEqualTo(OrderStatus.FILLED);
        assertThat(totalPosition.getQty()).isEqualByComparingTo("0.6");
        assertThat(totalPosition.getState()).isEqualTo(PositionState.LONG);
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
        ArgumentCaptor<TradingPosition> positionCaptor = ArgumentCaptor.forClass(TradingPosition.class);
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

    @Test
    void reconcile_waitStateWithZeroExecutedVolumeMapsToSubmitted() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-5")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();

        UpbitOrderResponse response = new UpbitOrderResponse(
                "upbit-5",
                "ask",
                "market",
                null,
                "wait",
                "KRW-BTC",
                "2026-02-20T00:00:00+00:00",
                "1.0",
                "0.0",
                "0",
                "0",
                "0",
                "0",
                "0",
                0,
                null
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getStatus()).isEqualTo(OrderStatus.SUBMITTED);
        verifyNoInteractions(positionRepository);
    }

    @Test
    void reconcile_tradeWithoutFundsFallsBackToPriceTimesVolume() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-6")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(
                TradingPosition.builder()
                        .symbol("KRW-BTC")
                        .qty(BigDecimal.ZERO)
                        .avgPrice(BigDecimal.ZERO)
                        .state(PositionState.FLAT)
                        .build()
        ));
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-6",
                "wait",
                "0.2",
                List.of(trade("trade-6", "100", "0.2", "0", "2026-02-20T00:00:03+00:00"))
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getAvgExecutedPrice()).isEqualByComparingTo(BigDecimal.ZERO);
        ArgumentCaptor<TradingPosition> captor = ArgumentCaptor.forClass(TradingPosition.class);
        verify(positionRepository).save(captor.capture());
        assertThat(captor.getValue().getAvgPrice()).isEqualByComparingTo("100");
        assertThat(captor.getValue().getQty()).isEqualByComparingTo("0.2");
    }

    @Test
    void reconcile_sellDeltaThatExceedsPositionFlattensPortfolio() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-7")
                .symbol("KRW-BTC")
                .side(OrderSide.SELL)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();
        TradingPosition existing = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("0.1"))
                .avgPrice(new BigDecimal("100"))
                .state(PositionState.LONG)
                .build();
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(existing));
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-7",
                "done",
                "0.3",
                List.of(trade("trade-7", "100", "0.3", "30", "2026-02-20T00:00:04+00:00"))
        );

        orderReconciliationService.reconcile(order, response);

        assertThat(existing.getQty()).isEqualByComparingTo(BigDecimal.ZERO);
        assertThat(existing.getAvgPrice()).isEqualByComparingTo(BigDecimal.ZERO);
        assertThat(existing.getState()).isEqualTo(PositionState.FLAT);
    }

    @Test
    void reconcile_usesFallbackTradeUuidAndEpochOnBlankOrInvalidTimestamp() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-8")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = orderResponse(
                "upbit-8",
                "wait",
                "0.2",
                List.of(
                        trade("", "100", "0.1", "10", " "),
                        trade("", "100", "0.1", "10", "not-a-date")
                )
        );

        orderReconciliationService.reconcile(order, response);

        ArgumentCaptor<Fill> fillCaptor = ArgumentCaptor.forClass(Fill.class);
        verify(fillRepository, times(2)).save(fillCaptor.capture());
        List<Fill> fills = fillCaptor.getAllValues();
        assertThat(fills.get(0).getId().getFilledAt()).isEqualTo(java.time.Instant.EPOCH);
        assertThat(fills.get(1).getId().getFilledAt()).isEqualTo(java.time.Instant.EPOCH);
        assertThat(fills.get(0).getId().getTradeUuid()).startsWith("client-8:");
    }

    @Test
    void reconcile_whenTotalTradeVolumeIsZeroFallsBackToResponsePrice() {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-9")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .status(OrderStatus.SUBMITTED)
                .executedVolume(BigDecimal.ZERO)
                .avgExecutedPrice(new BigDecimal("100"))
                .build();
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());
        when(fillRepository.existsById(any())).thenReturn(false);

        UpbitOrderResponse response = new UpbitOrderResponse(
                "upbit-9",
                "bid",
                "market",
                "123",
                "wait",
                "KRW-BTC",
                "2026-02-20T00:00:00+00:00",
                "0.2",
                "0.0",
                "0",
                "0",
                "0",
                "0",
                "0.2",
                1,
                List.of(trade("trade-9", "123", "0", "0", "2026-02-20T00:00:06+00:00"))
        );

        TradingOrder reconciled = orderReconciliationService.reconcile(order, response);

        assertThat(reconciled.getAvgExecutedPrice()).isEqualByComparingTo("123");
    }

    @Test
    void applyPositionDelta_returnsImmediatelyWhenDeltaQuantityIsNotPositive() throws Exception {
        TradingOrder order = TradingOrder.builder()
                .clientOrderId("client-zero-delta")
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .build();

        Method method = OrderReconciliationService.class.getDeclaredMethod(
                "applyPositionDelta",
                TradingOrder.class,
                BigDecimal.class,
                BigDecimal.class
        );
        method.setAccessible(true);
        method.invoke(orderReconciliationService, order, BigDecimal.ZERO, new BigDecimal("100"));

        verifyNoInteractions(positionRepository);
        verifyNoMoreInteractions(fillRepository);
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
