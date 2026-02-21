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
    void shouldEmitCandleSignalLog_suppressesUnchangedDigestWithinInterval() {
        boolean first = stateService.shouldEmitCandleSignalLog("KRW-BTC", "digest-1");
        boolean second = stateService.shouldEmitCandleSignalLog("KRW-BTC", "digest-1");

        assertThat(first).isTrue();
        assertThat(second).isFalse();
    }

    @Test
    void shouldEmitCandleSignalLog_emitsWhenDigestChanges() {
        stateService.shouldEmitCandleSignalLog("KRW-BTC", "digest-1");

        boolean emitted = stateService.shouldEmitCandleSignalLog("KRW-BTC", "digest-2");

        assertThat(emitted).isTrue();
    }
}
