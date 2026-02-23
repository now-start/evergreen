package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.OffsetDateTime;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.entity.Fill;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.FillRepository;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class OrderReconciliationService {

    private final TradingOrderRepository tradingOrderRepository;
    private final FillRepository fillRepository;
    private final PositionRepository positionRepository;

    @Transactional
    public TradingOrder reconcile(TradingOrder order, UpbitOrderResponse response) {
        BigDecimal previousExecutedVolume = safe(order.getExecutedVolume());
        BigDecimal latestExecutedVolume = parseDecimal(response.executed_volume());

        order.setExchangeOrderId(response.uuid());
        order.setExecutedVolume(latestExecutedVolume);
        order.setFeeAmount(parseDecimal(response.paid_fee()));
        order.setAvgExecutedPrice(calculateAvgPrice(response));
        order.setStatus(mapStatus(response.state(), order.getExecutedVolume()));
        tradingOrderRepository.save(order);

        BigDecimal deltaQtyFromNewFills = BigDecimal.ZERO;
        BigDecimal deltaFundsFromNewFills = BigDecimal.ZERO;

        if (response.trades() != null) {
            for (int index = 0; index < response.trades().size(); index++) {
                UpbitOrderResponse.UpbitTrade trade = response.trades().get(index);
                Instant filledAt = parseTimestamp(trade.created_at());
                String tradeUuid = resolveTradeUuid(order, trade, index);
                Fill.FillKey fillKey = new Fill.FillKey(order.getClientOrderId(), filledAt, tradeUuid);
                if (fillRepository.existsById(fillKey)) {
                    continue;
                }

                Fill fill = Fill.builder()
                        .id(fillKey)
                        .order(order)
                        .fillQty(parseDecimal(trade.volume()))
                        .fillPrice(parseDecimal(trade.price()))
                        .fee(BigDecimal.ZERO)
                        .build();
                fillRepository.save(fill);

                BigDecimal tradeQty = parseDecimal(trade.volume());
                BigDecimal tradeFunds = parseDecimal(trade.funds());
                if (tradeFunds.compareTo(BigDecimal.ZERO) <= 0) {
                    tradeFunds = parseDecimal(trade.price()).multiply(tradeQty);
                }
                deltaQtyFromNewFills = deltaQtyFromNewFills.add(tradeQty);
                deltaFundsFromNewFills = deltaFundsFromNewFills.add(tradeFunds);
            }
        }

        BigDecimal deltaExecutedVolume = latestExecutedVolume.subtract(previousExecutedVolume);
        if (deltaExecutedVolume.compareTo(BigDecimal.ZERO) > 0) {
            BigDecimal deltaPriceToApply = deltaQtyFromNewFills.compareTo(BigDecimal.ZERO) > 0
                    ? deltaFundsFromNewFills.divide(deltaQtyFromNewFills, 12, RoundingMode.HALF_UP)
                    : safe(order.getAvgExecutedPrice());
            applyPositionDelta(order, deltaExecutedVolume, deltaPriceToApply);
        }

        return order;
    }

    private void applyPositionDelta(TradingOrder order, BigDecimal deltaQty, BigDecimal deltaPrice) {
        if (deltaQty.compareTo(BigDecimal.ZERO) <= 0) {
            return;
        }

        TradingPosition position = positionRepository.findBySymbol(order.getSymbol()).orElseGet(() -> TradingPosition.builder()
                .symbol(order.getSymbol())
                .qty(BigDecimal.ZERO)
                .avgPrice(BigDecimal.ZERO)
                .state(PositionState.FLAT)
                .build());

        if (order.getSide() == OrderSide.BUY) {
            BigDecimal oldQty = safe(position.getQty());
            BigDecimal oldAvg = safe(position.getAvgPrice());
            BigDecimal newQty = oldQty.add(deltaQty);
            if (newQty.compareTo(BigDecimal.ZERO) > 0) {
                BigDecimal weighted = oldAvg.multiply(oldQty).add(deltaPrice.multiply(deltaQty));
                position.setAvgPrice(weighted.divide(newQty, 12, RoundingMode.HALF_UP));
            }
            position.setQty(newQty);
            position.setState(newQty.compareTo(BigDecimal.ZERO) > 0 ? PositionState.LONG : PositionState.FLAT);
        } else {
            BigDecimal newQty = safe(position.getQty()).subtract(deltaQty);
            if (newQty.compareTo(BigDecimal.ZERO) <= 0) {
                position.setQty(BigDecimal.ZERO);
                position.setAvgPrice(BigDecimal.ZERO);
                position.setState(PositionState.FLAT);
            } else {
                position.setQty(newQty);
                position.setState(PositionState.LONG);
            }
        }

        positionRepository.save(position);
    }

    private OrderStatus mapStatus(String state, BigDecimal executedVolume) {
        if ("done".equalsIgnoreCase(state)) {
            return OrderStatus.FILLED;
        }
        if ("cancel".equalsIgnoreCase(state)) {
            return OrderStatus.CANCELED;
        }
        if (safe(executedVolume).compareTo(BigDecimal.ZERO) > 0) {
            return OrderStatus.PARTIALLY_FILLED;
        }
        return OrderStatus.SUBMITTED;
    }

    private BigDecimal calculateAvgPrice(UpbitOrderResponse response) {
        if (response.trades() == null || response.trades().isEmpty()) {
            return parseDecimal(response.price());
        }

        BigDecimal totalFunds = BigDecimal.ZERO;
        BigDecimal totalVolume = BigDecimal.ZERO;
        for (UpbitOrderResponse.UpbitTrade trade : response.trades()) {
            totalFunds = totalFunds.add(parseDecimal(trade.funds()));
            totalVolume = totalVolume.add(parseDecimal(trade.volume()));
        }

        if (totalVolume.compareTo(BigDecimal.ZERO) <= 0) {
            return parseDecimal(response.price());
        }
        return totalFunds.divide(totalVolume, 12, RoundingMode.HALF_UP);
    }

    private Instant parseTimestamp(String value) {
        if (value == null || value.isBlank()) {
            return Instant.EPOCH;
        }
        try {
            return OffsetDateTime.parse(value).toInstant();
        } catch (Exception _) {
            return Instant.EPOCH;
        }
    }

    private String resolveTradeUuid(TradingOrder order, UpbitOrderResponse.UpbitTrade trade, int index) {
        if (trade.uuid() != null && !trade.uuid().isBlank()) {
            return trade.uuid();
        }

        String createdAt = trade.created_at() == null ? "" : trade.created_at();
        String price = trade.price() == null ? "" : trade.price();
        String volume = trade.volume() == null ? "" : trade.volume();
        return order.getClientOrderId() + ":" + createdAt + ":" + price + ":" + volume + ":" + index;
    }

    private BigDecimal parseDecimal(String value) {
        if (value == null || value.isBlank()) {
            return BigDecimal.ZERO;
        }
        return new BigDecimal(value);
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
