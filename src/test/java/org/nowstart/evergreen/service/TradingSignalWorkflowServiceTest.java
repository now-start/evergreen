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
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.service.strategy.StrategyRegistry;
import org.nowstart.evergreen.service.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyParams;
import org.nowstart.evergreen.service.strategy.core.StrategySignalDecision;
import org.nowstart.evergreen.service.strategy.v5.V5StrategyOverrides;

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

        StrategyParams v5Params = new V5StrategyOverrides(
                120,
                18,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                40,
                BigDecimal.valueOf(0.6),
                BigDecimal.valueOf(0.01)
        );
        when(strategyParamResolver.resolveActive())
                .thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", v5Params));
        when(strategyRegistry.evaluate(eq("v5"), anyList(), eq(1), any(PositionSnapshot.class), eq(v5Params)))
                .thenReturn(new StrategyEvaluation(
                        new StrategySignalDecision(false, true, "SELL_REGIME_TRANSITION"),
                        List.of(
                                StrategyDiagnostic.number("regime.anchor", "Regime Anchor", 90.0),
                                StrategyDiagnostic.number("regime.lower", "Regime Lower Band", 89.1)
                        )
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

    @Test
    void runOnce_returnsWhenAllMarketsBecomeBlankAfterNormalization() {
        TradingSignalWorkflowService service = createService(List.of(" ", "\t"));
        when(tradingSignalMarketDataService.normalizeMarket(" ")).thenReturn("");
        when(tradingSignalMarketDataService.normalizeMarket("\t")).thenReturn("");

        service.runOnce();

        verifyNoInteractions(tradingPositionSyncService);
        verifyNoInteractions(tradingSignalOrderService);
    }

    @Test
    void runOnce_continuesOtherMarketsWhenOneEvaluationThrows() {
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC", "KRW-ETH"));
        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        when(tradingSignalMarketDataService.normalizeMarket("KRW-ETH")).thenReturn("KRW-ETH");
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-BTC")).thenThrow(new IllegalStateException("broken candle feed"));
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-ETH")).thenReturn(List.of());
        when(tradingSignalMarketDataService.resolveSignalIndex(0)).thenReturn(-1);

        service.runOnce();

        verify(tradingSignalMarketDataService).fetchDailyCandles("KRW-BTC");
        verify(tradingSignalMarketDataService).fetchDailyCandles("KRW-ETH");
    }

    @Test
    void runOnce_submitsBuySignalAndUsesEmptyPositionSnapshotWhenNoPosition() {
        TradingSignalWorkflowService service = createService(List.of("KRW-BTC"));
        List<TradingDayCandleDto> candles = List.of(
                new TradingDayCandleDto(
                        Instant.parse("2026-02-20T00:00:00Z"),
                        new BigDecimal("90"),
                        new BigDecimal("91"),
                        new BigDecimal("89"),
                        new BigDecimal("90"),
                        new BigDecimal("1000")
                ),
                new TradingDayCandleDto(
                        Instant.parse("2026-02-21T00:00:00Z"),
                        new BigDecimal("110"),
                        new BigDecimal("111"),
                        new BigDecimal("109"),
                        new BigDecimal("110"),
                        new BigDecimal("1100")
                )
        );

        StrategyParams v5Params = new V5StrategyOverrides(
                120,
                18,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                40,
                BigDecimal.valueOf(0.6),
                BigDecimal.valueOf(0.01)
        );

        when(tradingSignalMarketDataService.normalizeMarket("KRW-BTC")).thenReturn("KRW-BTC");
        when(tradingSignalMarketDataService.fetchDailyCandles("KRW-BTC")).thenReturn(candles);
        when(tradingSignalMarketDataService.resolveSignalIndex(2)).thenReturn(1);
        when(tradingOrderGuardService.hasBlockingOrder("KRW-BTC")).thenReturn(false);
        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());
        when(strategyParamResolver.resolveActive())
                .thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", v5Params));
        when(strategyRegistry.evaluate(eq("v5"), anyList(), eq(1), any(PositionSnapshot.class), eq(v5Params)))
                .thenReturn(new StrategyEvaluation(
                        new StrategySignalDecision(true, false, "BUY_REGIME_TRANSITION"),
                        List.of(StrategyDiagnostic.number("regime.anchor", "Regime Anchor", 90.0))
                ));
        when(tradingSignalMetricsService.resolveExecutionMetrics("KRW-BTC")).thenReturn(TradingExecutionMetrics.empty());
        when(tradingSignalMarketDataService.resolveLivePrice("KRW-BTC", 110.0)).thenReturn(110.0);
        when(tradingSignalMetricsService.resolveUnrealizedReturnPct(false, 110.0, 0.0)).thenReturn(Double.NaN);

        service.runOnce();

        verify(tradingSignalOrderService).submitBuySignal("KRW-BTC", candles.get(1));
        verify(tradingSignalOrderService, never()).submitSellSignal(anyString(), any(), any());
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
