package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.UpbitDayCandleResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.nowstart.evergreen.service.strategy.StrategyRegistry;
import org.nowstart.evergreen.service.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.service.strategy.v5.V5StrategyOverrides;
import org.nowstart.evergreen.service.strategy.v6.V6StrategyOverrides;

@ExtendWith(MockitoExtension.class)
class TradingSignalMarketDataServiceTest {

    @Mock
    private UpbitFeignClient upbitFeignClient;
    @Mock
    private TradingStrategyParamResolver strategyParamResolver;
    @Mock
    private StrategyRegistry strategyRegistry;

    private TradingSignalMarketDataService service;

    @BeforeEach
    void setUp() {
        service = new TradingSignalMarketDataService(
                upbitFeignClient,
                properties(true),
                strategyParamResolver,
                strategyRegistry
        );
    }

    @Test
    void fetchDailyCandles_returnsEmptyWhenExchangeReturnsNoRows() {
        when(strategyParamResolver.resolveActive()).thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", params()));
        when(strategyRegistry.requiredWarmupCandles("v5", params())).thenReturn(10);
        when(upbitFeignClient.getDayCandles("KRW-BTC", 12, null)).thenReturn(null);

        List<TradingDayCandleDto> candles = service.fetchDailyCandles("KRW-BTC");

        assertThat(candles).isEmpty();
        verify(upbitFeignClient).getDayCandles("KRW-BTC", 12, null);
    }

    @Test
    void fetchDailyCandles_normalizesFiltersAndSortsRows() {
        when(strategyParamResolver.resolveActive()).thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", params()));
        when(strategyRegistry.requiredWarmupCandles("v5", params())).thenReturn(1);
        when(upbitFeignClient.getDayCandles("KRW-BTC", 5, null)).thenReturn(Arrays.asList(
                null,
                dayCandle(null, "1", "2", "0.5", "1.5", "10"),
                dayCandle("2026-02-02T00:00:00", null, "2", "0.5", "1.5", "10"),
                dayCandle("2026-02-02T00:00:00", "1", null, "0.5", "1.5", "10"),
                dayCandle("2026-02-02T00:00:00", "1", "2", null, "1.5", "10"),
                dayCandle("2026-02-02T00:00:00", "1", "2", "0.5", null, "10"),
                dayCandle("not-a-date", "1", "2", "0.5", "1.5", "10"),
                dayCandle("2026-02-03T00:00:00", "101", "103", "100", "102", null),
                dayCandle("2026-02-01T00:00:00", "99", "100", "98", "99.5", "321")
        ));

        List<TradingDayCandleDto> candles = service.fetchDailyCandles("KRW-BTC");

        assertThat(candles).hasSize(2);
        assertThat(candles.get(0).timestamp()).isEqualTo(Instant.parse("2026-02-01T00:00:00Z"));
        assertThat(candles.get(0).volume()).isEqualByComparingTo("321");
        assertThat(candles.get(1).timestamp()).isEqualTo(Instant.parse("2026-02-03T00:00:00Z"));
        assertThat(candles.get(1).volume()).isEqualByComparingTo(BigDecimal.ZERO);
    }

    @Test
    void fetchDailyCandles_returnsEmptyWhenAllRowsAreInvalid() {
        when(strategyParamResolver.resolveActive()).thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", params()));
        when(strategyRegistry.requiredWarmupCandles("v5", params())).thenReturn(1);
        when(upbitFeignClient.getDayCandles("KRW-BTC", 5, null)).thenReturn(List.of(
                dayCandle(null, "1", "2", "0.5", "1.5", "10"),
                dayCandle("bad", "1", "2", "0.5", "1.5", "10")
        ));

        List<TradingDayCandleDto> candles = service.fetchDailyCandles("KRW-BTC");

        assertThat(candles).isEmpty();
    }

    @Test
    void fetchDailyCandles_usesMinute240CandlesForV6() {
        V6StrategyOverrides params = v6Params();
        when(strategyParamResolver.resolveActive()).thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v6", params));
        when(strategyRegistry.requiredWarmupCandles("v6", params)).thenReturn(35);
        when(strategyRegistry.candleIntervalKey("v6", params)).thenReturn("minute_240");
        when(upbitFeignClient.getMinuteCandles(240, "KRW-BTC", 37, null)).thenReturn(List.of(
                dayCandle("2026-02-01T00:00:00", "100", "102", "99", "101", "10")
        ));

        List<TradingDayCandleDto> candles = service.fetchDailyCandles("KRW-BTC");

        assertThat(candles).hasSize(1);
        assertThat(candles.getFirst().timestamp()).isEqualTo(Instant.parse("2026-02-01T00:00:00Z"));
        verify(upbitFeignClient).getMinuteCandles(240, "KRW-BTC", 37, null);
    }

