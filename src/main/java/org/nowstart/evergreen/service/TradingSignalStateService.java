package org.nowstart.evergreen.service;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.nowstart.evergreen.data.type.OrderSide;
import org.springframework.stereotype.Service;

@Service
public class TradingSignalStateService {

    private final Map<String, Instant> lastSubmittedBuySignalByMarket = new ConcurrentHashMap<>();
    private final Map<String, Instant> lastSubmittedSellSignalByMarket = new ConcurrentHashMap<>();

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

}
