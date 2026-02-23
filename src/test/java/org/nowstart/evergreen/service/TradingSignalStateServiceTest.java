package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.type.OrderSide;

class TradingSignalStateServiceTest {

    private final TradingSignalStateService stateService = new TradingSignalStateService();

    @Test
    void isDuplicateSignal_returnsTrueForSameBuySignalTimestamp() {
        Instant ts = Instant.parse("2026-02-20T00:00:00Z");

        stateService.recordSubmittedSignal("KRW-BTC", OrderSide.BUY, ts);

        assertThat(stateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, ts)).isTrue();
    }

    @Test
    void isDuplicateSignal_returnsFalseForDifferentTimestamp() {
        Instant ts = Instant.parse("2026-02-20T00:00:00Z");
        Instant next = Instant.parse("2026-02-20T00:01:00Z");

        stateService.recordSubmittedSignal("KRW-BTC", OrderSide.BUY, ts);

        assertThat(stateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, next)).isFalse();
    }

    @Test
    void isDuplicateSignal_tracksBuyAndSellSeparately() {
        Instant ts = Instant.parse("2026-02-20T00:00:00Z");

        stateService.recordSubmittedSignal("KRW-BTC", OrderSide.BUY, ts);
        stateService.recordSubmittedSignal("KRW-BTC", OrderSide.SELL, ts);

        assertThat(stateService.isDuplicateSignal("KRW-BTC", OrderSide.BUY, ts)).isTrue();
        assertThat(stateService.isDuplicateSignal("KRW-BTC", OrderSide.SELL, ts)).isTrue();
    }
}
