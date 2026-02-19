package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.nowstart.evergreen.data.type.RegimeState;

import java.math.BigDecimal;

@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
public class StrategyState extends AuditableEntity {

    @Id
    private String symbol;

    @Enumerated(EnumType.STRING)
    private RegimeState regime;

    private BigDecimal highestCloseSinceEntry;
}
