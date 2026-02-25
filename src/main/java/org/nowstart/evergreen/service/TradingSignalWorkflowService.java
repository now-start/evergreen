package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.service.strategy.StrategyRegistry;
import org.nowstart.evergreen.service.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalWorkflowService {

    private final TradingProperties tradingProperties;
    private final TradingSignalMarketDataService tradingSignalMarketDataService;
    private final TradingSignalMetricsService tradingSignalMetricsService;
    private final TradingSignalOrderService tradingSignalOrderService;
    private final TradingPositionSyncService tradingPositionSyncService;
    private final TradingOrderGuardService tradingOrderGuardService;
    private final PositionRepository positionRepository;
    private final TradingSignalLogService tradingSignalLogService;
    private final TradingStrategyParamResolver strategyParamResolver;
    private final StrategyRegistry strategyRegistry;

    public void runOnce() {
        List<String> markets = tradingProperties.markets().stream()
                .map(tradingSignalMarketDataService::normalizeMarket)
                .filter(market -> !market.isBlank())
                .distinct()
                .toList();

        if (markets.isEmpty()) {
            return;
        }

        try {
            tradingPositionSyncService.syncPositions(markets);
        } catch (Exception e) {
            log.error("Failed to sync exchange positions. Skipping signal evaluation for this cycle.", e);
            return;
        }

        for (String market : markets) {
            try {
                evaluateMarket(market);
            } catch (Exception e) {
                log.error("Failed to evaluate market={}", market, e);
            }
        }
    }

    private void evaluateMarket(String market) {
        List<TradingDayCandleDto> candles = tradingSignalMarketDataService.fetchDailyCandles(market);
        int signalIndex = tradingSignalMarketDataService.resolveSignalIndex(candles.size());
        if (signalIndex < 1) {
            return;
        }

        if (tradingOrderGuardService.hasBlockingOrder(market)) {
            return;
        }

        TradingDayCandleDto signalCandle = candles.get(signalIndex);
        TradingPosition totalPosition = positionRepository.findBySymbol(market).orElse(null);
        BigDecimal totalQty = safe(totalPosition == null ? null : totalPosition.getQty());
        BigDecimal totalAvgPrice = safe(totalPosition == null ? null : totalPosition.getAvgPrice());
        BigDecimal sellableQty = totalQty.compareTo(BigDecimal.ZERO) < 0 ? BigDecimal.ZERO : totalQty;
        boolean hasPosition = sellableQty.compareTo(BigDecimal.ZERO) > 0;

        TradingStrategyParamResolver.ActiveStrategy activeStrategy = strategyParamResolver.resolveActive();
        StrategyEvaluation strategyEvaluation = strategyRegistry.evaluate(
                activeStrategy.version(),
                candles.stream().map(this::toOhlcv).toList(),
                signalIndex,
                toPositionSnapshot(totalPosition, sellableQty, totalAvgPrice),
                activeStrategy.params()
        );

        boolean buySignal = strategyEvaluation.decision().buySignal();
        boolean sellSignal = strategyEvaluation.decision().sellSignal();

        TradingExecutionMetrics executionMetrics = tradingSignalMetricsService.resolveExecutionMetrics(market);
        double livePrice = tradingSignalMarketDataService.resolveLivePrice(market, signalCandle.close().doubleValue());
        double unrealizedReturnPct = tradingSignalMetricsService.resolveUnrealizedReturnPct(
                hasPosition,
                livePrice,
                totalAvgPrice.doubleValue()
        );

        tradingSignalLogService.logCandleSignal(new TradingSignalLogService.TradingSignalLogContext(
                market,
                activeStrategy.version(),
                signalCandle,
                livePrice,
                hasPosition,
                sellableQty,
                totalAvgPrice,
                totalQty,
                unrealizedReturnPct,
                executionMetrics,
                strategyEvaluation
        ));

        if (buySignal) {
            tradingSignalOrderService.submitBuySignal(market, signalCandle);
            return;
        }

        if (sellSignal) {
            tradingSignalOrderService.submitSellSignal(market, signalCandle, sellableQty);
        }
    }

    private OhlcvCandle toOhlcv(TradingDayCandleDto candle) {
        return new OhlcvCandle(
                candle.timestamp(),
                safe(candle.open()).doubleValue(),
                safe(candle.high()).doubleValue(),
                safe(candle.low()).doubleValue(),
                safe(candle.close()).doubleValue(),
                safe(candle.volume()).doubleValue()
        );
    }

    private PositionSnapshot toPositionSnapshot(TradingPosition position, BigDecimal qty, BigDecimal avgPrice) {
        if (position == null) {
            return PositionSnapshot.EMPTY;
        }
        return new PositionSnapshot(
                safe(qty).doubleValue(),
                safe(avgPrice).doubleValue(),
                position.getUpdatedAt()
        );
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
