package org.nowstart.evergreen.service;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.nowstart.evergreen.data.type.OrderSide;
import org.springframework.stereotype.Service;

@Service
public class TradingSignalStateService {

    private static final Duration MIN_CANDLE_SIGNAL_LOG_INTERVAL = Duration.ofMinutes(1);

    private final Map<String, Instant> lastSubmittedBuySignalByMarket = new ConcurrentHashMap<>();
    private final Map<String, Instant> lastSubmittedSellSignalByMarket = new ConcurrentHashMap<>();
    private final Map<String, CandleSignalLogState> lastLoggedCandleSignalByMarket = new ConcurrentHashMap<>();

    public boolean isDuplicateSignal(String market, OrderSide side, Instant signalTs) {
        Instant last = side == OrderSide.BUY
                ? lastSubmittedBuySignalByMarket.get(market)
                : lastSubmittedSellSignalByMarket.get(market);
        return signalTs.equals(last);
    }

    public void recordSubmittedSignal(String market, OrderSide side, Instant signalTs) {
        if (side == OrderSide.BUY) {
            lastSubmittedBuySignalByMarket.put(market, signalTs);
            return;
        }
        lastSubmittedSellSignalByMarket.put(market, signalTs);
    }

    public boolean shouldEmitCandleSignalLog(String market, String digest) {
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

    private record CandleSignalLogState(
            Instant loggedAt,
            String digest
    ) {
    }
}
