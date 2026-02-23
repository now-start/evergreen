package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;
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
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalWorkflowService {

    private static final List<OrderStatus> ACTIVE_ORDER_STATUSES = List.of(
            OrderStatus.CREATED,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED
    );

    private final TradingOrderRepository tradingOrderRepository;
    private final PositionRepository positionRepository;
    private final TradingProperties tradingProperties;
    private final TradingSignalMarketDataService tradingSignalMarketDataService;
    private final TradingSignalComputationService tradingSignalComputationService;
    private final TradingSignalMetricsService tradingSignalMetricsService;
    private final TradingSignalOrderService tradingSignalOrderService;
    private final TradingPositionSyncService tradingPositionSyncService;

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

        if (hasActiveOrder(market)) {
            return;
        }

        TradingDayCandleDto signalCandle = candles.get(signalIndex);

        Optional<TradingPosition> positionOpt = positionRepository.findBySymbol(market);
        BigDecimal positionQty = positionOpt.map(TradingPosition::getQty)
                .orElse(BigDecimal.ZERO);
        BigDecimal positionAvgPrice = positionOpt.map(TradingPosition::getAvgPrice)
                .orElse(BigDecimal.ZERO);
        boolean hasPosition = positionQty.compareTo(tradingProperties.minPositionQty()) > 0;

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
                positionOpt.orElse(null),
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
                positionAvgPrice.doubleValue()
        );
        TradingSignalQualityStats signalQuality = tradingSignalComputationService.resolveSignalQualityStats(
                close,
                regimes,
                signalIndex
        );

        double livePriceForLog = tradingSignalMetricsService.sanitizeMetricForLog(livePrice);
        log.info(
                "event=ticker_price market={} ts={} live_price={} close={} regime={} buy_signal={} sell_signal={} signal_reason={}",
                market,
                signalCandle.timestamp(),
                livePriceForLog,
                signalCandle.close(),
                currentRegime,
                buySignal,
                sellSignal,
                signalReason
        );

        double unrealizedReturnPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(unrealizedReturnPct);
        double realizedPnlKrwForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.realizedPnlKrw());
        double realizedReturnPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.realizedReturnPct());
        double maxDrawdownPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.maxDrawdownPct());
        double winRatePctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.winRatePct());
        double avgWinPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.avgWinPct());
        double avgLossPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.avgLossPct());
        double rrRatioForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.rrRatio());
        double expectancyPctForLog = tradingSignalMetricsService.sanitizeMetricForLog(executionMetrics.expectancyPct());
        double signalQuality1dForLog = tradingSignalMetricsService.sanitizeMetricForLog(signalQuality.avg1dPct());
        double signalQuality3dForLog = tradingSignalMetricsService.sanitizeMetricForLog(signalQuality.avg3dPct());
        double signalQuality7dForLog = tradingSignalMetricsService.sanitizeMetricForLog(signalQuality.avg7dPct());
        double atrPriceRatioForLog = tradingSignalMetricsService.sanitizeMetricForLog(volatility.atrPriceRatio()[signalIndex]);
        double volPercentileForLog = tradingSignalMetricsService.sanitizeMetricForLog(volatility.percentile()[signalIndex]);

        log.info(
                "event=candle_signal market={} ts={} close={} live_price={} regime={} prev_regime={} regime_anchor={} regime_upper={} regime_lower={} atr={} atr_trail_multiplier={} atr_trail_stop={} has_position={} position_qty={} position_avg_price={} unrealized_return_pct={} realized_pnl_krw={} realized_return_pct={} max_drawdown_pct={} trade_count={} trade_win_rate_pct={} trade_avg_win_pct={} trade_avg_loss_pct={} trade_rr_ratio={} trade_expectancy_pct={} signal_quality_1d_avg_pct={} signal_quality_3d_avg_pct={} signal_quality_7d_avg_pct={} volatility_is_high={} atr_price_ratio={} vol_percentile={} buy_signal={} sell_signal={} signal_reason={}",
                market,
                signalCandle.timestamp(),
                signalCandle.close(),
                livePriceForLog,
                currentRegime,
                prevRegime,
                regimeAnchorValue,
                regimeUpperValue,
                regimeLowerValue,
                atr[signalIndex],
                atrMultiplier,
                trailStop.stopPrice(),
                hasPosition,
                positionQty,
                positionAvgPrice,
                unrealizedReturnPctForLog,
                realizedPnlKrwForLog,
                realizedReturnPctForLog,
                maxDrawdownPctForLog,
                executionMetrics.tradeCount(),
                winRatePctForLog,
                avgWinPctForLog,
                avgLossPctForLog,
                rrRatioForLog,
                expectancyPctForLog,
                signalQuality1dForLog,
                signalQuality3dForLog,
                signalQuality7dForLog,
                volatility.isHigh()[signalIndex],
                atrPriceRatioForLog,
                volPercentileForLog,
                buySignal,
                sellSignal,
                signalReason
        );

        if (buySignal) {
            tradingSignalOrderService.submitBuySignal(market, signalCandle);
            return;
        }

        if (sellSignal) {
            tradingSignalOrderService.submitSellSignal(market, signalCandle, positionQty);
        }
    }

    private boolean hasActiveOrder(String market) {
        return tradingOrderRepository.existsByModeAndSymbolAndStatusIn(
                tradingProperties.executionMode(),
                market,
                ACTIVE_ORDER_STATUSES
        );
    }
}
