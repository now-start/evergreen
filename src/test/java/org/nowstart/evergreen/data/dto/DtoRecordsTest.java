package org.nowstart.evergreen.data.dto;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class DtoRecordsTest {

    @Test
    void balanceDto_accessors() {
        BalanceDto dto = new BalanceDto(
                "KRW",
                new BigDecimal("100.5"),
                new BigDecimal("1.2"),
                new BigDecimal("90000000"),
                "KRW"
        );

        assertThat(dto.currency()).isEqualTo("KRW");
        assertThat(dto.balance()).isEqualByComparingTo("100.5");
        assertThat(dto.locked()).isEqualByComparingTo("1.2");
        assertThat(dto.avgBuyPrice()).isEqualByComparingTo("90000000");
        assertThat(dto.unitCurrency()).isEqualTo("KRW");
    }

    @Test
    void orderChanceDto_accessors() {
        OrderChanceDto dto = new OrderChanceDto(
                "KRW-BTC",
                new BigDecimal("0.0005"),
                new BigDecimal("0.0005"),
                new BigDecimal("100000"),
                new BigDecimal("0.3"),
                new BigDecimal("1000000")
        );

        assertThat(dto.market()).isEqualTo("KRW-BTC");
        assertThat(dto.bidFee()).isEqualByComparingTo("0.0005");
        assertThat(dto.askFee()).isEqualByComparingTo("0.0005");
        assertThat(dto.bidBalance()).isEqualByComparingTo("100000");
        assertThat(dto.askBalance()).isEqualByComparingTo("0.3");
        assertThat(dto.maxTotal()).isEqualByComparingTo("1000000");
    }

    @Test
    void upbitOrderChanceResponse_deserializesStringMaxTotal() throws Exception {
        ObjectMapper objectMapper = new ObjectMapper();

        UpbitOrderChanceResponse response = objectMapper.readValue("""
                {
                  "bid_fee": "0.0005",
                  "ask_fee": "0.0005",
                  "bid_account": {
                    "currency": "KRW",
                    "balance": "1000000",
                    "locked": "0",
                    "avg_buy_price": "0"
                  },
                  "ask_account": {
                    "currency": "BTC",
                    "balance": "0.1",
                    "locked": "0",
                    "avg_buy_price": "90000000"
                  },
                  "market": {
                    "id": "KRW-BTC",
                    "name": "BTC/KRW",
                    "bid": {
                      "currency": "KRW",
                      "min_total": "5000"
                    },
                    "max_total": "1000000000"
                  }
                }
                """, UpbitOrderChanceResponse.class);

        assertThat(response.market().max_total()).isEqualTo("1000000000");
        assertThat(response.market().bid().min_total()).isEqualTo("5000");
    }

    @Test
    void upbitCreateOrderRequest_omitsNullFields() throws Exception {
        ObjectMapper objectMapper = new ObjectMapper();
        UpbitCreateOrderRequest request = new UpbitCreateOrderRequest(
                "KRW-BTC",
                "bid",
                "price",
                null,
                "100000",
                "client-order-id"
        );

        String json = objectMapper.writeValueAsString(request);

        assertThat(json).contains("\"market\":\"KRW-BTC\"");
        assertThat(json).contains("\"price\":\"100000\"");
        assertThat(json).doesNotContain("volume");
    }

    @Test
    void tradingSignalQualityStats_accessors() {
        TradingSignalQualityStats stats = new TradingSignalQualityStats(1.1, 2.2, 3.3);

        assertThat(stats.avg1dPct()).isEqualTo(1.1);
        assertThat(stats.avg3dPct()).isEqualTo(2.2);
        assertThat(stats.avg7dPct()).isEqualTo(3.3);
    }

    @Test
    void tradingSignalTrailStopResult_accessors() {
        TradingSignalTrailStopResult result = new TradingSignalTrailStopResult(95.4, true);

        assertThat(result.stopPrice()).isEqualTo(95.4);
        assertThat(result.triggered()).isTrue();
    }

    @Test
    void tradingSignalVolatilityResult_accessors() {
        double[] ratio = new double[] {0.01, 0.02};
        double[] percentile = new double[] {0.4, 0.9};
        boolean[] high = new boolean[] {false, true};
        TradingSignalVolatilityResult result = new TradingSignalVolatilityResult(ratio, percentile, high);

        assertThat(result.atrPriceRatio()).containsExactly(0.01, 0.02);
        assertThat(result.percentile()).containsExactly(0.4, 0.9);
        assertThat(result.isHigh()).containsExactly(false, true);
    }

    @Test
    void upbitDayCandleResponse_accessors() {
        UpbitDayCandleResponse response = new UpbitDayCandleResponse(
                "2026-01-01T00:00:00",
                new BigDecimal("100"),
                new BigDecimal("110"),
                new BigDecimal("90"),
                new BigDecimal("105"),
                new BigDecimal("12345.67")
        );

        assertThat(response.candle_date_time_utc()).isEqualTo("2026-01-01T00:00:00");
        assertThat(response.opening_price()).isEqualByComparingTo("100");
        assertThat(response.high_price()).isEqualByComparingTo("110");
        assertThat(response.low_price()).isEqualByComparingTo("90");
        assertThat(response.trade_price()).isEqualByComparingTo("105");
        assertThat(response.candle_acc_trade_volume()).isEqualByComparingTo("12345.67");
    }
}
