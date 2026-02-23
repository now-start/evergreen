package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.TradeOrderType;

import java.math.BigDecimal;

@Entity
@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class TradingOrder extends AuditableEntity {

    @Id
    private String clientOrderId;

    private String symbol;

    private String exchangeOrderId;

    @Enumerated(EnumType.STRING)
    private OrderSide side;

    @Enumerated(EnumType.STRING)
    private TradeOrderType orderType;

    @Enumerated(EnumType.STRING)
    private ExecutionMode mode;

    @Column(precision = 38, scale = 12)
    private BigDecimal quantity;

    @Column(precision = 38, scale = 12)
    private BigDecimal price;

    @Column(precision = 38, scale = 12)
    private BigDecimal requestedNotional;

    @Column(precision = 38, scale = 12)
    private BigDecimal executedVolume;

    @Column(precision = 38, scale = 12)
    private BigDecimal avgExecutedPrice;

    @Column(precision = 38, scale = 12)
    private BigDecimal feeAmount;

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    private String reason;
}
