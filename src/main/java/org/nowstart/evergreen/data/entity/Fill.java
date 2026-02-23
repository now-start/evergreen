package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.MapsId;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Fill extends AuditableEntity {

    @EmbeddedId
    private FillKey id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @MapsId("orderId")
    private TradingOrder order;

    @Column(precision = 38, scale = 12)
    private BigDecimal fillQty;

    @Column(precision = 38, scale = 12)
    private BigDecimal fillPrice;

    @Column(precision = 38, scale = 12)
    private BigDecimal fee;

    @Embeddable
    @Getter
    @Setter
    @NoArgsConstructor(access = AccessLevel.PROTECTED)
    @AllArgsConstructor
    @EqualsAndHashCode
    public static class FillKey implements Serializable {

        private String orderId;

        private Instant filledAt;

        private String tradeUuid;
    }
}
