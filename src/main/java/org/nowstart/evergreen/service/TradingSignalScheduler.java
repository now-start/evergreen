package org.nowstart.evergreen.service;

import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.UpbitDayCandleResponse;
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
        log.info(
                "Trading scheduler tick started. mode={}, marketCount={}",
                tradingProperties.executionMode(),
                tradingProperties.markets().size()
        );
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
            log.info("Skipping signal evaluation due to insufficient candles. market={}, candles={}", market, candles.size());
            return;
        }

        if (hasActiveOrder(market)) {
            log.info("Skipping signal evaluation due to active order. market={}", market);
            return;
        }

        DayCandle signalCandle = candles.get(signalIndex);

        Optional<TradingPosition> positionOpt = positionRepository.findBySymbol(market);
        BigDecimal positionQty = positionOpt.map(TradingPosition::getQty)
                .filter(qty -> qty != null)
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

        if (buySignal) {
            log.info("Buy signal detected. market={}, signalTs={}, mode={}", market, signalCandle.timestamp(), tradingProperties.executionMode());
            submitBuySignal(market, signalCandle);
            return;
        }

        if (sellSignal) {
            log.info(
                    "Sell signal detected. market={}, signalTs={}, mode={}, trailStopTriggered={}",
                    market,
                    signalCandle.timestamp(),
                    tradingProperties.executionMode(),
                    trailStop.triggered()
            );
            submitSellSignal(market, signalCandle, positionQty);
            return;
        }

        log.info(
                "No trade signal. market={}, signalTs={}, hasPosition={}, prevRegime={}, currentRegime={}, trailStopTriggered={}",
                market,
                signalCandle.timestamp(),
                hasPosition,
                prevRegime,
                currentRegime,
                trailStop.triggered()
        );
    }

    private void submitBuySignal(String market, DayCandle signalCandle) {
        if (isDuplicateSignal(market, OrderSide.BUY, signalCandle.timestamp())) {
            log.info("Skipping duplicate buy signal. market={}, signalTs={}", market, signalCandle.timestamp());
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
            log.info("Skipping duplicate sell signal. market={}, signalTs={}", market, signalCandle.timestamp());
            return;
        }

        if (positionQty == null || positionQty.compareTo(tradingProperties.minPositionQty()) <= 0) {
            log.info(
                    "Skipping sell signal due to small position. market={}, positionQty={}, minPositionQty={}",
                    market,
                    positionQty,
                    tradingProperties.minPositionQty()
            );
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
        log.info(
                "Signal order submitted. market={}, side={}, signalTs={}, clientOrderId={}, status={}, mode={}",
                market,
                side,
                signalCandle.timestamp(),
                submittedOrder.clientOrderId(),
                submittedOrder.status(),
                submittedOrder.mode()
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

        DayCandle first = candles.get(0);
        DayCandle last = candles.get(candles.size() - 1);
        log.info(
                "Fetched daily candles. market={}, rawCount={}, validCount={}, firstTs={}, lastTs={}",
                market,
                rows.size(),
                candles.size(),
                first.timestamp(),
                last.timestamp()
        );
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
            log.info("Failed to parse day candle row. row={}", row, e);
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
}
