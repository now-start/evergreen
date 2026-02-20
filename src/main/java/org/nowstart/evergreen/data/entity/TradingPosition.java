package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.nowstart.evergreen.data.type.PositionState;

import java.math.BigDecimal;

@Entity
@Table(name = "trading_positions")
@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class TradingPosition extends AuditableEntity {

    @Id
    private String symbol;

    private BigDecimal qty;

    private BigDecimal avgPrice;

    @Enumerated(EnumType.STRING)
    private PositionState state;
}
