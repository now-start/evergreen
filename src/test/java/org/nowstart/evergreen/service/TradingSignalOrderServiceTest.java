package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
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
        TradingSignalOrderService service = new TradingSignalOrderService(
                tradingExecutionService,
                properties(),
                tradingSignalStateService
        );
        TradingDayCandleDto signal = new TradingDayCandleDto(
                Instant.parse("2026-02-21T00:00:00Z"),
                new BigDecimal("100"),
                new BigDecimal("101"),
                new BigDecimal("99"),
                new BigDecimal("100"),
                new BigDecimal("1000")
        );

        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(new OrderDto(
                "client-1",
                "upbit-1",
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                ExecutionMode.LIVE,
                null,
                null,
                null,
                new BigDecimal("0.001"),
                new BigDecimal("100"),
                new BigDecimal("0.0"),
                OrderStatus.SUBMITTED,
                "signal:2026-02-21T00:00:00Z"
        ));

        service.submitBuySignal("KRW-BTC", signal);

        ArgumentCaptor<SignalExecuteRequest> requestCaptor = ArgumentCaptor.forClass(SignalExecuteRequest.class);
        verify(tradingExecutionService).executeSignal(requestCaptor.capture());
        SignalExecuteRequest request = requestCaptor.getValue();
        assertThat(request.mode()).isEqualTo(ExecutionMode.LIVE);
        assertThat(request.orderType()).isEqualTo(TradeOrderType.MARKET_BUY);
        assertThat(request.quantity()).isNull();
        assertThat(request.price()).isNull();
    }

    @Test
    void submitBuySignal_liveStillSubmitsWithoutConfiguredNotional() {
        TradingSignalOrderService service = new TradingSignalOrderService(
                tradingExecutionService,
                properties(),
                tradingSignalStateService
        );
        TradingDayCandleDto signal = new TradingDayCandleDto(
                Instant.parse("2026-02-21T00:00:00Z"),
                new BigDecimal("100"),
                new BigDecimal("101"),
                new BigDecimal("99"),
                new BigDecimal("100"),
                new BigDecimal("1000")
        );

        when(tradingSignalStateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, signal.timestamp())).thenReturn(false);
        when(tradingExecutionService.executeSignal(any())).thenReturn(new OrderDto(
                "client-2",
                "upbit-2",
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                ExecutionMode.LIVE,
                null,
                null,
                null,
                null,
                null,
                null,
                OrderStatus.SUBMITTED,
                "signal:2026-02-21T00:00:00Z"
        ));

        service.submitBuySignal("KRW-BTC", signal);

        verify(tradingExecutionService).executeSignal(any());
    }

    private TradingProperties properties() {
        return new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.LIVE,
                List.of("KRW-BTC"),
                400,
                true,
                new BigDecimal("100000"),
                "v5"
        );
    }
}
