package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;

@ExtendWith(MockitoExtension.class)
class TradingOrderGuardServiceTest {

    @Mock
    private TradingOrderRepository tradingOrderRepository;
    @Mock
    private UpbitFeignClient upbitFeignClient;

    @Test
    void hasBlockingOrder_returnsTrueWhenLocalActiveOrderExists() {
        TradingOrderGuardService service = new TradingOrderGuardService(
                tradingOrderRepository,
                upbitFeignClient,
                properties(ExecutionMode.LIVE)
        );
        when(tradingOrderRepository.existsByModeAndSymbolAndStatusIn(any(), any(), any())).thenReturn(true);

        TradingOrderGuardService.GuardDecision decision = service.evaluate("KRW-BTC");

        assertThat(decision.blocked()).isTrue();
        assertThat(decision.reason()).isEqualTo(TradingOrderGuardService.GUARD_REASON_LOCAL_ACTIVE_ORDER);
        verify(upbitFeignClient, never()).getOpenOrders(any(), any());
    }

    @Test
    void hasBlockingOrder_returnsTrueWhenExchangeOpenOrderExists() {
        TradingOrderGuardService service = new TradingOrderGuardService(
                tradingOrderRepository,
                upbitFeignClient,
                properties(ExecutionMode.LIVE)
        );
        when(tradingOrderRepository.existsByModeAndSymbolAndStatusIn(any(), any(), any())).thenReturn(false);
        when(upbitFeignClient.getOpenOrders("KRW-BTC", "wait")).thenReturn(List.of(openOrder()));

        TradingOrderGuardService.GuardDecision decision = service.evaluate("KRW-BTC");

        assertThat(decision.blocked()).isTrue();
        assertThat(decision.reason()).isEqualTo(TradingOrderGuardService.GUARD_REASON_EXTERNAL_OPEN_ORDER);
        assertThat(decision.hasExternalOpenOrder()).isTrue();
        assertThat(decision.externalOpenOrderCount()).isEqualTo(1);
    }

    @Test
    void hasBlockingOrder_returnsFalseWhenNotLiveMode() {
        TradingOrderGuardService service = new TradingOrderGuardService(
                tradingOrderRepository,
                upbitFeignClient,
                properties(ExecutionMode.PAPER)
        );
        when(tradingOrderRepository.existsByModeAndSymbolAndStatusIn(any(), any(), any())).thenReturn(false);

        TradingOrderGuardService.GuardDecision decision = service.evaluate("KRW-BTC");

        assertThat(decision.blocked()).isFalse();
        assertThat(decision.reason()).isEqualTo(TradingOrderGuardService.GUARD_REASON_NONE);
        verify(upbitFeignClient, never()).getOpenOrders(any(), any());
    }

    @Test
    void hasBlockingOrder_returnsTrueWhenExternalGuardQueryFails() {
        TradingOrderGuardService service = new TradingOrderGuardService(
                tradingOrderRepository,
                upbitFeignClient,
                properties(ExecutionMode.LIVE)
        );
        when(tradingOrderRepository.existsByModeAndSymbolAndStatusIn(any(), any(), any())).thenReturn(false);
        when(upbitFeignClient.getOpenOrders("KRW-BTC", "wait")).thenThrow(new IllegalStateException("upbit timeout"));

        TradingOrderGuardService.GuardDecision decision = service.evaluate("KRW-BTC");

        assertThat(decision.blocked()).isTrue();
        assertThat(decision.reason()).isEqualTo(TradingOrderGuardService.GUARD_REASON_GUARD_QUERY_FAILED);
    }

    private TradingProperties properties(ExecutionMode mode) {
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
                120,
                18,
                new BigDecimal("2.0"),
                new BigDecimal("3.0"),
                40,
                new BigDecimal("0.6"),
                new BigDecimal("0.01"),
                new BigDecimal("100000")
        );
    }

    private UpbitOrderResponse openOrder() {
        return new UpbitOrderResponse(
                "external-1",
                "ask",
                "market",
                null,
                "wait",
                "KRW-BTC",
                "2026-02-21T01:00:00+00:00",
                "0.1",
                "0.1",
                "0",
                "0",
                "0",
                "0",
                "0",
                0,
                List.of()
        );
    }
}
