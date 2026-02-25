package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;

@ExtendWith(MockitoExtension.class)
class TradingSignalOrderServiceTest {

    @Mock
    private TradingExecutionService tradingExecutionService;
    @Mock
    private TradingSignalStateService tradingSignalStateService;

    @Test
    void submitBuySignal_liveUsesFullKrwBalanceByLeavingPriceNull() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100");

        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(order("client-1", ExecutionMode.LIVE, new BigDecimal("100")));

        service.submitBuySignal("KRW-BTC", signal);

        ArgumentCaptor<SignalExecuteRequest> requestCaptor = ArgumentCaptor.forClass(SignalExecuteRequest.class);
        verify(tradingExecutionService).executeSignal(requestCaptor.capture());
        SignalExecuteRequest request = requestCaptor.getValue();
        assertThat(request.mode()).isEqualTo(ExecutionMode.LIVE);
        assertThat(request.orderType()).isEqualTo(TradeOrderType.MARKET_BUY);
        assertThat(request.quantity()).isNull();
        assertThat(request.price()).isNull();
        verify(tradingSignalStateService).recordSubmittedSignal("KRW-BTC", OrderSide.BUY, signal.timestamp());
    }

    @Test
    void submitBuySignal_liveStillSubmitsWithoutConfiguredNotional() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100");

        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(order("client-2", ExecutionMode.LIVE, null));

        service.submitBuySignal("KRW-BTC", signal);

        verify(tradingExecutionService).executeSignal(any());
    }

    @Test
    void submitBuySignal_returnsImmediatelyWhenDuplicate() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(true);

        service.submitBuySignal("KRW-BTC", signal);

        verifyNoInteractions(tradingExecutionService);
    }

    @Test
    void submitBuySignal_paperSkipsWhenClosePriceIsNonPositive() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.PAPER, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("0");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);

        service.submitBuySignal("KRW-BTC", signal);

        verifyNoInteractions(tradingExecutionService);
    }

    @Test
    void submitBuySignal_paperSkipsWhenComputedQuantityBecomesZero() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.PAPER, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100000000000000000000");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);

        service.submitBuySignal("KRW-BTC", signal);

        verifyNoInteractions(tradingExecutionService);
    }

    @Test
    void submitBuySignal_paperSubmitsWithComputedQuantityAndConfiguredNotional() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.PAPER, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("25000");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(order("client-paper-buy", ExecutionMode.PAPER, new BigDecimal("25100")));

        service.submitBuySignal("KRW-BTC", signal);

        ArgumentCaptor<SignalExecuteRequest> captor = ArgumentCaptor.forClass(SignalExecuteRequest.class);
        verify(tradingExecutionService).executeSignal(captor.capture());
        SignalExecuteRequest request = captor.getValue();
        assertThat(request.mode()).isEqualTo(ExecutionMode.PAPER);
        assertThat(request.orderType()).isEqualTo(TradeOrderType.MARKET_BUY);
        assertThat(request.quantity()).isEqualByComparingTo("4");
        assertThat(request.price()).isEqualByComparingTo("100000");
    }

    @Test
    void submitSellSignal_returnsImmediatelyWhenDuplicate() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.SELL, signal.timestamp())).thenReturn(true);

        service.submitSellSignal("KRW-BTC", signal, new BigDecimal("0.2"));

        verifyNoInteractions(tradingExecutionService);
    }

    @Test
    void submitSellSignal_returnsWhenPositionQuantityIsMissingOrZero() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("100");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.SELL, signal.timestamp())).thenReturn(false);

        service.submitSellSignal("KRW-BTC", signal, null);
        service.submitSellSignal("KRW-BTC", signal, BigDecimal.ZERO);

        verifyNoInteractions(tradingExecutionService);
    }

    @Test
    void submitSellSignal_paperUsesSignalCloseAsExecutionPrice() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.PAPER, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("101.5");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.SELL, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(order("client-paper-sell", ExecutionMode.PAPER, null));

        service.submitSellSignal("KRW-BTC", signal, new BigDecimal("0.25"));

        ArgumentCaptor<SignalExecuteRequest> captor = ArgumentCaptor.forClass(SignalExecuteRequest.class);
        verify(tradingExecutionService).executeSignal(captor.capture());
        SignalExecuteRequest request = captor.getValue();
        assertThat(request.mode()).isEqualTo(ExecutionMode.PAPER);
        assertThat(request.orderType()).isEqualTo(TradeOrderType.MARKET_SELL);
        assertThat(request.quantity()).isEqualByComparingTo("0.25");
        assertThat(request.price()).isEqualByComparingTo("101.5");
        verify(tradingSignalStateService).recordSubmittedSignal("KRW-BTC", OrderSide.SELL, signal.timestamp());
    }

    @Test
    void submitSellSignal_liveLeavesPriceNull() {
        TradingSignalOrderService service = serviceFor(ExecutionMode.LIVE, new BigDecimal("100000"));
        TradingDayCandleDto signal = candle("101.5");
        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.SELL, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(order("client-live-sell", ExecutionMode.LIVE, new BigDecimal("100")));

        service.submitSellSignal("KRW-BTC", signal, new BigDecimal("0.25"));

        ArgumentCaptor<SignalExecuteRequest> captor = ArgumentCaptor.forClass(SignalExecuteRequest.class);
        verify(tradingExecutionService).executeSignal(captor.capture());
        assertThat(captor.getValue().price()).isNull();
    }

    private TradingSignalOrderService serviceFor(ExecutionMode mode, BigDecimal signalOrderNotional) {
        return new TradingSignalOrderService(
                tradingExecutionService,
                properties(mode, signalOrderNotional),
                tradingSignalStateService
        );
    }

    private TradingProperties properties(ExecutionMode mode, BigDecimal signalOrderNotional) {
        return new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                mode,
                List.of("KRW-BTC"),
                400,
                true,
                signalOrderNotional,
                "v5"
        );
    }

    private TradingDayCandleDto candle(String closePrice) {
        return new TradingDayCandleDto(
                Instant.parse("2026-02-21T00:00:00Z"),
                new BigDecimal("100"),
                new BigDecimal("101"),
                new BigDecimal("99"),
                new BigDecimal(closePrice),
                new BigDecimal("1000")
        );
    }

    private OrderDto order(String clientOrderId, ExecutionMode mode, BigDecimal avgExecutedPrice) {
        return new OrderDto(
                clientOrderId,
                "upbit-" + clientOrderId,
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                mode,
                null,
                null,
                null,
                new BigDecimal("0.001"),
                avgExecutedPrice,
                new BigDecimal("0.0"),
                OrderStatus.SUBMITTED,
                "signal:2026-02-21T00:00:00Z"
        );
    }
}
