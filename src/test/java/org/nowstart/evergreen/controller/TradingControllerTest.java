package org.nowstart.evergreen.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.BalanceDto;
import org.nowstart.evergreen.data.dto.CoexistenceStatusDto;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.dto.OrderChanceDto;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.nowstart.evergreen.service.CoexistenceStatusService;
import org.nowstart.evergreen.service.TradingExecutionService;
import org.springframework.http.HttpStatus;

@ExtendWith(MockitoExtension.class)
class TradingControllerTest {

    @Mock
    private TradingExecutionService tradingExecutionService;

    @Mock
    private CoexistenceStatusService coexistenceStatusService;

    @InjectMocks
    private TradingController controller;

    @Test
    void getBalances_delegatesToService() {
        List<BalanceDto> balances = List.of(new BalanceDto(
                "KRW",
                new BigDecimal("100"),
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                "KRW"
        ));
        when(tradingExecutionService.getBalances("KRW")).thenReturn(balances);

        List<BalanceDto> result = controller.getBalances("KRW");

        assertThat(result).isEqualTo(balances);
        verify(tradingExecutionService).getBalances("KRW");
    }

    @Test
    void getOrderChance_delegatesToService() {
        OrderChanceDto dto = new OrderChanceDto(
                "KRW-BTC",
                new BigDecimal("0.0005"),
                new BigDecimal("0.0005"),
                new BigDecimal("1000"),
                new BigDecimal("0.1"),
                new BigDecimal("1000")
        );
        when(tradingExecutionService.getOrderChance("KRW-BTC")).thenReturn(dto);

        OrderChanceDto result = controller.getOrderChance("KRW-BTC");

        assertThat(result).isEqualTo(dto);
        verify(tradingExecutionService).getOrderChance("KRW-BTC");
    }

    @Test
    void createOrder_returnsCreatedResponse() {
        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.LIMIT,
                new BigDecimal("0.01"),
                new BigDecimal("100000000"),
                ExecutionMode.PAPER,
                "test"
        );
        OrderDto order = sampleOrder("cid-1");
        when(tradingExecutionService.createOrder(request)).thenReturn(order);

        var response = controller.createOrder(request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getBody()).isEqualTo(order);
        verify(tradingExecutionService).createOrder(request);
    }

    @Test
    void getOrder_delegatesToService() {
        OrderDto order = sampleOrder("cid-2");
        when(tradingExecutionService.getOrder("cid-2")).thenReturn(order);

        OrderDto result = controller.getOrder("cid-2");

        assertThat(result).isEqualTo(order);
        verify(tradingExecutionService).getOrder("cid-2");
    }

    @Test
    void cancelOrder_delegatesToService() {
        OrderDto order = sampleOrder("cid-3");
        when(tradingExecutionService.cancelOrder("cid-3")).thenReturn(order);

        OrderDto result = controller.cancelOrder("cid-3");

        assertThat(result).isEqualTo(order);
        verify(tradingExecutionService).cancelOrder("cid-3");
    }

    @Test
    void executeSignal_returnsCreatedResponse() {
        SignalExecuteRequest request = new SignalExecuteRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.02"),
                null,
                ExecutionMode.PAPER,
                "2026-02-25T00:00:00Z"
        );
        OrderDto order = sampleOrder("cid-4");
        when(tradingExecutionService.executeSignal(request)).thenReturn(order);

        var response = controller.executeSignal(request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getBody()).isEqualTo(order);
        verify(tradingExecutionService).executeSignal(request);
    }

    @Test
    void getCoexistenceStatus_delegatesToService() {
        CoexistenceStatusDto dto = new CoexistenceStatusDto(
                "KRW-BTC",
                new BigDecimal("0.5"),
                false,
                false,
                "",
                0,
                Instant.parse("2026-02-25T00:00:00Z")
        );
        when(coexistenceStatusService.resolveStatus("KRW-BTC")).thenReturn(dto);

        CoexistenceStatusDto result = controller.getCoexistenceStatus("KRW-BTC");

        assertThat(result).isEqualTo(dto);
        verify(coexistenceStatusService).resolveStatus("KRW-BTC");
    }

    private OrderDto sampleOrder(String clientOrderId) {
        return new OrderDto(
                clientOrderId,
                "ex-" + clientOrderId,
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.LIMIT,
                ExecutionMode.PAPER,
                new BigDecimal("0.01"),
                new BigDecimal("100000000"),
                new BigDecimal("1000000"),
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                OrderStatus.CREATED,
                "reason"
        );
    }
}
