package org.nowstart.evergreen.service;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.dto.TradingSignalTrailStopResult;
import org.nowstart.evergreen.data.dto.TradingSignalVolatilityResult;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.PositionRepository;

@ExtendWith(MockitoExtension.class)
class TradingSignalWorkflowServiceTest {

    @Mock
    private TradingSignalMarketDataService tradingSignalMarketDataService;
    @Mock
    private TradingSignalComputationService tradingSignalComputationService;
    @Mock
    private TradingSignalMetricsService tradingSignalMetricsService;
    @Mock
    private TradingSignalOrderService tradingSignalOrderService;
    @Mock
    private TradingPositionSyncService tradingPositionSyncService;
    @Mock
    private TradingOrderGuardService tradingOrderGuardService;
    @Mock
    private PositionRepository positionRepository;
    @Mock
    private TradingSignalLogService tradingSignalLogService;

    @Test
    void runOnce_syncsMarketsBeforeEvaluation() {
        TradingSignalWorkflowService service = createService(List.of(" krw-btc ", "   "));

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
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC"));

        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        org.mockito.Mockito.doThrow(new IllegalStateException("sync failed"))
                .when(tradingPositionSyncService)
                .syncPositions(List.of("KRW-BTC"));

        service.runOnce();

        verify(tradingPositionSyncService).syncPositions(List.of("KRW-BTC"));
        verify(tradingSignalMarketDataService, never()).fetchDailyCandles("KRW-BTC");
    }

    @Test
    void runOnce_usesSellableQtyForSellSignal() {
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC"));
        List<TradingDayCandleDto> candles = List.of(
                new TradingDayCandleDto(Instant.parse("2026-02-20T00:00:00Z"), new BigDecimal("101"), new BigDecimal("99"), new BigDecimal("100")),
                new TradingDayCandleDto(Instant.parse("2026-02-21T00:00:00Z"), new BigDecimal("92"), new BigDecimal("88"), new BigDecimal("90"))
        );

        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-BTC")).thenReturn(candles);
        when(tradingSignalMarketDataService.resolveSignalIndex(2)).thenReturn(1);
        when(tradingSignalMarketDataService.resolveLivePrice("KRW-BTC", 90.0)).thenReturn(90.0);
        when(tradingOrderGuardService.hasBlockingOrder("KRW-BTC")).thenReturn(false);
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(java.util.Optional.of(TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("0.40"))
                .avgPrice(new BigDecimal("101"))
                .state(PositionState.LONG)
                .build()));

        when(tradingSignalComputationService.exponentialMovingAverage(any(), eq(120))).thenReturn(new double[] {100.0, 95.0});
        when(tradingSignalComputationService.wilderAtr(any(), any(), any(), eq(18))).thenReturn(new double[] {1.0, 1.0});
        when(tradingSignalComputationService.resolveRegimes(any(), any(), anyDouble())).thenReturn(
                new MarketRegime[] {MarketRegime.BULL, MarketRegime.BEAR});
        when(tradingSignalComputationService.resolveVolatilityStates(any(), any(), eq(40), anyDouble()))
                .thenReturn(new TradingSignalVolatilityResult(new double[] {0.01, 0.01}, new double[] {0.2, 0.2}, new boolean[] {false, false}));
        when(tradingSignalComputationService.evaluateTrailStop(any(), eq(1), any(), eq(2.0), any(), eq(true)))
                .thenReturn(new TradingSignalTrailStopResult(Double.NaN, false));
        when(tradingSignalComputationService.resolveSignalReason(false, true, false, true, false))
                .thenReturn("SELL_REGIME_TRANSITION");
        when(tradingSignalComputationService.resolveSignalQualityStats(any(), any(), eq(1)))
                .thenReturn(new TradingSignalQualityStats(Double.NaN, Double.NaN, Double.NaN));
        when(tradingSignalMetricsService.resolveExecutionMetrics("KRW-BTC")).thenReturn(TradingExecutionMetrics.empty());

        service.runOnce();

        verify(tradingSignalOrderService).submitSellSignal("KRW-BTC", candles.get(1), new BigDecimal("0.40"));
        verify(tradingSignalOrderService, never()).submitBuySignal(any(), any());
    }

    @Test
    void runOnce_blocksSignalWhenGuardServiceBlocksMarket() {
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC"));
        List<TradingDayCandleDto> candles = List.of(
                new TradingDayCandleDto(Instant.parse("2026-02-20T00:00:00Z"), new BigDecimal("101"), new BigDecimal("99"), new BigDecimal("100")),
                new TradingDayCandleDto(Instant.parse("2026-02-21T00:00:00Z"), new BigDecimal("92"), new BigDecimal("88"), new BigDecimal("90"))
        );

        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-BTC")).thenReturn(candles);
        when(tradingSignalMarketDataService.resolveSignalIndex(2)).thenReturn(1);
        when(tradingOrderGuardService.hasBlockingOrder("KRW-BTC")).thenReturn(true);

        service.runOnce();

        verifyNoInteractions(tradingSignalOrderService);
    }

    private TradingSignalWorkflowService createService(List<String> markets) {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.LIVE,
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
                new BigDecimal("100000")
        );

        return new TradingSignalWorkflowService(
                properties,
                tradingSignalMarketDataService,
                tradingSignalComputationService,
                tradingSignalMetricsService,
                tradingSignalOrderService,
                tradingPositionSyncService,
                tradingOrderGuardService,
                positionRepository,
                tradingSignalLogService
        );
    }
}
