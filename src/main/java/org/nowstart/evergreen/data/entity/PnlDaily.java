package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Embeddable;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDate;

@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
public class PnlDaily extends AuditableEntity {

    @EmbeddedId
    private PnlDailyKey id;

    private BigDecimal equity;

    private BigDecimal mdd;

    private BigDecimal cagrSnapshot;

    @Embeddable
    public record PnlDailyKey(String symbol, LocalDate pnlDate) implements Serializable {}
}
