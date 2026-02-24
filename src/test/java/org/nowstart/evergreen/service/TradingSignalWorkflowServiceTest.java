package org.nowstart.evergreen.service;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
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
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.strategy.StrategyRegistry;
import org.nowstart.evergreen.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.strategy.core.StrategyParams;
import org.nowstart.evergreen.strategy.core.StrategySignalDecision;
import org.nowstart.evergreen.strategy.v5.V5StrategyOverrides;

@ExtendWith(MockitoExtension.class)
class TradingSignalWorkflowServiceTest {

    @Mock
    private TradingSignalMarketDataService tradingSignalMarketDataService;
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
    @Mock
    private TradingStrategyParamResolver strategyParamResolver;
    @Mock
    private StrategyRegistry strategyRegistry;

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
                new TradingDayCandleDto(
                        Instant.parse("2026-02-20T00:00:00Z"),
                        new BigDecimal("100"),
                        new BigDecimal("101"),
                        new BigDecimal("99"),
                        new BigDecimal("100"),
                        new BigDecimal("1000")
                ),
                new TradingDayCandleDto(
                        Instant.parse("2026-02-21T00:00:00Z"),
                        new BigDecimal("95"),
                        new BigDecimal("92"),
                        new BigDecimal("88"),
                        new BigDecimal("90"),
                        new BigDecimal("1100")
                )
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

        when(strategyParamResolver.resolveActiveStrategyVersion()).thenReturn("v5");
        StrategyParams v5Params = V5StrategyOverrides.of(120, 18, 2.0, 3.0, 40, 0.6, 0.01);
        when(strategyParamResolver.resolve("v5")).thenReturn(v5Params);
        when(strategyRegistry.evaluate(eq("v5"), anyList(), eq(1), any(PositionSnapshot.class), eq(v5Params)))
                .thenReturn(new StrategyEvaluation(
                        new StrategySignalDecision(false, true, "SELL_REGIME_TRANSITION"),
                        MarketRegime.BULL,
                        MarketRegime.BEAR,
                        95.0,
                        95.95,
                        94.05,
                        1.0,
                        2.0,
                        Double.NaN,
                        false,
                        false,
                        0.01,
                        0.2,
                        new TradingSignalQualityStats(Double.NaN, Double.NaN, Double.NaN)
                ));
        when(tradingSignalMetricsService.resolveExecutionMetrics("KRW-BTC")).thenReturn(TradingExecutionMetrics.empty());

        service.runOnce();

        verify(tradingSignalOrderService).submitSellSignal("KRW-BTC", candles.get(1), new BigDecimal("0.40"));
        verify(tradingSignalOrderService, never()).submitBuySignal(anyString(), any());
    }

    @Test
    void runOnce_blocksSignalWhenGuardServiceBlocksMarket() {
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC"));
        List<TradingDayCandleDto> candles = List.of(
                new TradingDayCandleDto(
                        Instant.parse("2026-02-20T00:00:00Z"),
                        new BigDecimal("100"),
                        new BigDecimal("101"),
                        new BigDecimal("99"),
                        new BigDecimal("100"),
                        new BigDecimal("1000")
                ),
                new TradingDayCandleDto(
                        Instant.parse("2026-02-21T00:00:00Z"),
                        new BigDecimal("95"),
                        new BigDecimal("92"),
                        new BigDecimal("88"),
                        new BigDecimal("90"),
                        new BigDecimal("1100")
                )
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
                new BigDecimal("100000"),
                "v5"
        );

        return new TradingSignalWorkflowService(
                properties,
                tradingSignalMarketDataService,
                tradingSignalMetricsService,
                tradingSignalOrderService,
                tradingPositionSyncService,
                tradingOrderGuardService,
                positionRepository,
                tradingSignalLogService,
                strategyParamResolver,
                strategyRegistry
        );
    }
}
