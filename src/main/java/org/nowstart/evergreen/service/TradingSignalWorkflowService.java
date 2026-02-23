package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.dto.TradingSignalTrailStopResult;
import org.nowstart.evergreen.data.dto.TradingSignalVolatilityResult;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.repository.PositionRepository;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalWorkflowService {

    private final TradingProperties tradingProperties;
    private final TradingSignalMarketDataService tradingSignalMarketDataService;
    private final TradingSignalComputationService tradingSignalComputationService;
    private final TradingSignalMetricsService tradingSignalMetricsService;
    private final TradingSignalOrderService tradingSignalOrderService;
    private final TradingPositionSyncService tradingPositionSyncService;
    private final TradingOrderGuardService tradingOrderGuardService;
    private final PositionRepository positionRepository;
    private final TradingSignalLogService tradingSignalLogService;

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

        double[] close = candles.stream().mapToDouble(c -> c.close().doubleValue()).toArray();
        double[] high = candles.stream().mapToDouble(c -> c.high().doubleValue()).toArray();
        double[] low = candles.stream().mapToDouble(c -> c.low().doubleValue()).toArray();

        double[] regimeAnchor = tradingSignalComputationService.exponentialMovingAverage(close, tradingProperties.regimeEmaLen());
        double[] atr = tradingSignalComputationService.wilderAtr(high, low, close, tradingProperties.atrPeriod());
        MarketRegime[] regimes = tradingSignalComputationService.resolveRegimes(
                close,
                regimeAnchor,
                tradingProperties.regimeBand().doubleValue()
        );
        TradingSignalVolatilityResult volatility = tradingSignalComputationService.resolveVolatilityStates(
                atr,
                close,
                tradingProperties.volRegimeLookback(),
                tradingProperties.volRegimeThreshold().doubleValue()
        );

        int prevIndex = signalIndex - 1;
        MarketRegime prevRegime = regimes[prevIndex];
        MarketRegime currentRegime = regimes[signalIndex];

        boolean baseBuy = prevRegime == MarketRegime.BEAR
                && currentRegime == MarketRegime.BULL;
        boolean baseSell = hasPosition
                && prevRegime == MarketRegime.BULL
                && currentRegime == MarketRegime.BEAR;

        double atrMultiplier = volatility.isHigh()[signalIndex]
                ? tradingProperties.atrMultHighVol().doubleValue()
                : tradingProperties.atrMultLowVol().doubleValue();

        TradingSignalTrailStopResult trailStop = tradingSignalComputationService.evaluateTrailStop(
                candles,
                signalIndex,
                atr,
                atrMultiplier,
                totalPosition,
                hasPosition
        );
        boolean buySignal = !hasPosition && baseBuy;
        boolean sellSignal = hasPosition && (baseSell || trailStop.triggered());
        String signalReason = tradingSignalComputationService.resolveSignalReason(
                buySignal,
                sellSignal,
                baseBuy,
                baseSell,
                trailStop.triggered()
        );

        double regimeAnchorValue = regimeAnchor[signalIndex];
        double regimeUpperValue = Double.isFinite(regimeAnchorValue)
                ? regimeAnchorValue * (1.0 + tradingProperties.regimeBand().doubleValue())
                : Double.NaN;
        double regimeLowerValue = Double.isFinite(regimeAnchorValue)
                ? regimeAnchorValue * (1.0 - tradingProperties.regimeBand().doubleValue())
                : Double.NaN;

        TradingExecutionMetrics executionMetrics = tradingSignalMetricsService.resolveExecutionMetrics(market);
        double livePrice = tradingSignalMarketDataService.resolveLivePrice(market, signalCandle.close().doubleValue());
        double unrealizedReturnPct = tradingSignalMetricsService.resolveUnrealizedReturnPct(
                hasPosition,
                livePrice,
                totalAvgPrice.doubleValue()
        );

        TradingSignalQualityStats signalQuality = tradingSignalComputationService.resolveSignalQualityStats(
                close,
                regimes,
                signalIndex
        );

        tradingSignalLogService.logTicker(
                market,
                signalCandle,
                livePrice,
                currentRegime,
                buySignal,
                sellSignal,
                signalReason
        );
        tradingSignalLogService.logCandleSignal(new TradingSignalLogService.TradingSignalLogContext(
                market,
                signalCandle,
                livePrice,
                currentRegime,
                prevRegime,
                regimeAnchorValue,
                regimeUpperValue,
                regimeLowerValue,
                atr[signalIndex],
                atrMultiplier,
                trailStop.stopPrice(),
                hasPosition,
                sellableQty,
                totalAvgPrice,
                totalQty,
                unrealizedReturnPct,
                executionMetrics,
                signalQuality,
                volatility,
                signalIndex,
                buySignal,
                sellSignal,
                signalReason
        ));

        if (buySignal) {
            tradingSignalOrderService.submitBuySignal(market, signalCandle);
            return;
        }

        if (sellSignal) {
            tradingSignalOrderService.submitSellSignal(market, signalCandle, sellableQty);
        }
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