    @Test
    void fetchDailyCandles_paginatesWhenRequiredCountExceedsUpbitLimit() {
        when(strategyParamResolver.resolveActive()).thenReturn(new TradingStrategyParamResolver.ActiveStrategy("v5", params()));
        when(strategyRegistry.requiredWarmupCandles("v5", params())).thenReturn(250);
        when(upbitFeignClient.getDayCandles("KRW-BTC", 200, null))
                .thenReturn(candlePage("2026-02-01T00:00:00", 200));
        when(upbitFeignClient.getDayCandles("KRW-BTC", 52, "2026-01-31T23:59:59"))
                .thenReturn(candlePage("2025-12-11T00:00:00", 52));

        List<TradingDayCandleDto> candles = service.fetchDailyCandles("KRW-BTC");

        assertThat(candles).hasSize(252);
        verify(upbitFeignClient).getDayCandles("KRW-BTC", 200, null);
        verify(upbitFeignClient).getDayCandles("KRW-BTC", 52, "2026-01-31T23:59:59");
    }

    @Test
    void resolveSignalIndex_respectsClosedCandleOnlyFlag() {
        assertThat(service.resolveSignalIndex(3)).isEqualTo(1);
        assertThat(service.resolveSignalIndex(0)).isEqualTo(-1);

        TradingSignalMarketDataService openCandleService = new TradingSignalMarketDataService(
                upbitFeignClient,
                properties(false),
                strategyParamResolver,
                strategyRegistry
        );
        assertThat(openCandleService.resolveSignalIndex(3)).isEqualTo(2);
    }

    @Test
    void resolveLivePrice_returnsFallbackForInvalidTickerData() {
        when(upbitFeignClient.getTickers("KRW-BTC")).thenReturn(
                null,
                List.of(),
                Collections.singletonList(null),
                List.of(new UpbitTickerResponse("KRW-BTC", null)),
                List.of(new UpbitTickerResponse("KRW-BTC", BigDecimal.ZERO))
        );

        assertThat(service.resolveLivePrice("KRW-BTC", 123.45)).isEqualTo(123.45);
        assertThat(service.resolveLivePrice("KRW-BTC", 123.45)).isEqualTo(123.45);
        assertThat(service.resolveLivePrice("KRW-BTC", 123.45)).isEqualTo(123.45);
        assertThat(service.resolveLivePrice("KRW-BTC", 123.45)).isEqualTo(123.45);
        assertThat(service.resolveLivePrice("KRW-BTC", 123.45)).isEqualTo(123.45);
    }

    @Test
    void resolveLivePrice_returnsTradePriceWhenTickerIsValid() {
        when(upbitFeignClient.getTickers("KRW-BTC")).thenReturn(List.of(new UpbitTickerResponse("KRW-BTC", new BigDecimal("98765432.1"))));

        double livePrice = service.resolveLivePrice("KRW-BTC", 123.45);

        assertThat(livePrice).isEqualTo(98765432.1);
    }

    @Test
    void resolveLivePrice_returnsFallbackWhenExchangeThrows() {
        when(upbitFeignClient.getTickers("KRW-BTC")).thenThrow(new IllegalStateException("network"));

        double fromFiniteFallback = service.resolveLivePrice("KRW-BTC", 200.0);
        double fromInvalidFallback = service.resolveLivePrice("KRW-BTC", 0.0);

        assertThat(fromFiniteFallback).isEqualTo(200.0);
        assertThat(fromInvalidFallback).isNaN();
    }

    @Test
    void normalizeMarket_handlesNullAndTrimming() {
        assertThat(service.normalizeMarket(null)).isEqualTo("");
        assertThat(service.normalizeMarket(" krw-btc ")).isEqualTo("KRW-BTC");
    }

    private TradingProperties properties(boolean closedCandleOnly) {
        return new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.LIVE,
                List.of("KRW-BTC"),
                5,
                closedCandleOnly,
                new BigDecimal("100000"),
                "v5"
        );
    }

    private V5StrategyOverrides params() {
        return new V5StrategyOverrides(
                120,
                14,
                new BigDecimal("2.0"),
                new BigDecimal("3.0"),
                40,
                new BigDecimal("0.6"),
                new BigDecimal("0.01")
        );
    }

    private V6StrategyOverrides v6Params() {
        return new V6StrategyOverrides(
                new BigDecimal("0.124825"),
                new BigDecimal("0.055"),
                new BigDecimal("0.25")
        );
    }

    private UpbitDayCandleResponse dayCandle(
            String ts,
            String open,
            String high,
            String low,
            String close,
            String volume
    ) {
        return new UpbitDayCandleResponse(
                ts,
                decimal(open),
                decimal(high),
                decimal(low),
                decimal(close),
                decimal(volume)
        );
    }

    private List<UpbitDayCandleResponse> candlePage(String oldestTs, int count) {
        LocalDateTime oldest = LocalDateTime.parse(oldestTs);
        List<UpbitDayCandleResponse> rows = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            rows.add(dayCandle(
                    oldest.plusDays(i).format(DateTimeFormatter.ISO_LOCAL_DATE_TIME),
                    "100",
                    "102",
                    "99",
                    "101",
                    "10"
            ));
        }
        Collections.reverse(rows);
        return rows;
    }

    private BigDecimal decimal(String value) {
        return value == null ? null : new BigDecimal(value);
    }
}
