package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.UpbitDayCandleResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.nowstart.evergreen.service.strategy.StrategyRegistry;
import org.nowstart.evergreen.service.strategy.TradingStrategyParamResolver;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalMarketDataService {

    private static final String DAILY_INTERVAL_KEY = "days";
    private static final String MINUTE_240_INTERVAL_KEY = "minute_240";
    private static final int MINUTE_240_UNIT = 240;
    private static final int MAX_CANDLE_PAGE_SIZE = 200;

    private final UpbitFeignClient upbitFeignClient;
    private final TradingProperties tradingProperties;
    private final TradingStrategyParamResolver strategyParamResolver;
    private final StrategyRegistry strategyRegistry;

    public List<TradingDayCandleDto> fetchDailyCandles(String market) {
        TradingStrategyParamResolver.ActiveStrategy activeStrategy = strategyParamResolver.resolveActive();
        int strategyWarmup = strategyRegistry.requiredWarmupCandles(
                activeStrategy.version(),
                activeStrategy.params()
        );
        int required = Math.max(
                tradingProperties.candleCount(),
                strategyWarmup + 2
        );
        String candleIntervalKey = resolveCandleIntervalKey(activeStrategy);

        List<UpbitDayCandleResponse> rows = fetchExchangeCandles(market, required, candleIntervalKey);
        if (rows == null || rows.isEmpty()) {
            log.warn(
                    "No candles received from exchange. market={}, interval={}, requiredCount={}",
                    market,
                    candleIntervalKey,
                    required
            );
            return List.of();
        }

        List<TradingDayCandleDto> candles = rows.stream()
                .map(this::toDayCandle)
                .filter(Objects::nonNull)
                .sorted(Comparator.comparing(TradingDayCandleDto::timestamp))
                .toList();
        if (candles.isEmpty()) {
            log.warn("No valid candles after normalization. market={}, rawCount={}", market, rows.size());
            return candles;
        }

        return candles;
    }

    private String resolveCandleIntervalKey(TradingStrategyParamResolver.ActiveStrategy activeStrategy) {
        String value = strategyRegistry.candleIntervalKey(
                activeStrategy.version(),
                activeStrategy.params()
        );
        if (value == null || value.isBlank()) {
            return DAILY_INTERVAL_KEY;
        }
        return value.trim().toLowerCase(Locale.ROOT).replace("-", "_");
    }

    private List<UpbitDayCandleResponse> fetchExchangeCandles(
            String market,
            int required,
            String candleIntervalKey
    ) {
        List<UpbitDayCandleResponse> rows = new ArrayList<>();
        String to = null;
        while (rows.size() < required) {
            int count = Math.min(MAX_CANDLE_PAGE_SIZE, required - rows.size());
            List<UpbitDayCandleResponse> batch = fetchExchangeCandlePage(market, count, candleIntervalKey, to);
            if (batch == null || batch.isEmpty()) {
                break;
            }
            rows.addAll(batch);
            if (batch.size() < count) {
                break;
            }
            String nextTo = resolveNextPageTo(batch);
            if (nextTo == null || nextTo.equals(to)) {
                break;
            }
            to = nextTo;
        }
        return rows;
    }

    private List<UpbitDayCandleResponse> fetchExchangeCandlePage(
            String market,
            int count,
            String candleIntervalKey,
            String to
    ) {
        if (MINUTE_240_INTERVAL_KEY.equals(candleIntervalKey)) {
            return upbitFeignClient.getMinuteCandles(MINUTE_240_UNIT, market, count, to);
        }
        if (DAILY_INTERVAL_KEY.equals(candleIntervalKey)
                || "day".equals(candleIntervalKey)
                || "daily".equals(candleIntervalKey)) {
            return upbitFeignClient.getDayCandles(market, count, to);
        }
        throw new IllegalArgumentException("Unsupported candle interval: " + candleIntervalKey);
    }

    private String resolveNextPageTo(List<UpbitDayCandleResponse> batch) {
        return batch.stream()
                .map(this::parseCandleTimestamp)
                .filter(Objects::nonNull)
                .min(Comparator.naturalOrder())
                .map(value -> value.minusSeconds(1).atZone(ZoneOffset.UTC).toLocalDateTime())
                .map(value -> value.format(DateTimeFormatter.ISO_LOCAL_DATE_TIME))
                .orElse(null);
    }

    public int resolveSignalIndex(int size) {
        int last = size - 1;
        int signalIndex = tradingProperties.closedCandleOnly() ? last - 1 : last;
        return Math.max(-1, signalIndex);
    }

    public double resolveLivePrice(String market, double fallbackClose) {
        double fallback = (Double.isFinite(fallbackClose) && fallbackClose > 0.0) ? fallbackClose : Double.NaN;
        try {
            List<UpbitTickerResponse> tickers = upbitFeignClient.getTickers(market);
            if (tickers == null || tickers.isEmpty() || tickers.getFirst() == null || tickers.getFirst().trade_price() == null) {
                return fallback;
            }

            double tradePrice = tickers.getFirst().trade_price().doubleValue();
            if (!Double.isFinite(tradePrice) || tradePrice <= 0.0) {
                return fallback;
            }

            return tradePrice;
        } catch (Exception e) {
            log.debug("Failed to resolve live price for market={}", market, e);
            return fallback;
        }
    }

    public String normalizeMarket(String value) {
        if (value == null) {
            return "";
        }
        return value.trim().toUpperCase(Locale.ROOT);
    }

    private TradingDayCandleDto toDayCandle(UpbitDayCandleResponse row) {
        if (row == null
                || row.candle_date_time_utc() == null
                || row.opening_price() == null
                || row.high_price() == null
                || row.low_price() == null
                || row.trade_price() == null) {
            return null;
        }

        try {
            java.time.Instant timestamp = parseCandleTimestamp(row);
            if (timestamp == null) {
                return null;
            }
            return new TradingDayCandleDto(
                    timestamp,
                    row.opening_price(),
                    row.high_price(),
                    row.low_price(),
                    row.trade_price(),
                    row.candle_acc_trade_volume() == null ? BigDecimal.ZERO : row.candle_acc_trade_volume()
            );
        } catch (Exception e) {
            log.warn("Failed to parse day candle row. row={}", row, e);
            return null;
        }
    }

    private java.time.Instant parseCandleTimestamp(UpbitDayCandleResponse row) {
        if (row == null || row.candle_date_time_utc() == null) {
            return null;
        }
        try {
            return LocalDateTime.parse(row.candle_date_time_utc()).toInstant(ZoneOffset.UTC);
        } catch (Exception e) {
            return null;
        }
    }
}
