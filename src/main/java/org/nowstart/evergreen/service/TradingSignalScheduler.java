package org.nowstart.evergreen.service;

import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.UpbitDayCandleResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Slf4j
@Service
@RefreshScope
public class TradingSignalScheduler {

    private static final Duration MIN_CANDLE_SIGNAL_LOG_INTERVAL = Duration.ofMinutes(1);

    private static final List<OrderStatus> ACTIVE_ORDER_STATUSES = List.of(
            OrderStatus.CREATED,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED
    );

    private final TradingExecutionService tradingExecutionService;
    private final UpbitFeignClient upbitFeignClient;
    private final TradingOrderRepository tradingOrderRepository;
    private final PositionRepository positionRepository;
    private final TradingProperties tradingProperties;

    private final Map<String, Instant> lastSubmittedBuySignalByMarket = new ConcurrentHashMap<>();
    private final Map<String, Instant> lastSubmittedSellSignalByMarket = new ConcurrentHashMap<>();
    private final Map<String, CandleSignalLogState> lastLoggedCandleSignalByMarket = new ConcurrentHashMap<>();

    public TradingSignalScheduler(
            TradingExecutionService tradingExecutionService,
            UpbitFeignClient upbitFeignClient,
            TradingOrderRepository tradingOrderRepository,
            PositionRepository positionRepository,
            TradingProperties tradingProperties
    ) {
        this.tradingExecutionService = tradingExecutionService;
        this.upbitFeignClient = upbitFeignClient;
        this.tradingOrderRepository = tradingOrderRepository;
        this.positionRepository = positionRepository;
        this.tradingProperties = tradingProperties;
    }

    @Scheduled(fixedDelayString = "${evergreen.trading.interval:30s}")
    public void run() {
        for (String marketValue : tradingProperties.markets()) {
            String market = normalizeMarket(marketValue);
            if (market.isBlank()) {
                continue;
            }
            try {
                evaluateMarket(market);
            } catch (Exception e) {
                log.error("Failed to evaluate market={}", market, e);
            }
        }
    }

