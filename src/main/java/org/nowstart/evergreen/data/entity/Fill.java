package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Embeddable;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.MapsId;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.Instant;

@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
public class Fill extends AuditableEntity {

    @EmbeddedId
    private FillKey id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @MapsId("orderId")
    private TradingOrder order;

    private BigDecimal fillQty;

    private BigDecimal fillPrice;

    private BigDecimal fee;

    @Embeddable
    public record FillKey(String orderId, Instant filledAt) implements Serializable {}
}
