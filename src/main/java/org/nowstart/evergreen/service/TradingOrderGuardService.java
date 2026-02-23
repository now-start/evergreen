package org.nowstart.evergreen.service;

import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
public class TradingOrderGuardService {

    public static final String GUARD_REASON_NONE = "NONE";
    public static final String GUARD_REASON_LOCAL_ACTIVE_ORDER = "LOCAL_ACTIVE_ORDER";
    public static final String GUARD_REASON_EXTERNAL_OPEN_ORDER = "EXTERNAL_OPEN_ORDER";
    public static final String GUARD_REASON_GUARD_QUERY_FAILED = "GUARD_QUERY_FAILED";

    private static final List<OrderStatus> ACTIVE_ORDER_STATUSES = List.of(
            OrderStatus.CREATED,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED
    );

    private final TradingOrderRepository tradingOrderRepository;
    private final UpbitFeignClient upbitFeignClient;
    private final TradingProperties tradingProperties;

    public boolean hasBlockingOrder(String market) {
        return evaluate(market).blocked();
    }

    public GuardDecision evaluate(String market) {
        boolean hasLocalActiveOrder = tradingOrderRepository.existsByModeAndSymbolAndStatusIn(
                tradingProperties.executionMode(),
                market,
                ACTIVE_ORDER_STATUSES
        );
        if (hasLocalActiveOrder) {
            return new GuardDecision(true, GUARD_REASON_LOCAL_ACTIVE_ORDER, false, 0);
        }

        if (tradingProperties.executionMode() != ExecutionMode.LIVE) {
            return new GuardDecision(false, GUARD_REASON_NONE, false, 0);
        }

        try {
            List<UpbitOrderResponse> openOrders = upbitFeignClient.getOpenOrders(market, "wait");
            int openOrderCount = openOrders == null ? 0 : openOrders.size();
            boolean blocked = openOrderCount > 0;
            if (blocked) {
                log.warn("event=external_order_guard market={} blocked=true reason=open_order_detected open_order_count={}", market, openOrderCount);
                return new GuardDecision(true, GUARD_REASON_EXTERNAL_OPEN_ORDER, true, openOrderCount);
            }

            return new GuardDecision(false, GUARD_REASON_NONE, false, 0);
        } catch (Exception e) {
            log.warn("event=external_order_guard market={} blocked=true reason=guard_query_failed", market, e);
            return new GuardDecision(true, GUARD_REASON_GUARD_QUERY_FAILED, false, 0);
        }
    }

    public record GuardDecision(
            boolean blocked,
            String reason,
            boolean hasExternalOpenOrder,
            int externalOpenOrderCount
    ) {
    }
}