    private void evaluateMarket(String market) {
        List<DayCandle> candles = fetchDailyCandles(market);
        int signalIndex = resolveSignalIndex(candles.size());
        if (signalIndex < 1) {
            return;
        }

        if (hasActiveOrder(market)) {
            return;
        }

        DayCandle signalCandle = candles.get(signalIndex);

        Optional<TradingPosition> positionOpt = positionRepository.findBySymbol(market);
        BigDecimal positionQty = positionOpt.map(TradingPosition::getQty)
                .filter(qty -> qty != null)
                .orElse(BigDecimal.ZERO);
        BigDecimal positionAvgPrice = positionOpt.map(TradingPosition::getAvgPrice)
                .filter(avgPrice -> avgPrice != null)
                .orElse(BigDecimal.ZERO);
        boolean hasPosition = positionQty.compareTo(tradingProperties.minPositionQty()) > 0;

        double[] close = candles.stream().mapToDouble(c -> c.close().doubleValue()).toArray();
        double[] high = candles.stream().mapToDouble(c -> c.high().doubleValue()).toArray();
        double[] low = candles.stream().mapToDouble(c -> c.low().doubleValue()).toArray();

        double[] regimeAnchor = exponentialMovingAverage(close, tradingProperties.regimeEmaLen());
        double[] atr = wilderAtr(high, low, close, tradingProperties.atrPeriod());
        Regime[] regimes = resolveRegimes(close, regimeAnchor, tradingProperties.regimeBand().doubleValue());
        VolatilityResult volatility = resolveVolatilityStates(
                atr,
                close,
                tradingProperties.volRegimeLookback(),
                tradingProperties.volRegimeThreshold().doubleValue()
        );

        int prevIndex = signalIndex - 1;
        Regime prevRegime = regimes[prevIndex];
        Regime currentRegime = regimes[signalIndex];

        boolean baseBuy = prevRegime == Regime.BEAR && currentRegime == Regime.BULL;
        boolean baseSell = hasPosition && prevRegime == Regime.BULL && currentRegime == Regime.BEAR;

        double atrMultiplier = volatility.isHigh()[signalIndex]
                ? tradingProperties.atrMultHighVol().doubleValue()
                : tradingProperties.atrMultLowVol().doubleValue();

        TrailStopResult trailStop = evaluateTrailStop(candles, signalIndex, atr, atrMultiplier, positionOpt.orElse(null), hasPosition);
        boolean buySignal = !hasPosition && baseBuy;
        boolean sellSignal = hasPosition && (baseSell || trailStop.triggered());
        String signalReason = resolveSignalReason(buySignal, sellSignal, baseBuy, baseSell, trailStop.triggered());

        double regimeAnchorValue = regimeAnchor[signalIndex];
        double regimeUpperValue = Double.isFinite(regimeAnchorValue)
                ? regimeAnchorValue * (1.0 + tradingProperties.regimeBand().doubleValue())
                : Double.NaN;
        double regimeLowerValue = Double.isFinite(regimeAnchorValue)
                ? regimeAnchorValue * (1.0 - tradingProperties.regimeBand().doubleValue())
                : Double.NaN;
        ExecutionMetrics executionMetrics = resolveExecutionMetrics(market);
        double unrealizedReturnPct = resolveUnrealizedReturnPct(
                hasPosition,
                signalCandle.close().doubleValue(),
                positionAvgPrice.doubleValue()
        );
        SignalQualityStats signalQuality = resolveSignalQualityStats(close, regimes, signalIndex);
        double livePriceForLog = sanitizeMetricForLog(resolveLivePrice(market, signalCandle.close().doubleValue()));

        log.info(
                "event=ticker_price_v1 market={} ts={} live_price={} close={} regime={} buy_signal={} sell_signal={} signal_reason={}",
                market,
                signalCandle.timestamp(),
                livePriceForLog,
                signalCandle.close(),
                currentRegime,
                buySignal,
                sellSignal,
                signalReason
        );

        double unrealizedReturnPctForLog = sanitizeMetricForLog(unrealizedReturnPct);
        double realizedPnlKrwForLog = sanitizeMetricForLog(executionMetrics.realizedPnlKrw());
        double realizedReturnPctForLog = sanitizeMetricForLog(executionMetrics.realizedReturnPct());
        double maxDrawdownPctForLog = sanitizeMetricForLog(executionMetrics.maxDrawdownPct());
        double winRatePctForLog = sanitizeMetricForLog(executionMetrics.winRatePct());
        double avgWinPctForLog = sanitizeMetricForLog(executionMetrics.avgWinPct());
        double avgLossPctForLog = sanitizeMetricForLog(executionMetrics.avgLossPct());
        double rrRatioForLog = sanitizeMetricForLog(executionMetrics.rrRatio());
        double expectancyPctForLog = sanitizeMetricForLog(executionMetrics.expectancyPct());
        double signalQuality1dForLog = sanitizeMetricForLog(signalQuality.avg1dPct());
        double signalQuality3dForLog = sanitizeMetricForLog(signalQuality.avg3dPct());
        double signalQuality7dForLog = sanitizeMetricForLog(signalQuality.avg7dPct());
        double atrPriceRatioForLog = sanitizeMetricForLog(volatility.atrPriceRatio()[signalIndex]);
        double volPercentileForLog = sanitizeMetricForLog(volatility.percentile()[signalIndex]);

        String candleSignalLogDigest = String.join(
                "|",
                signalCandle.timestamp().toString(),
                currentRegime.name(),
                prevRegime.name(),
                Boolean.toString(hasPosition),
                positionQty.toPlainString(),
                positionAvgPrice.toPlainString(),
                Double.toString(unrealizedReturnPctForLog),
                Double.toString(realizedPnlKrwForLog),
                Double.toString(realizedReturnPctForLog),
                Double.toString(maxDrawdownPctForLog),
                Integer.toString(executionMetrics.tradeCount()),
                Double.toString(winRatePctForLog),
                Double.toString(avgWinPctForLog),
                Double.toString(avgLossPctForLog),
                Double.toString(rrRatioForLog),
                Double.toString(expectancyPctForLog),
                Double.toString(signalQuality1dForLog),
                Double.toString(signalQuality3dForLog),
                Double.toString(signalQuality7dForLog),
                Boolean.toString(volatility.isHigh()[signalIndex]),
                Double.toString(atrPriceRatioForLog),
                Double.toString(volPercentileForLog),
                Boolean.toString(buySignal),
                Boolean.toString(sellSignal),
                signalReason
        );

        if (shouldEmitCandleSignalLog(market, candleSignalLogDigest)) {
            log.info(
                    "event=candle_signal_v5 market={} ts={} close={} live_price={} regime={} prev_regime={} regime_anchor={} regime_upper={} regime_lower={} atr={} atr_trail_multiplier={} atr_trail_stop={} has_position={} position_qty={} position_avg_price={} unrealized_return_pct={} realized_pnl_krw={} realized_return_pct={} max_drawdown_pct={} trade_count={} trade_win_rate_pct={} trade_avg_win_pct={} trade_avg_loss_pct={} trade_rr_ratio={} trade_expectancy_pct={} signal_quality_1d_avg_pct={} signal_quality_3d_avg_pct={} signal_quality_7d_avg_pct={} volatility_is_high={} atr_price_ratio={} vol_percentile={} buy_signal={} sell_signal={} signal_reason={}",
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
        }

        if (buySignal) {
            submitBuySignal(market, signalCandle);
            return;
        }

        if (sellSignal) {
            submitSellSignal(market, signalCandle, positionQty);
            return;
        }
    }

    private void submitBuySignal(String market, DayCandle signalCandle) {
        if (isDuplicateSignal(market, OrderSide.BUY, signalCandle.timestamp())) {
            return;
        }

        BigDecimal paperQuantity = null;
        BigDecimal orderPrice = null;
        if (tradingProperties.executionMode() == ExecutionMode.PAPER) {
            BigDecimal signalOrderNotional = tradingProperties.signalOrderNotional();
            if (signalCandle.close().compareTo(BigDecimal.ZERO) <= 0) {
                log.warn("Skipping buy signal due to non-positive close price. market={}, close={}", market, signalCandle.close());
                return;
            }

            BigDecimal quantity = signalOrderNotional.divide(signalCandle.close(), 12, RoundingMode.DOWN);
            if (quantity.compareTo(BigDecimal.ZERO) <= 0) {
                log.warn(
                        "Skipping buy signal due to non-positive paper quantity. market={}, notional={}, close={}",
                        market,
                        signalOrderNotional,
                        signalCandle.close()
                );
                return;
            }
            paperQuantity = quantity;
            orderPrice = signalOrderNotional;
        }

        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                paperQuantity,
                orderPrice,
                tradingProperties.executionMode(),
                signalCandle.timestamp().toString()
        );

        submitSignal(market, signalCandle, OrderSide.BUY, request);
    }

