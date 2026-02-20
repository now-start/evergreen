package org.nowstart.evergreen.service;

import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.entity.Fill;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.FillRepository;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.UUID;

@Slf4j
@Service
@RefreshScope
public class PaperExecutionService {

    private final TradingOrderRepository tradingOrderRepository;
    private final FillRepository fillRepository;
    private final PositionRepository positionRepository;
    private final TradingProperties tradingProperties;

    public PaperExecutionService(
            TradingOrderRepository tradingOrderRepository,
            FillRepository fillRepository,
            PositionRepository positionRepository,
            TradingProperties tradingProperties
    ) {
        this.tradingOrderRepository = tradingOrderRepository;
        this.fillRepository = fillRepository;
        this.positionRepository = positionRepository;
        this.tradingProperties = tradingProperties;
    }

    @Transactional
    public TradingOrder execute(TradingOrder order) {
        BigDecimal execPrice = order.getPrice() == null ? BigDecimal.ZERO : order.getPrice();
        BigDecimal execQty = order.getQuantity() == null ? BigDecimal.ZERO : order.getQuantity();

        if (order.getQuantity() == null && order.getRequestedNotional() != null && execPrice.compareTo(BigDecimal.ZERO) > 0) {
            execQty = order.getRequestedNotional().divide(execPrice, 12, RoundingMode.DOWN);
        }

        BigDecimal fee = execPrice
                .multiply(execQty)
                .multiply(tradingProperties.feeRate());

        order.setExecutedVolume(execQty);
        order.setAvgExecutedPrice(execPrice);
        order.setFeeAmount(fee);
        order.setStatus(OrderStatus.FILLED);
        tradingOrderRepository.save(order);

        Fill fill = Fill.builder()
                .id(new Fill.FillKey(order.getClientOrderId(), Instant.now(), UUID.randomUUID().toString()))
                .order(order)
                .fillQty(execQty)
                .fillPrice(execPrice)
                .fee(fee)
                .build();
        fillRepository.save(fill);

        upsertPosition(order.getSymbol(), order.getSide(), execQty, execPrice);
        log.info(
                "Paper execution completed. clientOrderId={}, market={}, side={}, executedQty={}, executedPrice={}, fee={}",
                order.getClientOrderId(),
                order.getSymbol(),
                order.getSide(),
                execQty,
                execPrice,
                fee
        );

        return order;
    }

    private void upsertPosition(String symbol, OrderSide side, BigDecimal qty, BigDecimal price) {
        TradingPosition position = positionRepository.findBySymbol(symbol).orElseGet(() -> TradingPosition.builder()
                .symbol(symbol)
                .qty(BigDecimal.ZERO)
                .avgPrice(BigDecimal.ZERO)
                .state(PositionState.FLAT)
                .build());

        if (side == OrderSide.BUY) {
            BigDecimal oldQty = position.getQty() == null ? BigDecimal.ZERO : position.getQty();
            BigDecimal oldAvg = position.getAvgPrice() == null ? BigDecimal.ZERO : position.getAvgPrice();
            BigDecimal newQty = oldQty.add(qty);

            if (newQty.compareTo(BigDecimal.ZERO) > 0) {
                BigDecimal weighted = oldAvg.multiply(oldQty).add(price.multiply(qty));
                position.setAvgPrice(weighted.divide(newQty, 12, RoundingMode.HALF_UP));
            }

            position.setQty(newQty);
            position.setState(newQty.compareTo(BigDecimal.ZERO) > 0 ? PositionState.LONG : PositionState.FLAT);
        } else {
            BigDecimal oldQty = position.getQty() == null ? BigDecimal.ZERO : position.getQty();
            BigDecimal newQty = oldQty.subtract(qty);
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
        log.info(
                "Paper position updated. market={}, side={}, qty={}, avgPrice={}, state={}",
                symbol,
                side,
                position.getQty(),
                position.getAvgPrice(),
                position.getState()
        );
    }
}
