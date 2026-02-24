package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
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
import org.nowstart.evergreen.strategy.StrategyRegistry;
import org.nowstart.evergreen.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.strategy.core.StrategyParams;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalMarketDataService {

    private final UpbitFeignClient upbitFeignClient;
    private final TradingProperties tradingProperties;
    private final TradingStrategyParamResolver strategyParamResolver;
    private final StrategyRegistry strategyRegistry;

    public List<TradingDayCandleDto> fetchDailyCandles(String market) {
        String activeVersion = strategyParamResolver.resolveActiveStrategyVersion();
        StrategyParams strategyParams = strategyParamResolver.resolve(activeVersion);
        int strategyWarmup = strategyRegistry.requiredWarmupCandles(activeVersion, strategyParams);
        int required = Math.max(
                tradingProperties.candleCount(),
                strategyWarmup + 2
        );

        List<UpbitDayCandleResponse> rows = upbitFeignClient.getDayCandles(market, required);
        if (rows == null || rows.isEmpty()) {
            log.warn("No daily candles received from exchange. market={}, requiredCount={}", market, required);
            return List.of();
        }

        List<TradingDayCandleDto> candles = rows.stream()
                .map(this::toDayCandle)
                .filter(Objects::nonNull)
                .sorted(Comparator.comparing(TradingDayCandleDto::timestamp))
                .toList();
        if (candles.isEmpty()) {
            log.warn("No valid daily candles after normalization. market={}, rawCount={}", market, rows.size());
            return candles;
        }

        return candles;
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
            return new TradingDayCandleDto(
                    LocalDateTime.parse(row.candle_date_time_utc()).toInstant(ZoneOffset.UTC),
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
}