    private void submitSellSignal(String market, DayCandle signalCandle, BigDecimal positionQty) {
        if (isDuplicateSignal(market, OrderSide.SELL, signalCandle.timestamp())) {
            return;
        }

        if (positionQty == null || positionQty.compareTo(tradingProperties.minPositionQty()) <= 0) {
            return;
        }

        BigDecimal paperPrice = tradingProperties.executionMode() == ExecutionMode.PAPER ? signalCandle.close() : null;

        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                positionQty,
                paperPrice,
                tradingProperties.executionMode(),
                signalCandle.timestamp().toString()
        );

        submitSignal(market, signalCandle, OrderSide.SELL, request);
    }

    private void submitSignal(String market, DayCandle signalCandle, OrderSide side, SignalExecuteRequest request) {
        OrderDto submittedOrder = tradingExecutionService.executeSignal(request);
        double executedPrice = submittedOrder.avgExecutedPrice() == null
                ? Double.NaN
                : submittedOrder.avgExecutedPrice().doubleValue();
        double signalClose = signalCandle.close().doubleValue();
        double slippagePct = resolveSlippagePct(signalClose, executedPrice);
        double slippageBps = Double.isFinite(slippagePct) ? slippagePct * 100.0 : Double.NaN;

        log.info(
                "event=trade_execution_v1 market={} side={} signal_ts={} signal_close={} client_order_id={} order_status={} mode={} executed_price={} executed_volume={} fee_amount={} slippage_pct={} slippage_bps={}",
                market,
                side,
                signalCandle.timestamp(),
                signalClose,
                submittedOrder.clientOrderId(),
                submittedOrder.status(),
                submittedOrder.mode(),
                executedPrice,
                submittedOrder.executedVolume(),
                submittedOrder.feeAmount(),
                slippagePct,
                slippageBps
        );
        recordSubmittedSignal(market, side, signalCandle.timestamp());
    }

    private boolean hasActiveOrder(String market) {
        return tradingOrderRepository.existsByModeAndSymbolAndStatusIn(
                tradingProperties.executionMode(),
                market,
                ACTIVE_ORDER_STATUSES
        );
    }

    private List<DayCandle> fetchDailyCandles(String market) {
        int required = Math.max(
                tradingProperties.candleCount(),
                Math.max(
                        Math.max(tradingProperties.regimeEmaLen(), tradingProperties.atrPeriod()),
                        tradingProperties.volRegimeLookback()
                ) + 2
        );

        List<UpbitDayCandleResponse> rows = upbitFeignClient.getDayCandles(market, required);
        if (rows == null || rows.isEmpty()) {
            log.warn("No daily candles received from exchange. market={}, requiredCount={}", market, required);
            return List.of();
        }

        List<DayCandle> candles = rows.stream()
                .map(this::toDayCandle)
                .filter(candle -> candle != null)
                .sorted(Comparator.comparing(DayCandle::timestamp))
                .toList();
        if (candles.isEmpty()) {
            log.warn("No valid daily candles after normalization. market={}, rawCount={}", market, rows.size());
            return candles;
        }
        return candles;
    }

    private DayCandle toDayCandle(UpbitDayCandleResponse row) {
        if (row == null
                || row.candle_date_time_utc() == null
                || row.high_price() == null
                || row.low_price() == null
                || row.trade_price() == null) {
            return null;
        }

        try {
            Instant timestamp = LocalDateTime.parse(row.candle_date_time_utc()).toInstant(ZoneOffset.UTC);
            return new DayCandle(
                    timestamp,
                    row.high_price(),
                    row.low_price(),
                    row.trade_price()
            );
        } catch (Exception e) {
            log.warn("Failed to parse day candle row. row={}", row, e);
            return null;
        }
    }

    private int resolveSignalIndex(int size) {
        int last = size - 1;
        int signalIndex = tradingProperties.closedCandleOnly() ? last - 1 : last;
        return Math.max(-1, signalIndex);
    }

    private double[] exponentialMovingAverage(double[] values, int length) {
        int n = values.length;
        double[] ema = fillNaN(n);
        if (length <= 0 || n < length) {
            return ema;
        }

        double seed = 0.0;
        for (int i = 0; i < length; i++) {
            seed += values[i];
        }
        ema[length - 1] = seed / length;

        double alpha = 2.0 / (length + 1.0);
        for (int i = length; i < n; i++) {
            ema[i] = (alpha * values[i]) + ((1.0 - alpha) * ema[i - 1]);
        }
        return ema;
    }

    private double[] wilderAtr(double[] high, double[] low, double[] close, int period) {
        int n = close.length;
        double[] atr = fillNaN(n);
        if (period <= 0 || n < period) {
            return atr;
        }

        double[] tr = new double[n];
        tr[0] = high[0] - low[0];
        for (int i = 1; i < n; i++) {
            double highLow = high[i] - low[i];
            double highPrevClose = Math.abs(high[i] - close[i - 1]);
            double lowPrevClose = Math.abs(low[i] - close[i - 1]);
            tr[i] = Math.max(highLow, Math.max(highPrevClose, lowPrevClose));
        }

        double total = 0.0;
        for (int i = 0; i < period; i++) {
            total += tr[i];
        }

        int first = period - 1;
        atr[first] = total / period;
        for (int i = period; i < n; i++) {
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period;
        }
        return atr;
    }

    private Regime[] resolveRegimes(double[] close, double[] anchor, double regimeBand) {
        int n = close.length;
        Regime[] regimes = new Regime[n];

        for (int i = 0; i < n; i++) {
            if (!Double.isFinite(anchor[i])) {
                regimes[i] = Regime.UNKNOWN;
                continue;
            }

            double upper = anchor[i] * (1.0 + regimeBand);
            double lower = anchor[i] * (1.0 - regimeBand);

            Regime previous = i == 0 ? Regime.UNKNOWN : regimes[i - 1];
            if (close[i] > upper) {
                regimes[i] = Regime.BULL;
            } else if (close[i] < lower) {
                regimes[i] = Regime.BEAR;
            } else if (previous != Regime.UNKNOWN) {
                regimes[i] = previous;
            } else if (close[i] > anchor[i]) {
                regimes[i] = Regime.BULL;
            } else if (close[i] < anchor[i]) {
                regimes[i] = Regime.BEAR;
            } else {
                regimes[i] = Regime.UNKNOWN;
            }
        }

        return regimes;
    }

    private VolatilityResult resolveVolatilityStates(double[] atr, double[] close, int lookback, double threshold) {
        int n = close.length;
        double[] ratio = fillNaN(n);
        double[] percentile = fillNaN(n);
        boolean[] high = new boolean[n];

        for (int i = 0; i < n; i++) {
            if (Double.isFinite(atr[i]) && Double.isFinite(close[i]) && close[i] > 0.0) {
                ratio[i] = atr[i] / close[i];
            }

            if (!Double.isFinite(ratio[i])) {
                continue;
            }

            int start = Math.max(0, i - lookback + 1);
            int count = 0;
            int belowOrEqual = 0;
            for (int j = start; j <= i; j++) {
                if (!Double.isFinite(ratio[j])) {
                    continue;
                }
                count++;
                if (ratio[j] <= ratio[i]) {
                    belowOrEqual++;
                }
            }

            if (count == 0) {
                continue;
            }

            percentile[i] = belowOrEqual / (double) count;
            high[i] = percentile[i] >= threshold;
        }

        return new VolatilityResult(ratio, percentile, high);
    }

    private TrailStopResult evaluateTrailStop(
            List<DayCandle> candles,
            int signalIndex,
            double[] atr,
            double atrMultiplier,
            TradingPosition position,
            boolean hasPosition
    ) {
        if (!hasPosition || atrMultiplier <= 0.0 || !Double.isFinite(atr[signalIndex])) {
            return new TrailStopResult(Double.NaN, false);
        }

        double highestCloseSinceEntry = resolveHighestCloseSinceEntry(candles, signalIndex, position);
        if (!Double.isFinite(highestCloseSinceEntry)) {
            return new TrailStopResult(Double.NaN, false);
        }

        double stop = highestCloseSinceEntry - (atrMultiplier * atr[signalIndex]);
        double currentClose = candles.get(signalIndex).close().doubleValue();
        return new TrailStopResult(stop, currentClose <= stop);
    }

    private double resolveHighestCloseSinceEntry(List<DayCandle> candles, int signalIndex, TradingPosition position) {
        int startIndex = 0;

        if (position != null && position.getUpdatedAt() != null) {
            LocalDate positionDate = position.getUpdatedAt().atOffset(ZoneOffset.UTC).toLocalDate();
            boolean found = false;
            for (int i = 0; i <= signalIndex; i++) {
                LocalDate candleDate = candles.get(i).timestamp().atOffset(ZoneOffset.UTC).toLocalDate();
                if (!candleDate.isBefore(positionDate)) {
                    startIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                startIndex = signalIndex;
            }
        }

        double highest = Double.NaN;
        for (int i = startIndex; i <= signalIndex; i++) {
            double close = candles.get(i).close().doubleValue();
            if (!Double.isFinite(highest) || close > highest) {
                highest = close;
            }
        }

        return highest;
    }

    private String resolveSignalReason(
            boolean buySignal,
            boolean sellSignal,
            boolean baseBuy,
            boolean baseSell,
            boolean trailStopTriggered
    ) {
        if (buySignal) {
            return "BUY_REGIME_TRANSITION";
        }
        if (sellSignal && baseSell && trailStopTriggered) {
            return "SELL_REGIME_AND_TRAIL_STOP";
        }
        if (sellSignal && trailStopTriggered) {
            return "SELL_TRAIL_STOP";
        }
        if (sellSignal) {
            return "SELL_REGIME_TRANSITION";
        }
        if (baseBuy) {
            return "SETUP_BUY";
        }
        if (baseSell) {
            return "SETUP_SELL";
        }
        return "NONE";
    }

    private double resolveUnrealizedReturnPct(boolean hasPosition, double closePrice, double avgPrice) {
        if (!hasPosition || !Double.isFinite(closePrice) || !Double.isFinite(avgPrice) || avgPrice <= 0.0) {
            return Double.NaN;
        }
        return ((closePrice / avgPrice) - 1.0) * 100.0;
    }

    private ExecutionMetrics resolveExecutionMetrics(String market) {
        List<TradingOrder> filledOrders = tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                market,
                tradingProperties.executionMode(),
                OrderStatus.FILLED
        );
        if (filledOrders.isEmpty()) {
            return ExecutionMetrics.empty();
        }

        double positionQty = 0.0;
        double avgCost = 0.0;
        double realizedPnlKrw = 0.0;
        double realizedCostKrw = 0.0;
        int tradeCount = 0;
        int winCount = 0;
        int lossCount = 0;
        double winSumPct = 0.0;
        double lossSumAbsPct = 0.0;

        double equityCurve = 1.0;
        double peakEquityCurve = 1.0;
        double maxDrawdownPct = 0.0;

        for (TradingOrder order : filledOrders) {
            if (order == null || order.getSide() == null) {
                continue;
            }

            double qty = toPositiveDouble(order.getExecutedVolume());
            double price = toPositiveDouble(order.getAvgExecutedPrice());
            double fee = toNonNegativeDouble(order.getFeeAmount());
            if (!Double.isFinite(qty) || !Double.isFinite(price) || qty <= 0.0 || price <= 0.0) {
                continue;
            }

            if (order.getSide() == OrderSide.BUY) {
                double newQty = positionQty + qty;
                if (newQty > 0.0) {
                    double currentCostBasis = avgCost * positionQty;
                    double buyCostWithFee = (price * qty) + fee;
                    avgCost = (currentCostBasis + buyCostWithFee) / newQty;
                    positionQty = newQty;
                }
                continue;
            }

            double sellQty = Math.min(positionQty, qty);
            if (sellQty <= 0.0 || avgCost <= 0.0) {
                continue;
            }

            double proceedsAfterFee = (price * sellQty) - fee;
            double costBasis = avgCost * sellQty;
            double pnl = proceedsAfterFee - costBasis;
            double retPct = costBasis > 0.0 ? (pnl / costBasis) * 100.0 : Double.NaN;

            realizedPnlKrw += pnl;
            realizedCostKrw += costBasis;

            if (Double.isFinite(retPct)) {
                tradeCount++;
                if (retPct > 0.0) {
                    winCount++;
                    winSumPct += retPct;
                } else {
                    lossCount++;
                    lossSumAbsPct += Math.abs(retPct);
                }

                equityCurve *= (1.0 + (retPct / 100.0));
                if (equityCurve > peakEquityCurve) {
                    peakEquityCurve = equityCurve;
                }
                if (peakEquityCurve > 0.0) {
                    double drawdown = ((equityCurve / peakEquityCurve) - 1.0) * 100.0;
                    if (drawdown < maxDrawdownPct) {
                        maxDrawdownPct = drawdown;
                    }
                }
            }

            positionQty = Math.max(0.0, positionQty - sellQty);
            if (positionQty == 0.0) {
                avgCost = 0.0;
            }
        }

        double realizedReturnPct = realizedCostKrw > 0.0
                ? (realizedPnlKrw / realizedCostKrw) * 100.0
                : Double.NaN;
        double winRatePct = tradeCount > 0 ? (winCount * 100.0) / tradeCount : Double.NaN;
        double avgWinPct = winCount > 0 ? winSumPct / winCount : Double.NaN;
        double avgLossPct = lossCount > 0 ? lossSumAbsPct / lossCount : Double.NaN;
        double rrRatio = (Double.isFinite(avgWinPct) && Double.isFinite(avgLossPct) && avgLossPct > 0.0)
                ? avgWinPct / avgLossPct
                : Double.NaN;
        double expectancyPct = (Double.isFinite(winRatePct) && Double.isFinite(avgWinPct) && Double.isFinite(avgLossPct))
                ? ((winRatePct / 100.0) * avgWinPct) - ((1.0 - (winRatePct / 100.0)) * avgLossPct)
                : Double.NaN;

        return new ExecutionMetrics(
                realizedPnlKrw,
                realizedReturnPct,
                maxDrawdownPct,
                tradeCount,
                winRatePct,
                avgWinPct,
                avgLossPct,
                rrRatio,
                expectancyPct
        );
    }

    private SignalQualityStats resolveSignalQualityStats(double[] close, Regime[] regimes, int signalIndex) {
        double sum1d = 0.0;
        double sum3d = 0.0;
        double sum7d = 0.0;
        int count1d = 0;
        int count3d = 0;
        int count7d = 0;

        for (int i = 1; i <= signalIndex; i++) {
            Regime prev = regimes[i - 1];
            Regime current = regimes[i];
            boolean buy = prev == Regime.BEAR && current == Regime.BULL;
            if (!buy || !Double.isFinite(close[i]) || close[i] <= 0.0) {
                continue;
            }

            if (i + 1 <= signalIndex && Double.isFinite(close[i + 1])) {
                sum1d += ((close[i + 1] / close[i]) - 1.0) * 100.0;
                count1d++;
            }
            if (i + 3 <= signalIndex && Double.isFinite(close[i + 3])) {
                sum3d += ((close[i + 3] / close[i]) - 1.0) * 100.0;
                count3d++;
            }
            if (i + 7 <= signalIndex && Double.isFinite(close[i + 7])) {
                sum7d += ((close[i + 7] / close[i]) - 1.0) * 100.0;
                count7d++;
            }
        }

        return new SignalQualityStats(
                count1d == 0 ? Double.NaN : sum1d / count1d,
                count3d == 0 ? Double.NaN : sum3d / count3d,
                count7d == 0 ? Double.NaN : sum7d / count7d
        );
    }

    private double resolveSlippagePct(double signalClose, double executedPrice) {
        if (!Double.isFinite(signalClose) || !Double.isFinite(executedPrice) || signalClose <= 0.0 || executedPrice <= 0.0) {
            return Double.NaN;
        }
        return ((executedPrice / signalClose) - 1.0) * 100.0;
    }

    private double resolveLivePrice(String market, double fallbackClose) {
        double fallback = (Double.isFinite(fallbackClose) && fallbackClose > 0.0) ? fallbackClose : Double.NaN;
        try {
            List<UpbitTickerResponse> tickers = upbitFeignClient.getTickers(market);
            if (tickers == null || tickers.isEmpty() || tickers.get(0) == null || tickers.get(0).trade_price() == null) {
                return fallback;
            }
            double tradePrice = tickers.get(0).trade_price().doubleValue();
            if (!Double.isFinite(tradePrice) || tradePrice <= 0.0) {
                return fallback;
            }
            return tradePrice;
        } catch (Exception e) {
            log.debug("Failed to resolve live price for market={}", market, e);
            return fallback;
        }
    }

    private double toPositiveDouble(BigDecimal value) {
        if (value == null) {
            return Double.NaN;
        }
        double v = value.doubleValue();
        return v > 0.0 ? v : Double.NaN;
    }

    private double toNonNegativeDouble(BigDecimal value) {
        if (value == null) {
            return 0.0;
        }
        double v = value.doubleValue();
        return Math.max(v, 0.0);
    }

    private boolean isDuplicateSignal(String market, OrderSide side, Instant signalTs) {
        Instant last = side == OrderSide.BUY
                ? lastSubmittedBuySignalByMarket.get(market)
                : lastSubmittedSellSignalByMarket.get(market);
        return signalTs.equals(last);
    }

    private void recordSubmittedSignal(String market, OrderSide side, Instant signalTs) {
        if (side == OrderSide.BUY) {
            lastSubmittedBuySignalByMarket.put(market, signalTs);
            return;
        }
        lastSubmittedSellSignalByMarket.put(market, signalTs);
    }

    private boolean shouldEmitCandleSignalLog(String market, String digest) {
        Instant now = Instant.now();
        CandleSignalLogState previous = lastLoggedCandleSignalByMarket.get(market);
        boolean changed = previous == null || !previous.digest().equals(digest);
        boolean intervalElapsed = previous == null
                || Duration.between(previous.loggedAt(), now).compareTo(MIN_CANDLE_SIGNAL_LOG_INTERVAL) >= 0;
        if (!changed && !intervalElapsed) {
            return false;
        }
        lastLoggedCandleSignalByMarket.put(market, new CandleSignalLogState(now, digest));
        return true;
    }

    private double sanitizeMetricForLog(double value) {
        if (!Double.isFinite(value)) {
            return 0.0;
        }
        return value == -0.0 ? 0.0 : value;
    }

    private String normalizeMarket(String value) {
        if (value == null) {
            return "";
        }
        return value.trim().toUpperCase(Locale.ROOT);
    }

    private double[] fillNaN(int size) {
        double[] values = new double[size];
        for (int i = 0; i < size; i++) {
            values[i] = Double.NaN;
        }
        return values;
    }

    private record DayCandle(
            Instant timestamp,
            BigDecimal high,
            BigDecimal low,
            BigDecimal close
    ) {
    }

    private enum Regime {
        BULL,
        BEAR,
        UNKNOWN
    }

    private record VolatilityResult(
            double[] atrPriceRatio,
            double[] percentile,
            boolean[] isHigh
    ) {
    }

    private record TrailStopResult(
            double stopPrice,
            boolean triggered
    ) {
    }

    private record SignalQualityStats(
            double avg1dPct,
            double avg3dPct,
            double avg7dPct
    ) {
    }

    private record ExecutionMetrics(
            double realizedPnlKrw,
            double realizedReturnPct,
            double maxDrawdownPct,
            int tradeCount,
            double winRatePct,
            double avgWinPct,
            double avgLossPct,
            double rrRatio,
            double expectancyPct
    ) {
        private static ExecutionMetrics empty() {
            return new ExecutionMetrics(
                    Double.NaN,
                    Double.NaN,
                    Double.NaN,
                    0,
                    Double.NaN,
                    Double.NaN,
                    Double.NaN,
                    Double.NaN,
                    Double.NaN
            );
        }
    }

    private record CandleSignalLogState(
            Instant loggedAt,
            String digest
    ) {
    }
}
