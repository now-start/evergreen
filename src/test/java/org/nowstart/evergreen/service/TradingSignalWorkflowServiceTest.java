package org.nowstart.evergreen.service;

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
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;

@ExtendWith(MockitoExtension.class)
class TradingSignalWorkflowServiceTest {

    @Mock
    private TradingOrderRepository tradingOrderRepository;
    @Mock
    private PositionRepository positionRepository;
    @Mock
    private TradingSignalMarketDataService tradingSignalMarketDataService;
    @Mock
    private TradingSignalComputationService tradingSignalComputationService;
    @Mock
    private TradingSignalMetricsService tradingSignalMetricsService;
    @Mock
    private TradingSignalStateService tradingSignalStateService;
    @Mock
    private TradingSignalOrderService tradingSignalOrderService;
    @Mock
    private TradingPositionSyncService tradingPositionSyncService;

    @Test
    void runOnce_syncsMarketsBeforeEvaluation() {
        TradingSignalWorkflowService service = createService(ExecutionMode.LIVE, List.of(" krw-btc ", "   "));

        when(tradingSignalMarketDataService.normalizeMarket(" krw-btc ")).thenReturn("KRW-BTC");
        when(tradingSignalMarketDataService.normalizeMarket("   ")).thenReturn("");
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-BTC")).thenReturn(List.of());
        when(tradingSignalMarketDataService.resolveSignalIndex(0)).thenReturn(-1);

        service.runOnce();

        verify(tradingPositionSyncService).syncPositions(List.of("KRW-BTC"));
        verify(tradingSignalMarketDataService).fetchDailyCandles("KRW-BTC");
    }

    @Test
    void runOnce_stopsWhenInitialSyncFails() {
        TradingSignalWorkflowService service = createService(ExecutionMode.LIVE, List.of("KRW-BTC"));

        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        org.mockito.Mockito.doThrow(new IllegalStateException("sync failed"))
                .when(tradingPositionSyncService)
                .syncPositions(List.of("KRW-BTC"));

        service.runOnce();

        verify(tradingPositionSyncService).syncPositions(List.of("KRW-BTC"));
        verify(tradingSignalMarketDataService, never()).fetchDailyCandles("KRW-BTC");
    }

    private TradingSignalWorkflowService createService(ExecutionMode mode, List<String> markets) {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                mode,
                markets,
                400,
                true,
                120,
                18,
                new BigDecimal("2.0"),
                new BigDecimal("3.0"),
                40,
                new BigDecimal("0.6"),
                new BigDecimal("0.01"),
                new BigDecimal("100000"),
                new BigDecimal("0.00000001")
        );

        return new TradingSignalWorkflowService(
                tradingOrderRepository,
                positionRepository,
                properties,
                tradingSignalMarketDataService,
                tradingSignalComputationService,
                tradingSignalMetricsService,
                tradingSignalStateService,
                tradingSignalOrderService,
                tradingPositionSyncService
        );
    }
}
